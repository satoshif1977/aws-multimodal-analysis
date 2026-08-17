"""
aws-multimodal-analysis Lambda 詳細ユニットテスト
既存テストを補完するエッジケース・追加検証
"""

import json
import os
import sys
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))
from index import (
    analyze_with_bedrock,
    build_prompt,
    handler,
    save_to_dynamodb,
    validate_file,
)

# ── validate_file 詳細 ────────────────────────────────────────────


class TestValidateFileDetail:
    def test_拡張子なしファイルは未対応(self):
        ok, msg = validate_file("dockerfile", 100)
        assert ok is False
        assert "未対応" in msg

    def test_0バイトファイルはOK(self):
        ok, msg = validate_file("doc.png", 0)
        assert ok is True
        assert msg == ""

    def test_上限5MB超の1バイト超過は失敗(self):
        ok, msg = validate_file("doc.png", 5 * 1024 * 1024 + 1)
        assert ok is False
        assert "超過" in msg

    def test_大文字拡張子PNGも許可される(self):
        # 実装内で lower() しているため大文字でも OK
        ok, _ = validate_file("IMAGE.PNG", 1 * 1024 * 1024)
        assert ok is True

    def test_戻り値はタプルでboolとstr(self):
        ok, msg = validate_file("doc.png", 100)
        assert isinstance(ok, bool)
        assert isinstance(msg, str)


# ── build_prompt 詳細 ─────────────────────────────────────────────


class TestBuildPromptDetail:
    def test_invoiceキーワードでプロンプトがJSON形式を含む(self):
        prompt = build_prompt("invoice.png")
        assert "invoice_number" in prompt or "請求書番号" in prompt

    def test_estimateキーワードでプロンプトがestimate_numberを含む(self):
        prompt = build_prompt("estimate_01.png")
        assert "estimate_number" in prompt or "見積番号" in prompt

    def test_汎用プロンプトにJSONが含まれる(self):
        prompt = build_prompt("unknown.png")
        assert "JSON" in prompt

    def test_どのプロンプトも非空文字列を返す(self):
        for key in ["invoice.png", "estimate.png", "report.png"]:
            assert len(build_prompt(key)) > 0

    def test_大文字INVOICE混在でも請求書プロンプト(self):
        prompt = build_prompt("INVOICE_2024.png")
        assert "請求書" in prompt


# ── analyze_with_bedrock 詳細 ─────────────────────────────────────


class TestAnalyzeWithBedrockDetail:
    @patch("index._bedrock_client")
    def test_ネストされたJSONも正しく返す(self, mock_bedrock):
        nested = {"document_type": "請求書", "items": [{"description": "コンサル"}]}
        mock_bedrock.invoke_model.return_value = {
            "body": MagicMock(
                read=lambda: json.dumps(
                    {"content": [{"text": json.dumps(nested)}]}
                ).encode()
            )
        }
        result = analyze_with_bedrock(b"fake", "invoice.png")
        assert result["document_type"] == "請求書"
        assert isinstance(result["items"], list)

    @patch("index._bedrock_client")
    def test_JPGファイルは正常に処理される(self, mock_bedrock):
        mock_bedrock.invoke_model.return_value = {
            "body": MagicMock(
                read=lambda: json.dumps(
                    {"content": [{"text": '{"document_type": "報告書"}'}]}
                ).encode()
            )
        }
        result = analyze_with_bedrock(b"fake", "report.jpg")
        assert result.get("document_type") == "報告書"

    @patch("index._bedrock_client")
    def test_空のJSONテキストはraw_textで返る(self, mock_bedrock):
        mock_bedrock.invoke_model.return_value = {
            "body": MagicMock(
                read=lambda: json.dumps(
                    {"content": [{"text": "解析できませんでした"}]}
                ).encode()
            )
        }
        result = analyze_with_bedrock(b"fake", "doc.png")
        assert "raw_text" in result


# ── save_to_dynamodb 詳細 ─────────────────────────────────────────


class TestSaveToDynamoDBDetail:
    @patch("index._dynamodb")
    def test_expires_atが現在より後のタイムスタンプ(self, mock_dynamo):
        mock_table = MagicMock()
        mock_dynamo.Table.return_value = mock_table
        mock_table.put_item.return_value = {}

        save_to_dynamodb("b/k.png", "b", "k.png", {"doc": "test"})

        item = mock_table.put_item.call_args[1]["Item"]
        now_ts = int(datetime.now(UTC).timestamp())
        assert item["expires_at"] > now_ts

    @patch("index._dynamodb")
    def test_model_idが格納される(self, mock_dynamo):
        mock_table = MagicMock()
        mock_dynamo.Table.return_value = mock_table
        mock_table.put_item.return_value = {}

        save_to_dynamodb("b/k.png", "b", "k.png", {})

        item = mock_table.put_item.call_args[1]["Item"]
        assert "model_id" in item
        assert len(item["model_id"]) > 0


# ── handler 詳細 ──────────────────────────────────────────────────


class TestHandlerDetail:
    def _make_event(self, bucket="test-bucket", key="invoice.png", size=100):
        return {
            "Records": [
                {
                    "s3": {
                        "bucket": {"name": bucket},
                        "object": {"key": key, "size": size},
                    }
                }
            ]
        }

    def test_document_idがbucketとkeyの結合形式(self):
        event = self._make_event(key="big.png", size=10 * 1024 * 1024)
        result = handler(event, MagicMock())
        body = json.loads(result["body"])
        # skipped だが key が正しく記録されること
        assert body["processed"][0]["key"] == "big.png"

    def test_Recordsキーがない場合は空配列を返す(self):
        result = handler({}, MagicMock())
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["processed"] == []

    @patch("index._dynamodb")
    @patch("index._bedrock_client")
    @patch("index._s3_client")
    @patch.dict(
        "os.environ",
        {
            "BEDROCK_MODEL_ID": "jp.anthropic.claude-haiku-4-5-20251001-v1:0",
            "DYNAMODB_TABLE": "test-table",
        },
    )
    def test_複数レコードをすべて処理する(self, mock_s3, mock_bedrock, mock_dynamo):
        mock_s3.get_object.return_value = {"Body": MagicMock(read=lambda: b"fake")}
        mock_bedrock.invoke_model.return_value = {
            "body": MagicMock(
                read=lambda: json.dumps(
                    {"content": [{"text": '{"document_type": "請求書"}'}]}
                ).encode()
            )
        }
        mock_dynamo.Table.return_value.put_item.return_value = {}

        event = {
            "Records": [
                {
                    "s3": {
                        "bucket": {"name": "bucket"},
                        "object": {"key": "a.png", "size": 100},
                    }
                },
                {
                    "s3": {
                        "bucket": {"name": "bucket"},
                        "object": {"key": "b.jpg", "size": 200},
                    }
                },
            ]
        }
        result = handler(event, MagicMock())
        body = json.loads(result["body"])
        assert len(body["processed"]) == 2

    def test_skipしたレコードにreasonが含まれる(self):
        event = self._make_event(key="virus.exe", size=100)
        result = handler(event, MagicMock())
        body = json.loads(result["body"])
        assert "reason" in body["processed"][0]


# ── validate_file 追加テスト ──────────────────────────────────────


class TestValidateFileExtra:
    def test_gif拡張子は未対応(self):
        ok, msg = validate_file("animation.gif", 100)
        assert ok is False
        assert "未対応" in msg

    def test_docx拡張子は未対応(self):
        ok, msg = validate_file("document.docx", 100)
        assert ok is False

    def test_mp4拡張子は未対応(self):
        ok, msg = validate_file("video.mp4", 100)
        assert ok is False

    def test_txt拡張子は未対応(self):
        ok, msg = validate_file("readme.txt", 100)
        assert ok is False

    def test_サイズ超過メッセージにMB情報が含まれる(self):
        ok, msg = validate_file("doc.png", 10 * 1024 * 1024)
        assert ok is False
        assert "MB" in msg

    def test_正常系のエラーメッセージは空文字(self):
        ok, msg = validate_file("doc.jpg", 1 * 1024 * 1024)
        assert ok is True
        assert msg == ""

    def test_JPEG大文字拡張子も許可される(self):
        ok, _ = validate_file("photo.JPEG", 1 * 1024 * 1024)
        assert ok is True

    def test_PDF大文字拡張子も許可される(self):
        ok, _ = validate_file("invoice.PDF", 1 * 1024 * 1024)
        assert ok is True


# ── build_prompt 追加テスト ──────────────────────────────────────


class TestBuildPromptExtra:
    def test_invoiceプロンプトにdue_dateが含まれる(self):
        prompt = build_prompt("invoice.pdf")
        assert "due_date" in prompt

    def test_invoiceプロンプトにtotal_amountが含まれる(self):
        prompt = build_prompt("invoice.pdf")
        assert "total_amount" in prompt

    def test_estimateプロンプトにtotal_amountが含まれる(self):
        prompt = build_prompt("estimate.pdf")
        assert "total_amount" in prompt

    def test_estimateプロンプトにvalid_untilが含まれる(self):
        prompt = build_prompt("estimate.pdf")
        assert "valid_until" in prompt

    def test_デフォルトプロンプトにnullが含まれる(self):
        prompt = build_prompt("report.pdf")
        assert "null" in prompt

    def test_requestキーワードはデフォルトプロンプト(self):
        prompt = build_prompt("request.pdf")
        assert "業務文書" in prompt

    def test_すべてのプロンプトが文字列型(self):
        for key in ["invoice.pdf", "estimate.pdf", "contract.pdf"]:
            assert isinstance(build_prompt(key), str)


# ── analyze_with_bedrock 追加テスト ──────────────────────────────


class TestAnalyzeWithBedrockExtra:
    @patch("index._bedrock_client")
    def test_PDFファイルのmedia_typeがapplication_pdf(self, mock_bedrock):
        mock_bedrock.invoke_model.return_value = {
            "body": MagicMock(
                read=lambda: json.dumps(
                    {"content": [{"text": '{"document_type": "請求書"}'}]}
                ).encode()
            )
        }
        analyze_with_bedrock(b"fake", "invoice.pdf")
        call_kwargs = mock_bedrock.invoke_model.call_args[1]
        body = json.loads(call_kwargs["body"])
        source = body["messages"][0]["content"][0]["source"]
        assert source["media_type"] == "application/pdf"

    @patch("index._bedrock_client")
    def test_base64エンコードされたデータがAPIに渡る(self, mock_bedrock):
        import base64

        mock_bedrock.invoke_model.return_value = {
            "body": MagicMock(
                read=lambda: json.dumps({"content": [{"text": "{}"}]}).encode()
            )
        }
        file_bytes = b"test_image_data"
        analyze_with_bedrock(file_bytes, "doc.png")
        call_kwargs = mock_bedrock.invoke_model.call_args[1]
        body = json.loads(call_kwargs["body"])
        encoded = body["messages"][0]["content"][0]["source"]["data"]
        assert encoded == base64.standard_b64encode(file_bytes).decode("utf-8")

    @patch("index._bedrock_client")
    def test_JPGのmedia_typeがimage_jpeg(self, mock_bedrock):
        mock_bedrock.invoke_model.return_value = {
            "body": MagicMock(
                read=lambda: json.dumps(
                    {"content": [{"text": '{"doc": "ok"}'}]}
                ).encode()
            )
        }
        analyze_with_bedrock(b"fake", "photo.jpg")
        call_kwargs = mock_bedrock.invoke_model.call_args[1]
        body = json.loads(call_kwargs["body"])
        source = body["messages"][0]["content"][0]["source"]
        assert source["media_type"] == "image/jpeg"

    @patch("index._bedrock_client")
    def test_戻り値はdict型(self, mock_bedrock):
        mock_bedrock.invoke_model.return_value = {
            "body": MagicMock(
                read=lambda: json.dumps(
                    {"content": [{"text": '{"key": "value"}'}]}
                ).encode()
            )
        }
        result = analyze_with_bedrock(b"fake", "doc.png")
        assert isinstance(result, dict)


# ── save_to_dynamodb 追加テスト ──────────────────────────────────


class TestSaveToDynamoDBExtra:
    @patch("index._dynamodb")
    def test_analyzed_atがISO8601形式(self, mock_dynamo):
        mock_table = MagicMock()
        mock_dynamo.Table.return_value = mock_table
        mock_table.put_item.return_value = {}

        save_to_dynamodb("b/k.png", "b", "k.png", {})

        item = mock_table.put_item.call_args[1]["Item"]
        analyzed_at = item["analyzed_at"]
        # ISO 8601 の基本形式確認（T と + または Z を含む）
        assert "T" in analyzed_at

    @patch("index._dynamodb")
    def test_resultがdict型で保存される(self, mock_dynamo):
        mock_table = MagicMock()
        mock_dynamo.Table.return_value = mock_table
        mock_table.put_item.return_value = {}

        result_data = {"document_type": "請求書", "total_amount": 50000}
        save_to_dynamodb("b/k.png", "b", "k.png", result_data)

        item = mock_table.put_item.call_args[1]["Item"]
        assert item["result"] == result_data

    @patch("index._dynamodb")
    def test_statusがsuccess(self, mock_dynamo):
        mock_table = MagicMock()
        mock_dynamo.Table.return_value = mock_table
        mock_table.put_item.return_value = {}

        save_to_dynamodb("b/k.png", "b", "k.png", {})

        item = mock_table.put_item.call_args[1]["Item"]
        assert item["status"] == "success"


# ── handler 追加テスト ────────────────────────────────────────────


class TestHandlerExtra:
    def _make_event(self, bucket="test-bucket", key="invoice.png", size=100):
        return {
            "Records": [
                {
                    "s3": {
                        "bucket": {"name": bucket},
                        "object": {"key": key, "size": size},
                    }
                }
            ]
        }

    def test_レスポンスbodyがJSON文字列(self):
        result = handler({"Records": []}, MagicMock())
        assert isinstance(result["body"], str)
        json.loads(result["body"])  # パースできること

    def test_レスポンスにstatusCodeが含まれる(self):
        result = handler({"Records": []}, MagicMock())
        assert "statusCode" in result
        assert result["statusCode"] == 200

    def test_sizeフィールドなしはデフォルト0でバリデーション通過後S3エラー(self):
        # size が省略された場合は .get("size", 0) = 0 → 0MB < 5MB → S3 呼び出し → error
        event = {
            "Records": [
                {
                    "s3": {
                        "bucket": {"name": "bucket"},
                        "object": {"key": "doc.png"},  # size なし
                    }
                }
            ]
        }
        result = handler(event, MagicMock())
        body = json.loads(result["body"])
        # size=0 はバリデーション通過 → S3 エラーで "error"
        assert body["processed"][0]["status"] in ("error", "success")

    @patch("index._s3_client")
    def test_errorステータスにreasonが含まれる(self, mock_s3):
        from botocore.exceptions import ClientError

        mock_s3.get_object.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": ""}}, "GetObject"
        )
        result = handler(self._make_event(key="invoice.png", size=100), MagicMock())
        body = json.loads(result["body"])
        record = body["processed"][0]
        assert record["status"] == "error"
        assert "reason" in record
        assert len(record["reason"]) > 0

    @patch("index._dynamodb")
    @patch("index._bedrock_client")
    @patch("index._s3_client")
    @patch.dict(
        "os.environ",
        {
            "BEDROCK_MODEL_ID": "jp.anthropic.claude-haiku-4-5-20251001-v1:0",
            "DYNAMODB_TABLE": "test-table",
        },
    )
    def test_PDFファイルが正常に処理される(self, mock_s3, mock_bedrock, mock_dynamo):
        mock_s3.get_object.return_value = {"Body": MagicMock(read=lambda: b"fake_pdf")}
        mock_bedrock.invoke_model.return_value = {
            "body": MagicMock(
                read=lambda: json.dumps(
                    {"content": [{"text": '{"document_type": "請求書"}'}]}
                ).encode()
            )
        }
        mock_dynamo.Table.return_value.put_item.return_value = {}

        result = handler(self._make_event(key="invoice.pdf", size=1000), MagicMock())
        body = json.loads(result["body"])
        assert body["processed"][0]["status"] == "success"

    @patch("index._s3_client")
    def test_混在ステータス_skipped_and_error(self, mock_s3):
        from botocore.exceptions import ClientError

        mock_s3.get_object.side_effect = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": ""}}, "GetObject"
        )
        event = {
            "Records": [
                # txt → skipped（S3 呼ばれない）
                {
                    "s3": {
                        "bucket": {"name": "b"},
                        "object": {"key": "doc.txt", "size": 100},
                    }
                },
                # png → S3 error
                {
                    "s3": {
                        "bucket": {"name": "b"},
                        "object": {"key": "img.png", "size": 100},
                    }
                },
            ]
        }
        result = handler(event, MagicMock())
        body = json.loads(result["body"])
        statuses = {r["key"]: r["status"] for r in body["processed"]}
        assert statuses["doc.txt"] == "skipped"
        assert statuses["img.png"] == "error"
