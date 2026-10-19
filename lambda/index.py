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
  - 解析プロンプトの精度評価（抽出フィールドの充足率）を継続的に測る
"""

import base64
import json
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, os.path.dirname(__file__))
from logger import (  # noqa: E402
    StructuredLogger,
    create_logger_from_env,
    retry_logger,
)
from metrics import (  # noqa: E402
    MetricsCollector,
    create_metrics_from_env,
    retry_metrics,
)
from retry import RetryConfig, retry_call  # noqa: E402

# ── ロガー ─────────────────────────────────────────────────
# 1 行 JSON で出し、機密キーは自動でマスキングする（logger.py を参照）。
# S3 のキーやファイル名には顧客名が入りうるので、message に埋めず
# 必ずフィールドで渡すこと。
logger = create_logger_from_env()

# ── 定数 ──────────────────────────────────────────────────
BEDROCK_MODEL_ID = os.environ.get(
    "BEDROCK_MODEL_ID", "jp.anthropic.claude-haiku-4-5-20251001-v1:0"
)
DYNAMODB_TABLE = os.environ.get("DYNAMODB_TABLE", "multimodal-dev-results")
ALLOWED_EXTENSIONS = os.environ.get("ALLOWED_EXTENSIONS", ".png,.jpg,.jpeg,.pdf").split(
    ","
)
MAX_FILE_SIZE_MB = 5  # Bedrock の画像サイズ制限

# TTL: 解析結果の保持期間（90日）
TTL_DAYS = 90

# ── リトライ設定 ───────────────────────────────────────────
# Bedrock は同時実行が増えると ThrottlingException を返すため、
# 指数バックオフ + フルジッターで自動リトライする（retry.py を参照）
RETRY_CONFIG = RetryConfig(
    max_attempts=int(os.environ.get("RETRY_MAX_ATTEMPTS", "4")),
    base_delay=float(os.environ.get("RETRY_BASE_DELAY", "0.5")),
    max_delay=float(os.environ.get("RETRY_MAX_DELAY", "8.0")),
)


def retry_hooks(
    operation: str,
    log: StructuredLogger,
    metrics: MetricsCollector | None,
):
    """retry_call(on_retry=...) に渡すフックを1つにまとめる。

    logger.py の retry_logger() と metrics.py の retry_metrics() は
    どちらも (試行回数, 待機秒数, 例外) を受ける同じ形なので、まとめて呼べる。
    metrics が None のときはログだけに絞る。
    """
    hooks = [retry_logger(log, operation)]
    if metrics is not None:
        hooks.append(retry_metrics(metrics, operation))

    def on_retry(attempt: int, delay_seconds: float, exc: BaseException) -> None:
        for hook in hooks:
            hook(attempt, delay_seconds, exc)

    return on_retry


# ── AWS クライアント（モジュールレベルでウォームスタート時に再利用） ──
_s3_client = boto3.client("s3")
_bedrock_client = boto3.client("bedrock-runtime", region_name="ap-northeast-1")
_dynamodb = boto3.resource("dynamodb", region_name="ap-northeast-1")


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
        return (
            False,
            f"ファイルサイズ超過: {size_mb:.1f}MB（上限 {MAX_FILE_SIZE_MB}MB）",
        )

    return True, ""


# ── S3 からファイル取得 ────────────────────────────────────
def get_file_from_s3(
    bucket: str,
    key: str,
    *,
    log: StructuredLogger | None = None,
    metrics: MetricsCollector | None = None,
) -> bytes:
    """
    S3 からファイルをダウンロードしてバイト列で返す。

    SlowDown（S3 のスロットリング）に備えてリトライ付きで呼び出す。
    リトライが起きたらログとメトリクスの両方に残す。

    TODO: 大きなファイルはストリーミングで処理する
    """
    log = log or logger
    response = retry_call(
        _s3_client.get_object,
        Bucket=bucket,
        Key=key,
        config=RETRY_CONFIG,
        on_retry=retry_hooks("S3.GetObject", log, metrics),
    )
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
def analyze_with_bedrock(
    file_bytes: bytes,
    key: str,
    *,
    log: StructuredLogger | None = None,
    metrics: MetricsCollector | None = None,
) -> dict[str, Any]:
    """
    Bedrock（Claude マルチモーダル）でファイルを解析して結果を返す。

    ThrottlingException / ModelNotReadyException は retry.py の
    指数バックオフ + フルジッターで自動リトライする。

    TODO: PDF の場合は pymupdf などでページ画像に変換してから送る
    TODO: 応答の JSON バリデーションを追加する
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

    body = json.dumps(
        {
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
        }
    )

    log = log or logger
    response = retry_call(
        _bedrock_client.invoke_model,
        modelId=BEDROCK_MODEL_ID,
        body=body,
        contentType="application/json",
        accept="application/json",
        config=RETRY_CONFIG,
        on_retry=retry_hooks("Bedrock.InvokeModel", log, metrics),
    )

    result = json.loads(response["body"].read())
    raw_text = result["content"][0]["text"]

    # JSON 部分を抽出（Bedrock が余分なテキストを付けることがある）
    try:
        start = raw_text.index("{")
        end = raw_text.rindex("}") + 1
        extracted = json.loads(raw_text[start:end])
    except (ValueError, json.JSONDecodeError):
        # JSON パース失敗時はテキストとして保存する。
        # 無言で握りつぶすと「抽出できたのか、テキストのまま保存されたのか」が
        # 後から判別できないので、必ずログとメトリクスに残す。
        log.warn(
            "解析結果を JSON として読めませんでした。テキストのまま保存します",
            s3_key=key,
            raw_length=len(raw_text),
        )
        if metrics is not None:
            metrics.add_metric("AnalyzeJsonParseFailures", 1, unit="Count")
        extracted = {"raw_text": raw_text}

    return extracted


# ── DynamoDB に保存（サンプル実装） ───────────────────────
def save_to_dynamodb(
    document_id: str,
    bucket: str,
    key: str,
    result: dict[str, Any],
    *,
    log: StructuredLogger | None = None,
    metrics: MetricsCollector | None = None,
) -> None:
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
    table = _dynamodb.Table(DYNAMODB_TABLE)
    now = datetime.now(UTC)
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

    # ProvisionedThroughputExceededException に備えてリトライ付きで書き込む
    log = log or logger
    retry_call(
        table.put_item,
        Item=item,
        config=RETRY_CONFIG,
        on_retry=retry_hooks("DynamoDB.PutItem", log, metrics),
    )
    log.info(
        "DynamoDB へ保存しました",
        document_id=document_id,
        table=DYNAMODB_TABLE,
        expires_at=expires_at,
    )


# ── Lambda ハンドラー ──────────────────────────────────────
def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    S3 イベントを受け取り、ファイルを解析して DynamoDB に保存する。

    ログは 1 行 JSON（logger.py）、メトリクスは EMF（metrics.py）で出力する。
    S3 キーやファイル名は message に埋めず、フィールドで渡してマスキングを効かせる。

    TODO: 複数ファイルの並列処理（S3 イベントは複数レコードを含む場合がある）
    TODO: 解析失敗時に S3 の error/ プレフィックスにファイルを移動する
    """
    request_id = getattr(context, "aws_request_id", None)
    log = logger.child(request_id=request_id) if request_id else logger
    metrics = create_metrics_from_env()
    metrics.set_dimensions(
        FunctionName=os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "local")
    )

    records = event.get("Records", [])
    log.info("イベントを受信しました", record_count=len(records))
    metrics.add_metric("InvocationRecords", len(records), unit="Count")

    processed = []

    for record in records:
        bucket = record["s3"]["bucket"]["name"]
        key = record["s3"]["object"]["key"]
        size = record["s3"]["object"].get("size", 0)
        document_id = f"{bucket}/{key}"

        # s3_key はフィールドで渡す（message に埋めるとマスキングが効かない）
        rec_log = log.child(s3_bucket=bucket, s3_key=key)
        rec_log.info("処理を開始します", size_bytes=size)
        started = time.monotonic()

        # ── ファイル検証 ───────────────────────────────────
        ok, error_msg = validate_file(key, size)
        if not ok:
            rec_log.warn("ファイル検証に失敗しました", reason=error_msg)
            metrics.add_metric("FilesSkipped", 1, unit="Count")
            processed.append({"key": key, "status": "skipped", "reason": error_msg})
            continue

        try:
            # ── S3 からファイル取得 ────────────────────────
            file_bytes = get_file_from_s3(bucket, key, log=rec_log, metrics=metrics)
            rec_log.info("S3 から取得しました", bytes=len(file_bytes))
            metrics.add_metric("FileSizeBytes", len(file_bytes), unit="Bytes")

            # ── Bedrock で解析 ────────────────────────────
            result = analyze_with_bedrock(file_bytes, key, log=rec_log, metrics=metrics)
            rec_log.info("Bedrock で解析しました", fields=sorted(result.keys()))

            # ── DynamoDB に保存 ───────────────────────────
            save_to_dynamodb(
                document_id, bucket, key, result, log=rec_log, metrics=metrics
            )

            metrics.add_metric("FilesProcessed", 1, unit="Count")
            processed.append({"key": key, "status": "success"})

        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            rec_log.error(
                "AWS API がエラーを返しました", error_code=error_code, error=e
            )
            metrics.add_metric("FilesFailed", 1, unit="Count")
            metrics.set_property("last_error_code", error_code)
            processed.append({"key": key, "status": "error", "reason": error_code})

        except Exception as e:
            rec_log.error("予期しないエラーが発生しました", error=e)
            metrics.add_metric("FilesFailed", 1, unit="Count")
            processed.append({"key": key, "status": "error", "reason": str(e)})

        finally:
            metrics.add_metric(
                "ProcessingDuration",
                (time.monotonic() - started) * 1000,
                unit="Milliseconds",
            )

    # メトリクスは副次処理。ここで失敗しても本処理の結果は返す
    try:
        metrics.flush()
    except Exception as e:  # pragma: no cover - 想定外のみ
        log.warn("メトリクスの出力に失敗しました", error=e)

    return {
        "statusCode": 200,
        "body": json.dumps({"processed": processed}, ensure_ascii=False),
    }
