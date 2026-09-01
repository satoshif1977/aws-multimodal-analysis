"""
retry.py ユニットテスト

sleep / rand を注入することで、実際に待機せず決定的に検証する。
"""

import os
import sys
from dataclasses import FrozenInstanceError

import pytest
from botocore.exceptions import (
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ParamValidationError,
    ReadTimeoutError,
)

sys.path.insert(0, os.path.dirname(__file__))
from retry import (  # noqa: E402
    DEFAULT_CONFIG,
    RETRYABLE_ERROR_CODES,
    RETRYABLE_STATUS_CODES,
    RetryConfig,
    compute_delay,
    extract_error_code,
    extract_status_code,
    is_retryable,
    retry_call,
    with_retry,
)


# ── テスト用ヘルパー ───────────────────────────────────────
def make_client_error(code: str = "ThrottlingException", status: int = 429):
    """指定のエラーコード・ステータスを持つ ClientError を組み立てる"""
    return ClientError(
        {
            "Error": {"Code": code, "Message": f"{code} が発生しました"},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        "InvokeModel",
    )


class SleepRecorder:
    """sleep の呼び出し秒数を記録するスタブ"""

    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)

    @property
    def count(self) -> int:
        return len(self.calls)


class FlakyFunc:
    """指定回数だけ失敗し、その後成功する関数のスタブ"""

    def __init__(self, fail_times: int, exc: BaseException | None = None) -> None:
        self.fail_times = fail_times
        self.exc = exc or make_client_error()
        self.calls = 0
        self.received_args: tuple = ()
        self.received_kwargs: dict = {}

    def __call__(self, *args, **kwargs):
        self.calls += 1
        self.received_args = args
        self.received_kwargs = kwargs
        if self.calls <= self.fail_times:
            raise self.exc
        return "成功"


NO_JITTER = RetryConfig(max_attempts=4, base_delay=1.0, max_delay=100.0, jitter=False)


# ── RetryConfig ────────────────────────────────────────────
class TestRetryConfig:
    def test_デフォルト値(self):
        c = RetryConfig()
        assert c.max_attempts == 3
        assert c.base_delay == 0.5
        assert c.max_delay == 8.0
        assert c.jitter is True

    def test_DEFAULT_CONFIGはデフォルト値と一致(self):
        assert DEFAULT_CONFIG == RetryConfig()

    def test_イミュータブル(self):
        c = RetryConfig()
        with pytest.raises(FrozenInstanceError):
            c.max_attempts = 10  # type: ignore[misc]

    def test_max_attempts_1は許容(self):
        assert RetryConfig(max_attempts=1).max_attempts == 1

    def test_異常系_max_attempts_0(self):
        with pytest.raises(ValueError, match="max_attempts"):
            RetryConfig(max_attempts=0)

    def test_異常系_max_attempts_負数(self):
        with pytest.raises(ValueError, match="max_attempts"):
            RetryConfig(max_attempts=-1)

    def test_異常系_base_delay_0(self):
        with pytest.raises(ValueError, match="base_delay"):
            RetryConfig(base_delay=0)

    def test_異常系_base_delay_負数(self):
        with pytest.raises(ValueError, match="base_delay"):
            RetryConfig(base_delay=-0.5)

    def test_異常系_max_delayがbase_delay未満(self):
        with pytest.raises(ValueError, match="max_delay"):
            RetryConfig(base_delay=5.0, max_delay=1.0)

    def test_max_delayとbase_delayが同値なら許容(self):
        assert RetryConfig(base_delay=2.0, max_delay=2.0).max_delay == 2.0

    def test_等価比較(self):
        assert RetryConfig(max_attempts=5) == RetryConfig(max_attempts=5)
        assert RetryConfig(max_attempts=5) != RetryConfig(max_attempts=6)


# ── エラー情報の抽出 ───────────────────────────────────────
class TestExtractErrorCode:
    def test_ClientErrorからコードを取得(self):
        assert extract_error_code(make_client_error("ThrottlingException")) == (
            "ThrottlingException"
        )

    def test_ClientError以外は空文字(self):
        assert extract_error_code(ValueError("boom")) == ""

    def test_responseが辞書でない場合は空文字(self):
        exc = make_client_error()
        exc.response = "not-a-dict"  # type: ignore[assignment]
        assert extract_error_code(exc) == ""

    def test_Errorキーが辞書でない場合は空文字(self):
        exc = make_client_error()
        exc.response = {"Error": "broken"}  # type: ignore[assignment]
        assert extract_error_code(exc) == ""

    def test_Errorキーが無い場合は空文字(self):
        exc = make_client_error()
        exc.response = {"ResponseMetadata": {}}  # type: ignore[assignment]
        assert extract_error_code(exc) == ""

    def test_Codeが文字列でない場合は空文字(self):
        exc = make_client_error()
        exc.response = {"Error": {"Code": 500}}  # type: ignore[assignment]
        assert extract_error_code(exc) == ""


class TestExtractStatusCode:
    def test_ClientErrorからステータスを取得(self):
        assert extract_status_code(make_client_error(status=503)) == 503

    def test_ClientError以外はNone(self):
        assert extract_status_code(RuntimeError("boom")) is None

    def test_responseが辞書でない場合はNone(self):
        exc = make_client_error()
        exc.response = None  # type: ignore[assignment]
        assert extract_status_code(exc) is None

    def test_ResponseMetadataが無い場合はNone(self):
        exc = make_client_error()
        exc.response = {"Error": {"Code": "X"}}  # type: ignore[assignment]
        assert extract_status_code(exc) is None

    def test_HTTPStatusCodeが整数でない場合はNone(self):
        exc = make_client_error()
        exc.response = {  # type: ignore[assignment]
            "Error": {"Code": "X"},
            "ResponseMetadata": {"HTTPStatusCode": "503"},
        }
        assert extract_status_code(exc) is None


# ── リトライ可否の判定 ─────────────────────────────────────
class TestIsRetryable:
    @pytest.mark.parametrize("code", sorted(RETRYABLE_ERROR_CODES))
    def test_リトライ対象コードは全てTrue(self, code):
        # ステータスは非リトライ対象の 400 にして、コード単体の判定を確認する
        assert is_retryable(make_client_error(code, status=400)) is True

    @pytest.mark.parametrize("status", sorted(RETRYABLE_STATUS_CODES))
    def test_リトライ対象ステータスは全てTrue(self, status):
        assert is_retryable(make_client_error("UnknownError", status=status)) is True

    @pytest.mark.parametrize(
        "code",
        [
            "ValidationException",
            "AccessDeniedException",
            "ResourceNotFoundException",
            "InvalidSignatureException",
        ],
    )
    def test_リトライ不能なコードはFalse(self, code):
        assert is_retryable(make_client_error(code, status=400)) is False

    def test_400はリトライしない(self):
        assert is_retryable(make_client_error("Whatever", status=400)) is False

    def test_404はリトライしない(self):
        assert is_retryable(make_client_error("NotFound", status=404)) is False

    def test_ConnectTimeoutErrorはTrue(self):
        assert is_retryable(ConnectTimeoutError(endpoint_url="https://x")) is True

    def test_ReadTimeoutErrorはTrue(self):
        assert is_retryable(ReadTimeoutError(endpoint_url="https://x")) is True

    def test_EndpointConnectionErrorはTrue(self):
        assert is_retryable(EndpointConnectionError(endpoint_url="https://x")) is True

    def test_ParamValidationErrorはFalse(self):
        assert is_retryable(ParamValidationError(report="bad")) is False

    def test_一般例外はFalse(self):
        assert is_retryable(ValueError("boom")) is False

    def test_KeyErrorはFalse(self):
        assert is_retryable(KeyError("missing")) is False


# ── 待機秒数の計算 ─────────────────────────────────────────
class TestComputeDelay:
    def test_ジッター無効なら指数バックオフそのまま(self):
        assert compute_delay(1, NO_JITTER) == 1.0
        assert compute_delay(2, NO_JITTER) == 2.0
        assert compute_delay(3, NO_JITTER) == 4.0
        assert compute_delay(4, NO_JITTER) == 8.0

    def test_max_delayで頭打ちになる(self):
        c = RetryConfig(max_attempts=10, base_delay=1.0, max_delay=5.0, jitter=False)
        assert compute_delay(3, c) == 4.0
        assert compute_delay(4, c) == 5.0
        assert compute_delay(9, c) == 5.0

    def test_巨大なattemptでもオーバーフローしない(self):
        c = RetryConfig(max_attempts=1000, base_delay=1.0, max_delay=30.0, jitter=False)
        assert compute_delay(999, c) == 30.0

    def test_フルジッターは0から上限の範囲に収まる(self):
        c = RetryConfig(max_attempts=5, base_delay=1.0, max_delay=100.0)
        assert compute_delay(3, c, rand=lambda: 0.0) == 0.0
        assert compute_delay(3, c, rand=lambda: 1.0) == 4.0
        assert compute_delay(3, c, rand=lambda: 0.5) == 2.0

    def test_ジッターありでも常に上限以下(self):
        c = RetryConfig(max_attempts=8, base_delay=0.5, max_delay=8.0)
        for attempt in range(1, 9):
            for r in (0.0, 0.25, 0.5, 0.75, 1.0):
                assert 0.0 <= compute_delay(attempt, c, rand=lambda r=r: r) <= 8.0

    def test_base_delayが小さい場合(self):
        c = RetryConfig(base_delay=0.1, max_delay=10.0, jitter=False)
        assert compute_delay(1, c) == pytest.approx(0.1)
        assert compute_delay(4, c) == pytest.approx(0.8)

    def test_異常系_attempt_0(self):
        with pytest.raises(ValueError, match="attempt"):
            compute_delay(0, NO_JITTER)

    def test_異常系_attempt_負数(self):
        with pytest.raises(ValueError, match="attempt"):
            compute_delay(-3, NO_JITTER)

    def test_デフォルト設定でも計算できる(self):
        assert 0.0 <= compute_delay(1) <= DEFAULT_CONFIG.base_delay


# ── retry_call ─────────────────────────────────────────────
class TestRetryCall:
    def test_初回成功ならリトライしない(self):
        sleeper = SleepRecorder()
        func = FlakyFunc(fail_times=0)
        assert retry_call(func, config=NO_JITTER, sleep=sleeper) == "成功"
        assert func.calls == 1
        assert sleeper.count == 0

    def test_2回失敗後に成功(self):
        sleeper = SleepRecorder()
        func = FlakyFunc(fail_times=2)
        assert retry_call(func, config=NO_JITTER, sleep=sleeper) == "成功"
        assert func.calls == 3
        assert sleeper.calls == [1.0, 2.0]

    def test_試行回数を使い切ると元の例外を再送出(self):
        sleeper = SleepRecorder()
        func = FlakyFunc(fail_times=99)
        with pytest.raises(ClientError) as ei:
            retry_call(func, config=NO_JITTER, sleep=sleeper)
        assert ei.value.response["Error"]["Code"] == "ThrottlingException"
        assert func.calls == NO_JITTER.max_attempts
        assert sleeper.count == NO_JITTER.max_attempts - 1

    def test_リトライ不能な例外は即座に送出(self):
        sleeper = SleepRecorder()
        func = FlakyFunc(
            fail_times=99, exc=make_client_error("ValidationException", 400)
        )
        with pytest.raises(ClientError):
            retry_call(func, config=NO_JITTER, sleep=sleeper)
        assert func.calls == 1
        assert sleeper.count == 0

    def test_一般例外も即座に送出(self):
        func = FlakyFunc(fail_times=99, exc=ValueError("設定ミス"))
        with pytest.raises(ValueError, match="設定ミス"):
            retry_call(func, config=NO_JITTER, sleep=SleepRecorder())
        assert func.calls == 1

    def test_max_attempts_1ならリトライしない(self):
        c = RetryConfig(max_attempts=1, base_delay=1.0, max_delay=1.0, jitter=False)
        sleeper = SleepRecorder()
        func = FlakyFunc(fail_times=1)
        with pytest.raises(ClientError):
            retry_call(func, config=c, sleep=sleeper)
        assert func.calls == 1
        assert sleeper.count == 0

    def test_位置引数とキーワード引数が渡る(self):
        func = FlakyFunc(fail_times=0)
        retry_call(func, "a", "b", Bucket="my-bucket", config=NO_JITTER)
        assert func.received_args == ("a", "b")
        assert func.received_kwargs == {"Bucket": "my-bucket"}

    def test_configという名前のキーワードは予約されている(self):
        # config は retry_call 自身の引数として消費され、func には渡らない
        func = FlakyFunc(fail_times=0)
        retry_call(func, config=NO_JITTER)
        assert "config" not in func.received_kwargs

    def test_戻り値をそのまま返す(self):
        assert retry_call(lambda: {"key": [1, 2, 3]}, config=NO_JITTER) == {
            "key": [1, 2, 3]
        }

    def test_Noneを返す関数も扱える(self):
        assert retry_call(lambda: None, config=NO_JITTER) is None

    def test_on_retryコールバックが呼ばれる(self):
        events: list[tuple[int, float, str]] = []
        func = FlakyFunc(fail_times=2)
        retry_call(
            func,
            config=NO_JITTER,
            sleep=SleepRecorder(),
            on_retry=lambda a, d, e: events.append((a, d, type(e).__name__)),
        )
        assert [e[0] for e in events] == [1, 2]
        assert [e[1] for e in events] == [1.0, 2.0]
        assert all(e[2] == "ClientError" for e in events)

    def test_on_retryは成功時に呼ばれない(self):
        events: list = []
        retry_call(
            FlakyFunc(fail_times=0),
            config=NO_JITTER,
            on_retry=lambda a, d, e: events.append(a),
        )
        assert events == []

    def test_on_retryは最終失敗時には呼ばれない(self):
        # 3 回試行 = リトライは 2 回のみ
        c = RetryConfig(max_attempts=3, base_delay=1.0, max_delay=100.0, jitter=False)
        events: list = []
        with pytest.raises(ClientError):
            retry_call(
                FlakyFunc(fail_times=99),
                config=c,
                sleep=SleepRecorder(),
                on_retry=lambda a, d, e: events.append(a),
            )
        assert events == [1, 2]

    def test_ネットワーク例外もリトライされる(self):
        sleeper = SleepRecorder()
        func = FlakyFunc(
            fail_times=1, exc=ReadTimeoutError(endpoint_url="https://bedrock")
        )
        assert retry_call(func, config=NO_JITTER, sleep=sleeper) == "成功"
        assert func.calls == 2

    def test_randが待機秒数に反映される(self):
        c = RetryConfig(max_attempts=3, base_delay=2.0, max_delay=100.0)
        sleeper = SleepRecorder()
        retry_call(FlakyFunc(fail_times=2), config=c, sleep=sleeper, rand=lambda: 0.5)
        assert sleeper.calls == [1.0, 2.0]

    def test_待機秒数は単調増加する(self):
        sleeper = SleepRecorder()
        c = RetryConfig(max_attempts=5, base_delay=1.0, max_delay=100.0, jitter=False)
        with pytest.raises(ClientError):
            retry_call(FlakyFunc(fail_times=99), config=c, sleep=sleeper)
        assert sleeper.calls == sorted(sleeper.calls)
        # max_attempts=5 → リトライは 4 回（待機も 4 回）
        assert sleeper.calls == [1.0, 2.0, 4.0, 8.0]

    def test_デフォルト設定でも動作する(self):
        # 実際に待たないよう sleep をスタブ化する
        func = FlakyFunc(fail_times=1)
        assert retry_call(func, sleep=SleepRecorder(), rand=lambda: 0.0) == "成功"
        assert func.calls == 2


# ── with_retry デコレータ ──────────────────────────────────
class TestWithRetry:
    def test_デコレータでリトライされる(self):
        sleeper = SleepRecorder()
        state = {"calls": 0}

        @with_retry(config=NO_JITTER, sleep=sleeper)
        def flaky() -> str:
            state["calls"] += 1
            if state["calls"] < 3:
                raise make_client_error()
            return "ok"

        assert flaky() == "ok"
        assert state["calls"] == 3
        assert sleeper.count == 2

    def test_引数がそのまま渡る(self):
        @with_retry(config=NO_JITTER)
        def add(a: int, b: int = 0) -> int:
            return a + b

        assert add(1, b=2) == 3

    def test_メタデータが保持される(self):
        @with_retry(config=NO_JITTER)
        def documented() -> None:
            """説明文"""

        assert documented.__name__ == "documented"
        assert documented.__doc__ == "説明文"
        assert hasattr(documented, "__wrapped__")

    def test_リトライ不能な例外はそのまま送出(self):
        @with_retry(config=NO_JITTER, sleep=SleepRecorder())
        def broken() -> None:
            raise make_client_error("AccessDeniedException", 403)

        with pytest.raises(ClientError):
            broken()

    def test_デフォルト引数でデコレートできる(self):
        @with_retry()
        def fine() -> int:
            return 42

        assert fine() == 42
