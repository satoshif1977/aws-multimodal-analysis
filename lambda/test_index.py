"""
aws-multimodal-analysis Lambda ユニットテスト
AWS 接続なしでファイル検証・プロンプト生成・ハンドラーを検証する
"""

import json
from unittest.mock import MagicMock, patch

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from index import validate_file, build_prompt, handler


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

    @patch("index.dynamodb")
    @patch("index.bedrock_client")
    @patch("index.s3_client")
    @patch.dict(
        "os.environ",
        {
            "BEDROCK_MODEL_ID": "anthropic.claude-3-haiku-20240307-v1:0",
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

    @patch("index.s3_client")
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
