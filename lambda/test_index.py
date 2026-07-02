"""
aws-multimodal-analysis Lambda ユニットテスト
AWS 接続なしでファイル検証・プロンプト生成・解析・ハンドラーを検証する
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))
from index import (
    analyze_with_bedrock,
    build_prompt,
    handler,
    save_to_dynamodb,
    validate_file,
)


class TestValidateFile:
    def test_正常系_PNG(self):
        ok, msg = validate_file("invoice.png", 1 * 1024 * 1024)
        assert ok is True
        assert msg == ""

    def test_正常系_JPG(self):
        ok, msg = validate_file("doc.jpg", 500 * 1024)
        assert ok is True

    def test_異常系_未対応拡張子(self):
        ok, msg = validate_file("doc.exe", 100)
        assert ok is False
        assert "未対応" in msg

    def test_異常系_サイズ超過(self):
        ok, msg = validate_file("doc.png", 10 * 1024 * 1024)
        assert ok is False
        assert "超過" in msg

    def test_正常系_PDF(self):
        ok, msg = validate_file("report.pdf", 2 * 1024 * 1024)
        assert ok is True
        assert msg == ""

    def test_正常系_JPEG(self):
        ok, msg = validate_file("photo.jpeg", 1 * 1024 * 1024)
        assert ok is True

    def test_ちょうど上限5MBはOK(self):
        ok, _ = validate_file("doc.png", 5 * 1024 * 1024)
        assert ok is True


class TestBuildPrompt:
    def test_請求書キーワードで請求書プロンプト(self):
        prompt = build_prompt("invoice_2024.png")
        assert "請求書" in prompt

    def test_見積キーワードで見積書プロンプト(self):
        prompt = build_prompt("estimate_01.png")
        assert "見積" in prompt

    def test_不明なファイルで汎用プロンプト(self):
        prompt = build_prompt("unknown_doc.png")
        assert "業務文書" in prompt

    def test_日本語の請求書でもマッチする(self):
        prompt = build_prompt("請求書_202407.png")
        assert "請求書" in prompt

    def test_日本語の見積書でもマッチする(self):
        prompt = build_prompt("見積書_202407.png")
        assert "見積" in prompt


# ── analyze_with_bedrock テスト ────────────────────────────
class TestAnalyzeWithBedrock:
    @patch("index._bedrock_client")
    def test_正常系_JSON抽出できる(self, mock_bedrock):
        mock_bedrock.invoke_model.return_value = {
            "body": MagicMock(
                read=lambda: json.dumps(
                    {
                        "content": [
                            {
                                "text": '余分なテキスト {"document_type": "請求書", "total_amount": 10000} 以上'
                            }
                        ]
                    }
                ).encode()
            )
        }
        result = analyze_with_bedrock(b"fake_image_bytes", "invoice.png")
        assert result["document_type"] == "請求書"
        assert result["total_amount"] == 10000

    @patch("index._bedrock_client")
    def test_JSONパース失敗時はraw_textで保存(self, mock_bedrock):
        mock_bedrock.invoke_model.return_value = {
            "body": MagicMock(
                read=lambda: json.dumps(
                    {"content": [{"text": "JSONではない自由テキスト回答"}]}
                ).encode()
            )
        }
        result = analyze_with_bedrock(b"fake_image_bytes", "doc.png")
        assert "raw_text" in result
        assert "JSONではない" in result["raw_text"]

    @patch("index._bedrock_client")
    def test_ClientError時は例外を再送出(self, mock_bedrock):
        from botocore.exceptions import ClientError

        mock_bedrock.invoke_model.side_effect = ClientError(
            {"Error": {"Code": "ValidationException", "Message": ""}}, "InvokeModel"
        )
        try:
            analyze_with_bedrock(b"toobig", "doc.png")
            raise AssertionError("例外が発生するはず")
        except ClientError as e:
            assert e.response["Error"]["Code"] == "ValidationException"


# ── save_to_dynamodb テスト ────────────────────────────────
class TestSaveToDynamoDB:
    @patch("index._dynamodb")
    def test_正常系_put_itemが呼ばれる(self, mock_dynamo):
        mock_table = MagicMock()
        mock_dynamo.Table.return_value = mock_table
        mock_table.put_item.return_value = {}

        save_to_dynamodb(
            "bucket/key.png", "bucket", "key.png", {"document_type": "請求書"}
        )

        mock_table.put_item.assert_called_once()
        item = mock_table.put_item.call_args[1]["Item"]
        assert item["document_id"] == "bucket/key.png"
        assert item["status"] == "success"
        assert "expires_at" in item
        assert "analyzed_at" in item

    @patch("index._dynamodb")
    def test_s3_keyが保存されること(self, mock_dynamo):
        mock_table = MagicMock()
        mock_dynamo.Table.return_value = mock_table
        mock_table.put_item.return_value = {}

        save_to_dynamodb(
            "bucket/key.png", "bucket", "key.png", {"document_type": "請求書"}
        )

        item = mock_table.put_item.call_args[1]["Item"]
        assert item["s3_bucket"] == "bucket"
        assert item["s3_key"] == "key.png"
        assert item["result"]["document_type"] == "請求書"

    @patch("index._dynamodb")
    def test_DynamoDBエラー時は例外を再送出(self, mock_dynamo):
        from botocore.exceptions import ClientError

        mock_table = MagicMock()
        mock_dynamo.Table.return_value = mock_table
        mock_table.put_item.side_effect = ClientError(
            {
                "Error": {
                    "Code": "ProvisionedThroughputExceededException",
                    "Message": "",
                }
            },
            "PutItem",
        )
        try:
            save_to_dynamodb("b/k.png", "b", "k.png", {})
            raise AssertionError("例外が発生するはず")
        except ClientError:
            pass


# ── handler テスト ─────────────────────────────────────────
class TestHandler:
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
    def test_正常系_200を返す(self, mock_s3, mock_bedrock, mock_dynamo):
        mock_s3.get_object.return_value = {"Body": MagicMock(read=lambda: b"fake")}
        mock_bedrock.invoke_model.return_value = {
            "body": MagicMock(
                read=lambda: json.dumps(
                    {"content": [{"text": '{"document_type": "請求書"}'}]}
                ).encode()
            )
        }
        mock_dynamo.Table.return_value.put_item.return_value = {}

        result = handler(self._make_event(), MagicMock())
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["processed"][0]["status"] == "success"

    @patch("index._s3_client")
    def test_未対応拡張子はスキップ(self, mock_s3):
        event = self._make_event(key="doc.exe", size=100)
        result = handler(event, MagicMock())
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["processed"][0]["status"] == "skipped"
        mock_s3.get_object.assert_not_called()

    def test_レコードなしは空配列を返す(self):
        result = handler({"Records": []}, MagicMock())
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["processed"] == []

    def test_サイズ超過はスキップステータスを返す(self):
        event = self._make_event(key="big.png", size=10 * 1024 * 1024)
        result = handler(event, MagicMock())
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["processed"][0]["status"] == "skipped"

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
    def test_JPGファイルも処理できる(self, mock_s3, mock_bedrock, mock_dynamo):
        mock_s3.get_object.return_value = {"Body": MagicMock(read=lambda: b"fake")}
        mock_bedrock.invoke_model.return_value = {
            "body": MagicMock(
                read=lambda: json.dumps(
                    {"content": [{"text": '{"document_type": "報告書"}'}]}
                ).encode()
            )
        }
        mock_dynamo.Table.return_value.put_item.return_value = {}
        result = handler(self._make_event(key="report.jpg", size=100), MagicMock())
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["processed"][0]["status"] == "success"

    @patch("index._s3_client")
    def test_S3エラーはerrorステータスを返す(self, mock_s3):
        from botocore.exceptions import ClientError

        mock_s3.get_object.side_effect = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": ""}}, "GetObject"
        )
        result = handler(self._make_event(key="invoice.png", size=100), MagicMock())
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["processed"][0]["status"] == "error"

    @patch("index._s3_client")
    def test_予期しない例外もerrorステータスを返す(self, mock_s3):
        mock_s3.get_object.side_effect = RuntimeError("unexpected!")
        result = handler(self._make_event(key="invoice.png", size=100), MagicMock())
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["processed"][0]["status"] == "error"
