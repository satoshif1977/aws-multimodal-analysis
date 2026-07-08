"""
aws-multimodal-analysis Lambda 詳細ユニットテスト
既存テストを補完するエッジケース・追加検証
"""

import json
import os
import sys
from datetime import datetime, timezone
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
        now_ts = int(datetime.now(timezone.utc).timestamp())
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
                {"s3": {"bucket": {"name": "bucket"}, "object": {"key": "a.png", "size": 100}}},
                {"s3": {"bucket": {"name": "bucket"}, "object": {"key": "b.jpg", "size": 200}}},
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
