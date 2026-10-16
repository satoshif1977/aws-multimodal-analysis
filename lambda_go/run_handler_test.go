package main

import (
	"bytes"
	"context"
	"encoding/json"
	"log/slog"
	"strings"
	"testing"

	"github.com/aws/aws-lambda-go/events"
)

// ── テスト用ヘルパー ───────────────────────────────────────

// runTestHarness は runHandler をメモリ上の出力先で実行するための一式。
type runTestHarness struct {
	logger  *slog.Logger
	metrics *Metrics
	logBuf  *bytes.Buffer
	metBuf  *bytes.Buffer
}

func newRunTestHarness(t *testing.T) *runTestHarness {
	t.Helper()
	logBuf := &bytes.Buffer{}
	metBuf := &bytes.Buffer{}
	metrics, err := NewMetrics(MetricsOptions{Namespace: "Test/Multimodal", Sink: metBuf})
	if err != nil {
		t.Fatalf("NewMetrics が失敗しました: %v", err)
	}
	return &runTestHarness{
		logger:  NewLogger(LoggerOptions{Level: LogLevelDebug, Writer: logBuf}),
		metrics: metrics,
		logBuf:  logBuf,
		metBuf:  metBuf,
	}
}

// metricValues は Flush 後の EMF ドキュメントからメトリクス名と値を取り出す。
func (h *runTestHarness) metricValues(t *testing.T) map[string]float64 {
	t.Helper()
	if err := h.metrics.Flush(); err != nil {
		t.Fatalf("Flush が失敗しました: %v", err)
	}
	values := map[string]float64{}
	for _, line := range strings.Split(strings.TrimSpace(h.metBuf.String()), "\n") {
		if line == "" {
			continue
		}
		var doc map[string]any
		if err := json.Unmarshal([]byte(line), &doc); err != nil {
			t.Fatalf("EMF ドキュメントの解析に失敗しました: %v\n%s", err, line)
		}
		for k, v := range doc {
			if num, ok := v.(float64); ok {
				values[k] = num
			}
		}
	}
	return values
}

// logMessages はロガーが書き出した各行の message を返す。
func (h *runTestHarness) logMessages(t *testing.T) []string {
	t.Helper()
	var msgs []string
	for _, line := range strings.Split(strings.TrimSpace(h.logBuf.String()), "\n") {
		if line == "" {
			continue
		}
		var rec map[string]any
		if err := json.Unmarshal([]byte(line), &rec); err != nil {
			t.Fatalf("ログ行の解析に失敗しました: %v\n%s", err, line)
		}
		if msg, ok := rec["message"].(string); ok {
			msgs = append(msgs, msg)
		}
	}
	return msgs
}

// s3Event は検証のみで完結する S3 イベントを組み立てる。
// 検証で弾かれる入力しか渡さないため、AWS への通信は発生しない。
func s3Event(entries ...struct {
	Key  string
	Size int64
}) events.S3Event {
	ev := events.S3Event{}
	for _, e := range entries {
		rec := events.S3EventRecord{}
		rec.S3.Bucket.Name = "test-bucket"
		rec.S3.Object.Key = e.Key
		rec.S3.Object.Size = e.Size
		ev.Records = append(ev.Records, rec)
	}
	return ev
}

type entry = struct {
	Key  string
	Size int64
}

// ── validateRecord ─────────────────────────────────────────

func TestValidateRecord(t *testing.T) {
	const mb = 1024 * 1024
	tests := []struct {
		name       string
		key        string
		size       int64
		wantOK     bool
		wantReason string
	}{
		{"png は対象", "a.png", 1 * mb, true, ""},
		{"jpg は対象", "dir/b.jpg", 1 * mb, true, ""},
		{"pdf は対象", "c.pdf", 1 * mb, true, ""},
		{"txt は対象外", "d.txt", 1 * mb, false, "未対応の拡張子: .txt"},
		{"拡張子なしは対象外", "noext", 1 * mb, false, "未対応の拡張子: "},
		{"上限ちょうどは対象", "e.png", 5 * mb, true, ""},
		{"上限超過は対象外", "f.png", 6 * mb, false, "ファイルサイズ超過: 6.0MB（上限 5MB）"},
		{"サイズ 0 は対象", "g.png", 0, true, ""},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			ok, reason := validateRecord(tt.key, tt.size)
			if ok != tt.wantOK {
				t.Errorf("ok = %v, want %v（reason=%q）", ok, tt.wantOK, reason)
			}
			if reason != tt.wantReason {
				t.Errorf("reason = %q, want %q", reason, tt.wantReason)
			}
		})
	}
}

// ── recordCount ────────────────────────────────────────────

func TestRecordCountWithNilMetricsDoesNotPanic(t *testing.T) {
	logBuf := &bytes.Buffer{}
	logger := NewLogger(LoggerOptions{Writer: logBuf})
	recordCount(logger, nil, "Anything", 1)
	if logBuf.Len() != 0 {
		t.Errorf("メトリクス未設定時はログを出さない想定だが出力された: %s", logBuf.String())
	}
}

func TestRecordCountLogsWarningOnInvalidName(t *testing.T) {
	h := newRunTestHarness(t)
	recordCount(h.logger, h.metrics, "", 1)
	if !strings.Contains(h.logBuf.String(), "メトリクスの記録に失敗しました") {
		t.Errorf("警告ログが出ていません: %s", h.logBuf.String())
	}
}

// ── newMetricsFromEnv ──────────────────────────────────────

func TestNewMetricsFromEnvUsesDefaultNamespace(t *testing.T) {
	t.Setenv("METRICS_NAMESPACE", "")
	logBuf := &bytes.Buffer{}
	m := newMetricsFromEnv(NewLogger(LoggerOptions{Writer: logBuf}))
	if m == nil {
		t.Fatal("既定の名前空間で生成できるはずです")
	}
}

func TestNewMetricsFromEnvHonorsEnv(t *testing.T) {
	t.Setenv("METRICS_NAMESPACE", "  Custom/NS  ")
	logBuf := &bytes.Buffer{}
	if m := newMetricsFromEnv(NewLogger(LoggerOptions{Writer: logBuf})); m == nil {
		t.Fatal("環境変数の名前空間で生成できるはずです")
	}
}

// ── runHandler ─────────────────────────────────────────────

func TestRunHandlerEmptyEvent(t *testing.T) {
	h := newRunTestHarness(t)
	resp := runHandler(context.Background(), h.logger, h.metrics, events.S3Event{})

	if resp["statusCode"] != 200 {
		t.Errorf("statusCode = %v, want 200", resp["statusCode"])
	}
	body, ok := resp["body"].([]ProcessedRecord)
	if !ok || len(body) != 0 {
		t.Errorf("body = %v, want 空スライス", resp["body"])
	}

	values := h.metricValues(t)
	for name, want := range map[string]float64{
		"DocumentsReceived":  0,
		"DocumentsSucceeded": 0,
		"DocumentsSkipped":   0,
		"DocumentsFailed":    0,
	} {
		if got := values[name]; got != want {
			t.Errorf("%s = %v, want %v", name, got, want)
		}
	}
}

func TestRunHandlerSkipsUnsupportedExtension(t *testing.T) {
	h := newRunTestHarness(t)
	resp := runHandler(context.Background(), h.logger, h.metrics,
		s3Event(entry{Key: "memo.txt", Size: 1024}))

	body := resp["body"].([]ProcessedRecord)
	if len(body) != 1 {
		t.Fatalf("件数 = %d, want 1", len(body))
	}
	if body[0].Status != "skipped" {
		t.Errorf("Status = %q, want skipped", body[0].Status)
	}
	if !strings.Contains(body[0].Reason, "未対応の拡張子") {
		t.Errorf("Reason = %q", body[0].Reason)
	}

	values := h.metricValues(t)
	if values["DocumentsReceived"] != 1 {
		t.Errorf("DocumentsReceived = %v, want 1", values["DocumentsReceived"])
	}
	if values["DocumentsSkipped"] != 1 {
		t.Errorf("DocumentsSkipped = %v, want 1", values["DocumentsSkipped"])
	}
	if values["DocumentsSucceeded"] != 0 {
		t.Errorf("DocumentsSucceeded = %v, want 0", values["DocumentsSucceeded"])
	}
}

func TestRunHandlerSkipsOversizedFile(t *testing.T) {
	h := newRunTestHarness(t)
	resp := runHandler(context.Background(), h.logger, h.metrics,
		s3Event(entry{Key: "big.png", Size: 10 * 1024 * 1024}))

	body := resp["body"].([]ProcessedRecord)
	if body[0].Status != "skipped" {
		t.Errorf("Status = %q, want skipped", body[0].Status)
	}
	if !strings.Contains(body[0].Reason, "ファイルサイズ超過") {
		t.Errorf("Reason = %q", body[0].Reason)
	}
	if got := h.metricValues(t)["DocumentsSkipped"]; got != 1 {
		t.Errorf("DocumentsSkipped = %v, want 1", got)
	}
}

func TestRunHandlerCountsMultipleSkips(t *testing.T) {
	h := newRunTestHarness(t)
	resp := runHandler(context.Background(), h.logger, h.metrics, s3Event(
		entry{Key: "a.txt", Size: 1024},
		entry{Key: "b.docx", Size: 1024},
		entry{Key: "c.png", Size: 99 * 1024 * 1024},
	))

	body := resp["body"].([]ProcessedRecord)
	if len(body) != 3 {
		t.Fatalf("件数 = %d, want 3", len(body))
	}
	for i, r := range body {
		if r.Status != "skipped" {
			t.Errorf("body[%d].Status = %q, want skipped", i, r.Status)
		}
	}

	values := h.metricValues(t)
	if values["DocumentsReceived"] != 3 {
		t.Errorf("DocumentsReceived = %v, want 3", values["DocumentsReceived"])
	}
	if values["DocumentsSkipped"] != 3 {
		t.Errorf("DocumentsSkipped = %v, want 3", values["DocumentsSkipped"])
	}
	if values["DocumentsFailed"] != 0 {
		t.Errorf("DocumentsFailed = %v, want 0", values["DocumentsFailed"])
	}
}

func TestRunHandlerLogsStructuredContext(t *testing.T) {
	h := newRunTestHarness(t)
	runHandler(context.Background(), h.logger, h.metrics,
		s3Event(entry{Key: "memo.txt", Size: 1024}))

	msgs := strings.Join(h.logMessages(t), "\n")
	for _, want := range []string{
		"S3 イベントを受信しました",
		"処理を開始しました",
		"対象外のファイルのためスキップしました",
		"すべてのレコードを処理しました",
	} {
		if !strings.Contains(msgs, want) {
			t.Errorf("ログに %q がありません:\n%s", want, msgs)
		}
	}
	// bucket / key が構造化フィールドとして載っていること
	if !strings.Contains(h.logBuf.String(), `"key":"memo.txt"`) {
		t.Errorf("key が構造化フィールドに載っていません: %s", h.logBuf.String())
	}
}

func TestRunHandlerWorksWithoutMetrics(t *testing.T) {
	logBuf := &bytes.Buffer{}
	logger := NewLogger(LoggerOptions{Writer: logBuf})

	resp := runHandler(context.Background(), logger, nil,
		s3Event(entry{Key: "memo.txt", Size: 1024}))

	if resp["statusCode"] != 200 {
		t.Errorf("メトリクスが nil でも完走すべきです: %v", resp)
	}
}
