"""
index.py の結線を検証するテスト。

logger.py / metrics.py / retry.py は「置いてあるだけ」では意味がない。
このファイルは、それらが index.py から実際に呼ばれていることを確かめる。

確認する3点:
  1. ハンドラーのログが 1 行 JSON で出ていること
  2. S3 キーが message ではなくフィールドとして出ていること（マスキングが効く形）
  3. リトライが起きたとき、ログとメトリクスの両方に記録が残ること
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

sys.path.insert(0, os.path.dirname(__file__))

import index  # noqa: E402
from logger import create_logger  # noqa: E402
from metrics import create_metrics  # noqa: E402

# ── ヘルパー ───────────────────────────────────────────────


def s3_event(key: str = "invoice.png", size: int = 1024) -> dict[str, Any]:
    return {
        "Records": [
            {"s3": {"bucket": {"name": "docs"}, "object": {"key": key, "size": size}}}
        ]
    }


class _Ctx:
    aws_request_id = "req-0001"


def json_lines(captured: Any) -> list[dict[str, Any]]:
    """1 行 JSON だけを拾う。

    stdout_sink は warn / error を標準エラーへ分けて出すので、
    capsys の結果（out と err の両方）を渡せるようにしてある。
    """
    if hasattr(captured, "out"):
        text = captured.out + chr(10) + captured.err
    else:
        text = str(captured)
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def throttling() -> ClientError:
    return ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "slow down"}},
        "InvokeModel",
    )


# ── 1. ログが 1 行 JSON で出る ─────────────────────────────


class TestHandlerLogsAreStructured:
    def test_ハンドラーのログが1行JSONで出る(self, capsys, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "info")
        monkeypatch.setattr(index, "logger", create_logger(level="info"))

        index.handler({"Records": []}, _Ctx())

        entries = json_lines(capsys.readouterr())
        assert entries, "1 行 JSON が 1 件も出ていない"
        assert any(e.get("message") == "イベントを受信しました" for e in entries)

    def test_request_idが全ログに付く(self, capsys, monkeypatch):
        monkeypatch.setattr(index, "logger", create_logger(level="info"))

        index.handler({"Records": []}, _Ctx())

        entries = [e for e in json_lines(capsys.readouterr()) if "message" in e]
        assert entries
        assert all(e.get("request_id") == "req-0001" for e in entries)

    def test_contextがなくても落ちない(self, capsys, monkeypatch):
        monkeypatch.setattr(index, "logger", create_logger(level="info"))

        res = index.handler({"Records": []}, None)

        assert res["statusCode"] == 200


# ── 2. S3 キーはフィールドで出る（message に埋めない）──────


class TestS3KeyIsAField:
    def test_s3_keyがフィールドとして出る(self, capsys, monkeypatch):
        monkeypatch.setattr(index, "logger", create_logger(level="info"))

        index.handler(s3_event("請求書_山田商事.png", size=99 * 1024 * 1024), _Ctx())

        entries = json_lines(capsys.readouterr())
        started = [e for e in entries if e.get("message") == "処理を開始します"]
        assert started, "処理開始のログが出ていない"
        # フィールドに入っていること
        assert started[0]["s3_key"] == "請求書_山田商事.png"
        assert started[0]["s3_bucket"] == "docs"
        # message には埋め込まれていないこと（埋めるとマスキングが効かない）
        assert "山田商事" not in started[0]["message"]

    def test_検証NGもフィールドで理由を残す(self, capsys, monkeypatch):
        monkeypatch.setattr(index, "logger", create_logger(level="info"))

        index.handler(s3_event("memo.txt"), _Ctx())

        entries = json_lines(capsys.readouterr())
        ng = [e for e in entries if e.get("level") == "warn"]
        assert ng
        assert "reason" in ng[0]
        assert ng[0]["s3_key"] == "memo.txt"


# ── 3. リトライがログとメトリクスの両方に残る ──────────────


class TestRetryHooksAreWired:
    def test_retry_hooksはログとメトリクスの両方を呼ぶ(self):
        lines: list[str] = []
        emitted: list[str] = []
        log = create_logger(level="debug", sink=lambda line, level: lines.append(line))
        met = create_metrics("Test/NS", sink=emitted.append)

        index.retry_hooks("Bedrock.InvokeModel", log, met)(2, 0.25, throttling())

        # ログ側
        assert lines, "ログに出ていない"
        entry = json.loads(lines[0])
        assert entry["operation"] == "Bedrock.InvokeModel"
        assert entry["attempt"] == 2
        assert entry["level"] == "warn"

        # メトリクス側
        met.flush()
        doc = json.loads(emitted[0])
        assert doc["retry_operation"] == "Bedrock.InvokeModel"
        assert doc["RetryAttempts"] == 1
        assert "_aws" in doc

    def test_metricsがNoneでもログだけで動く(self):
        lines: list[str] = []
        log = create_logger(level="debug", sink=lambda line, level: lines.append(line))

        index.retry_hooks("S3.GetObject", log, None)(1, 0.1, throttling())

        assert len(lines) == 1
        assert json.loads(lines[0])["operation"] == "S3.GetObject"

    def test_S3取得のリトライが記録される(self):
        lines: list[str] = []
        emitted: list[str] = []
        log = create_logger(level="debug", sink=lambda line, level: lines.append(line))
        met = create_metrics("Test/NS", sink=emitted.append)

        body = MagicMock()
        body.read.return_value = b"ok"
        client = MagicMock()
        client.get_object.side_effect = [throttling(), {"Body": body}]

        with patch.object(index, "_s3_client", client), patch("time.sleep"):
            data = index.get_file_from_s3("docs", "a.png", log=log, metrics=met)

        assert data == b"ok"
        assert client.get_object.call_count == 2
        ops = [json.loads(x).get("operation") for x in lines]
        assert "S3.GetObject" in ops, "リトライのログが出ていない＝結線されていない"
        met.flush()
        assert json.loads(emitted[0])["RetryAttempts"] == 1

    def test_DynamoDB保存のリトライが記録される(self):
        lines: list[str] = []
        log = create_logger(level="debug", sink=lambda line, level: lines.append(line))

        table = MagicMock()
        table.put_item.side_effect = [throttling(), None]
        dynamodb = MagicMock()
        dynamodb.Table.return_value = table

        with patch.object(index, "_dynamodb", dynamodb), patch("time.sleep"):
            index.save_to_dynamodb("docs/a.png", "docs", "a.png", {"x": 1}, log=log)

        assert table.put_item.call_count == 2
        ops = [json.loads(x).get("operation") for x in lines]
        assert "DynamoDB.PutItem" in ops


# ── 4. EMF メトリクスが出る ────────────────────────────────


class TestMetricsAreEmitted:
    def test_ハンドラーがEMFを出力する(self, capsys, monkeypatch):
        monkeypatch.setattr(index, "logger", create_logger(level="info"))

        index.handler(s3_event("memo.txt"), _Ctx())

        docs = [d for d in json_lines(capsys.readouterr()) if "_aws" in d]
        assert docs, "EMF ドキュメントが出ていない＝metrics.py が結線されていない"
        doc = docs[0]
        assert doc["FilesSkipped"] == 1
        assert doc["InvocationRecords"] == 1
        # 検証で弾いた分は処理していないので ProcessingDuration は載せない
        # （0ms が混ざると処理時間の平均が歪むため）
        assert "ProcessingDuration" not in doc

    def test_成功時はFilesProcessedが立つ(self, capsys, monkeypatch):
        monkeypatch.setattr(index, "logger", create_logger(level="info"))
        monkeypatch.setattr(index, "get_file_from_s3", lambda *a, **k: b"xx")
        monkeypatch.setattr(index, "analyze_with_bedrock", lambda *a, **k: {"total": 1})
        monkeypatch.setattr(index, "save_to_dynamodb", lambda *a, **k: None)

        index.handler(s3_event(), _Ctx())

        docs = [d for d in json_lines(capsys.readouterr()) if "_aws" in d]
        assert docs
        assert docs[0]["FilesProcessed"] == 1
        assert docs[0]["FileSizeBytes"] == 2

    def test_失敗時はFilesFailedが立つ(self, capsys, monkeypatch):
        monkeypatch.setattr(index, "logger", create_logger(level="info"))

        def boom(*a: Any, **k: Any) -> bytes:
            raise throttling()

        monkeypatch.setattr(index, "get_file_from_s3", boom)

        res = index.handler(s3_event(), _Ctx())

        assert res["statusCode"] == 200
        docs = [d for d in json_lines(capsys.readouterr()) if "_aws" in d]
        assert docs
        assert docs[0]["FilesFailed"] == 1
        assert docs[0]["last_error_code"] == "ThrottlingException"

    def test_METRICS_ENABLED_falseで出力が止まる(self, capsys, monkeypatch):
        monkeypatch.setenv("METRICS_ENABLED", "false")
        monkeypatch.setattr(index, "logger", create_logger(level="info"))

        index.handler(s3_event("memo.txt"), _Ctx())

        docs = [d for d in json_lines(capsys.readouterr()) if "_aws" in d]
        assert not docs


# ── 5. JSON パース失敗が可視化される ───────────────────────


class TestJsonParseFailureIsVisible:
    def test_パース失敗がログとメトリクスに残る(self):
        lines: list[str] = []
        emitted: list[str] = []
        log = create_logger(level="debug", sink=lambda line, level: lines.append(line))
        met = create_metrics("Test/NS", sink=emitted.append)

        body = MagicMock()
        body.read.return_value = json.dumps(
            {"content": [{"text": "JSON ではない返答"}]}
        ).encode()
        client = MagicMock()
        client.invoke_model.return_value = {"body": body}

        with patch.object(index, "_bedrock_client", client):
            result = index.analyze_with_bedrock(b"x", "a.png", log=log, metrics=met)

        assert "raw_text" in result
        warns = [json.loads(x) for x in lines if json.loads(x)["level"] == "warn"]
        assert warns, "パース失敗が無言で握りつぶされている"
        assert warns[0]["s3_key"] == "a.png"
        met.flush()
        assert json.loads(emitted[0])["AnalyzeJsonParseFailures"] == 1


# ── 6. ログの失敗で本処理を止めない ────────────────────────


class TestMetricsFailureDoesNotBreakHandler:
    def test_flushが失敗してもレスポンスは返る(self, monkeypatch):
        monkeypatch.setattr(index, "logger", create_logger(level="silent"))

        broken = create_metrics("Test/NS")
        monkeypatch.setattr(
            broken, "flush", MagicMock(side_effect=RuntimeError("boom"))
        )
        monkeypatch.setattr(index, "create_metrics_from_env", lambda **k: broken)

        res = index.handler({"Records": []}, _Ctx())

        assert res["statusCode"] == 200


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
