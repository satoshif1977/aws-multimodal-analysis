"""
aws-multimodal-analysis: 業務文書解析 Lambda

処理フロー:
  1. S3 イベントからファイル情報を取得
  2. ファイル種別チェック（拡張子・サイズ）
  3. S3 からファイルをダウンロード
  4. Bedrock（Claude マルチモーダル）で解析
  5. 解析結果を DynamoDB に保存 ← サンプル実装済み

TODO:
  - PDF の場合はページ分割して処理する
  - 解析プロンプトを文書種別（請求書/見積書/報告書）ごとに切り替える
  - 解析失敗時の S3 エラーフォルダへの移動処理
  - DLQ（Dead Letter Queue）で失敗イベントを保存する
  - 抽出精度の評価指標（抽出成功率）を CloudWatch カスタムメトリクスで記録する
"""

import base64
import json
import logging
import os
import uuid
from datetime import datetime, timezone, timedelta

import boto3
from botocore.exceptions import ClientError

# ── ロガー設定 ─────────────────────────────────────────────
logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# ── 定数 ──────────────────────────────────────────────────
BEDROCK_MODEL_ID = os.environ.get(
    "BEDROCK_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0"
)
DYNAMODB_TABLE = os.environ.get("DYNAMODB_TABLE", "multimodal-dev-results")
ALLOWED_EXTENSIONS = os.environ.get(
    "ALLOWED_EXTENSIONS", ".png,.jpg,.jpeg,.pdf"
).split(",")
MAX_FILE_SIZE_MB = 5  # Bedrock の画像サイズ制限

# TTL: 解析結果の保持期間（90日）
TTL_DAYS = 90

# ── AWS クライアント ───────────────────────────────────────
s3_client = boto3.client("s3")
bedrock_client = boto3.client("bedrock-runtime", region_name="ap-northeast-1")
dynamodb = boto3.resource("dynamodb")


# ── ファイル検証 ───────────────────────────────────────────
def validate_file(key: str, size_bytes: int) -> tuple[bool, str]:
    """
    ファイルの拡張子とサイズを検証する。
    戻り値: (OK かどうか, エラーメッセージ)

    TODO: MIME タイプによる検証も追加する（拡張子偽装対策）
    """
    ext = "." + key.rsplit(".", 1)[-1].lower() if "." in key else ""
    if ext not in ALLOWED_EXTENSIONS:
        return False, f"未対応の拡張子: {ext}"

    size_mb = size_bytes / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        return False, f"ファイルサイズ超過: {size_mb:.1f}MB（上限 {MAX_FILE_SIZE_MB}MB）"

    return True, ""


# ── S3 からファイル取得 ────────────────────────────────────
def get_file_from_s3(bucket: str, key: str) -> bytes:
    """
    S3 からファイルをダウンロードしてバイト列で返す。

    TODO: 大きなファイルはストリーミングで処理する
    """
    response = s3_client.get_object(Bucket=bucket, Key=key)
    return response["Body"].read()


# ── 解析プロンプト生成 ─────────────────────────────────────
def build_prompt(key: str) -> str:
    """
    ファイル名から文書種別を判定してプロンプトを生成する。

    TODO: 文書種別の判定ロジックを充実させる
    TODO: 本番では文書種別ごとに抽出フィールドを定義する
    """
    key_lower = key.lower()

    if "invoice" in key_lower or "請求" in key_lower:
        return """この請求書画像から以下の情報を JSON 形式で抽出してください。
該当する情報がない場合は null としてください。

{
  "document_type": "請求書",
  "invoice_number": "請求書番号",
  "issue_date": "発行日（YYYY-MM-DD）",
  "due_date": "支払期日（YYYY-MM-DD）",
  "vendor_name": "請求元会社名",
  "total_amount": 請求金額（数値）,
  "currency": "通貨（JPY/USD等）",
  "items": [
    {"description": "品目", "quantity": 数量, "unit_price": 単価, "amount": 金額}
  ]
}"""

    elif "estimate" in key_lower or "見積" in key_lower:
        return """この見積書画像から以下の情報を JSON 形式で抽出してください。
{
  "document_type": "見積書",
  "estimate_number": "見積番号",
  "issue_date": "発行日（YYYY-MM-DD）",
  "valid_until": "有効期限（YYYY-MM-DD）",
  "vendor_name": "見積元会社名",
  "total_amount": 見積金額（数値）,
  "items": [
    {"description": "品目", "quantity": 数量, "unit_price": 単価, "amount": 金額}
  ]
}"""

    else:
        # 汎用プロンプト
        return """この業務文書から重要な情報を JSON 形式で抽出してください。
文書の種類、日付、金額、関係者名、主要な項目を含めてください。
情報が読み取れない場合は null としてください。"""


# ── Bedrock でファイル解析 ─────────────────────────────────
def analyze_with_bedrock(file_bytes: bytes, key: str) -> dict:
    """
    Bedrock（Claude マルチモーダル）でファイルを解析して結果を返す。

    TODO: PDF の場合は pymupdf などでページ画像に変換してから送る
    TODO: 応答の JSON バリデーションを追加する
    TODO: リトライ処理を追加する（ThrottlingException 対策）
    """
    ext = "." + key.rsplit(".", 1)[-1].lower()

    # メディアタイプの決定
    media_type_map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".pdf": "application/pdf",
    }
    media_type = media_type_map.get(ext, "image/jpeg")

    # Base64 エンコード
    file_b64 = base64.standard_b64encode(file_bytes).decode("utf-8")
    prompt = build_prompt(key)

    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 1000,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": file_b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    })

    response = bedrock_client.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        body=body,
        contentType="application/json",
        accept="application/json",
    )

    result = json.loads(response["body"].read())
    raw_text = result["content"][0]["text"]

    # JSON 部分を抽出（Bedrock が余分なテキストを付けることがある）
    try:
        start = raw_text.index("{")
        end = raw_text.rindex("}") + 1
        extracted = json.loads(raw_text[start:end])
    except (ValueError, json.JSONDecodeError):
        # JSON パース失敗時はテキストとして保存
        extracted = {"raw_text": raw_text}

    return extracted


# ── DynamoDB に保存（サンプル実装） ───────────────────────
def save_to_dynamodb(document_id: str, bucket: str, key: str, result: dict) -> None:
    """
    解析結果を DynamoDB に保存する。

    テーブル設計:
      - document_id (PK): S3 バケット名 + オブジェクトキー
      - analyzed_at (SK): 解析日時（ISO 8601）
      - expires_at: TTL（90日後の Unix タイム）
      - result: 解析結果（JSON）
      - status: success / error

    TODO: 同一ファイルの再解析時に上書きするか別レコードにするか設計する
    TODO: 解析失敗時も status=error でレコードを残してトラッキングする
    """
    table = dynamodb.Table(DYNAMODB_TABLE)
    now = datetime.now(timezone.utc)
    expires_at = int((now + timedelta(days=TTL_DAYS)).timestamp())

    item = {
        "document_id": document_id,
        "analyzed_at": now.isoformat(),
        "expires_at": expires_at,
        "s3_bucket": bucket,
        "s3_key": key,
        "model_id": BEDROCK_MODEL_ID,
        "status": "success",
        "result": result,
    }

    table.put_item(Item=item)
    logger.info(f"DynamoDB 保存完了: document_id={document_id}")


# ── Lambda ハンドラー ──────────────────────────────────────
def handler(event: dict, context) -> dict:
    """
    S3 イベントを受け取り、ファイルを解析して DynamoDB に保存する。

    TODO: 複数ファイルの並列処理（S3 イベントは複数レコードを含む場合がある）
    TODO: 解析失敗時に S3 の error/ プレフィックスにファイルを移動する
    TODO: CloudWatch カスタムメトリクスで抽出成功率を記録する
    """
    logger.info(f"イベント受信: {json.dumps(event)[:200]}")
    processed = []

    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = record["s3"]["object"]["key"]
        size = record["s3"]["object"].get("size", 0)
        document_id = f"{bucket}/{key}"

        logger.info(f"処理開始: {document_id}")

        # ── ファイル検証 ───────────────────────────────────
        ok, error_msg = validate_file(key, size)
        if not ok:
            logger.warning(f"ファイル検証NG: {error_msg}")
            processed.append({"key": key, "status": "skipped", "reason": error_msg})
            continue

        try:
            # ── S3 からファイル取得 ────────────────────────
            file_bytes = get_file_from_s3(bucket, key)
            logger.info(f"S3 取得完了: {len(file_bytes)} bytes")

            # ── Bedrock で解析 ────────────────────────────
            result = analyze_with_bedrock(file_bytes, key)
            logger.info(f"Bedrock 解析完了: {list(result.keys())}")

            # ── DynamoDB に保存 ───────────────────────────
            save_to_dynamodb(document_id, bucket, key, result)

            processed.append({"key": key, "status": "success"})

        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            logger.error(f"AWS エラー: {error_code} / {key} / {e}")
            processed.append({"key": key, "status": "error", "reason": error_code})

        except Exception as e:
            logger.error(f"予期しないエラー: {key} / {e}", exc_info=True)
            processed.append({"key": key, "status": "error", "reason": str(e)})

    return {
        "statusCode": 200,
        "body": json.dumps({"processed": processed}, ensure_ascii=False),
    }
