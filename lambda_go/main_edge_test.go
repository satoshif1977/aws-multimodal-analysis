package main

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/aws/aws-lambda-go/events"
)

// ── 定数の値検証 ──────────────────────────────────────────────

func TestMaxFileSizeMB_Value(t *testing.T) {
	if maxFileSizeMB != 5 {
		t.Errorf("maxFileSizeMB = %d, want 5", maxFileSizeMB)
	}
}

func TestTTLDays_Value(t *testing.T) {
	if ttlDays != 90 {
		t.Errorf("ttlDays = %d, want 90", ttlDays)
	}
}

// ── isAllowedExt エッジケース ─────────────────────────────────

func TestIsAllowedExt_MP4_NotAllowed(t *testing.T) {
	if isAllowedExt("video.mp4") {
		t.Error("mp4 should not be allowed")
	}
}

func TestIsAllowedExt_CSV_NotAllowed(t *testing.T) {
	if isAllowedExt("data.csv") {
		t.Error("csv should not be allowed")
	}
}

func TestIsAllowedExt_SVG_NotAllowed(t *testing.T) {
	if isAllowedExt("image.svg") {
		t.Error("svg should not be allowed")
	}
}

func TestIsAllowedExt_DeepPath_PDF(t *testing.T) {
	if !isAllowedExt("dept/sales/2026/Q1/invoice.pdf") {
		t.Error("deep path pdf should be allowed")
	}
}

func TestIsAllowedExt_MixedCase_JPG(t *testing.T) {
	if !isAllowedExt("photo.JpG") {
		t.Error("mixed-case .JpG should be allowed (lowercased)")
	}
}

func TestIsAllowedExt_DotOnly(t *testing.T) {
	// "." のみのファイル名 → ext = "." → allowedExtensions に含まれないので false
	if isAllowedExt(".") {
		t.Error("dot-only filename should not be allowed")
	}
}

// ── getExt エッジケース ───────────────────────────────────────

func TestGetExt_JPEG(t *testing.T) {
	if got := getExt("photo.jpeg"); got != ".jpeg" {
		t.Errorf("got %q, want .jpeg", got)
	}
}

func TestGetExt_DeepSubdir(t *testing.T) {
	if got := getExt("a/b/c/invoice.pdf"); got != ".pdf" {
		t.Errorf("got %q, want .pdf", got)
	}
}

func TestGetExt_NoDotInPath(t *testing.T) {
	// パス全体にドットがない場合は空文字を返す
	if got := getExt("folderonly/filename"); got != "" {
		t.Errorf("file without dot anywhere: got %q, want empty", got)
	}
}

// ── mediaType エッジケース ────────────────────────────────────

func TestMediaType_UpperCasePDF(t *testing.T) {
	if got := mediaType("doc.PDF"); got != "application/pdf" {
		t.Errorf("got %q, want application/pdf", got)
	}
}

func TestMediaType_UpperCasePNG(t *testing.T) {
	if got := mediaType("img.PNG"); got != "image/png" {
		t.Errorf("got %q, want image/png", got)
	}
}

func TestMediaType_TXT_DefaultJPEG(t *testing.T) {
	// 未対応拡張子はデフォルト image/jpeg
	if got := mediaType("file.txt"); got != "image/jpeg" {
		t.Errorf("got %q, want image/jpeg", got)
	}
}

func TestMediaType_NoExtension_DefaultJPEG(t *testing.T) {
	if got := mediaType("noext"); got != "image/jpeg" {
		t.Errorf("got %q, want image/jpeg", got)
	}
}

// ── buildPrompt エッジケース ──────────────────────────────────

func TestBuildPrompt_Invoice_ContainsInvoiceNumber(t *testing.T) {
	prompt := buildPrompt("invoice.pdf")
	if !strings.Contains(prompt, "invoice_number") {
		t.Error("invoice prompt should contain 'invoice_number' field in JSON template")
	}
}

func TestBuildPrompt_Estimate_ContainsEstimateNumber(t *testing.T) {
	prompt := buildPrompt("estimate.pdf")
	if !strings.Contains(prompt, "estimate_number") {
		t.Error("estimate prompt should contain 'estimate_number' field")
	}
}

func TestBuildPrompt_Default_ContainsJSON(t *testing.T) {
	prompt := buildPrompt("general.pdf")
	if !strings.Contains(prompt, "JSON") {
		t.Error("default prompt should mention JSON形式")
	}
}

func TestBuildPrompt_SubdirInvoice(t *testing.T) {
	prompt := buildPrompt("invoices/2026/invoice_001.pdf")
	if !strings.Contains(prompt, "請求書") {
		t.Error("subdirectory invoice path should use invoice prompt")
	}
}

func TestBuildPrompt_EmptyKey_Default(t *testing.T) {
	prompt := buildPrompt("")
	if !strings.Contains(prompt, "業務文書") {
		t.Error("empty key should use default prompt with 業務文書")
	}
}

func TestBuildPrompt_Invoice_ContainsDueDate(t *testing.T) {
	prompt := buildPrompt("invoice.pdf")
	if !strings.Contains(prompt, "due_date") {
		t.Error("invoice prompt should contain due_date field")
	}
}

// ── BedrockBody / BedrockMessage エッジケース ─────────────────

func TestBedrockBody_MaxTokensPreserved(t *testing.T) {
	body := BedrockBody{MaxTokens: 1000}
	b, _ := json.Marshal(body)
	var got BedrockBody
	json.Unmarshal(b, &got)
	if got.MaxTokens != 1000 {
		t.Errorf("MaxTokens = %d, want 1000", got.MaxTokens)
	}
}

func TestBedrockBody_AnthropicVersionPreserved(t *testing.T) {
	body := BedrockBody{AnthropicVersion: "bedrock-2023-05-31"}
	b, _ := json.Marshal(body)
	var got BedrockBody
	json.Unmarshal(b, &got)
	if got.AnthropicVersion != "bedrock-2023-05-31" {
		t.Errorf("AnthropicVersion = %q", got.AnthropicVersion)
	}
}

func TestBedrockMessage_JSONRoundTrip(t *testing.T) {
	msg := BedrockMessage{
		Role: "user",
		Content: []map[string]interface{}{
			{"type": "text", "text": "テスト"},
		},
	}
	b, err := json.Marshal(msg)
	if err != nil {
		t.Fatalf("marshal error: %v", err)
	}
	var got BedrockMessage
	if err := json.Unmarshal(b, &got); err != nil {
		t.Fatalf("unmarshal error: %v", err)
	}
	if got.Role != "user" {
		t.Errorf("Role = %q, want user", got.Role)
	}
	if len(got.Content) != 1 {
		t.Errorf("Content len = %d, want 1", len(got.Content))
	}
}

func TestBedrockResponse_MultipleContent(t *testing.T) {
	raw := `{"content":[{"text":"first"},{"text":"second"}]}`
	var resp BedrockResponse
	json.Unmarshal([]byte(raw), &resp)
	if len(resp.Content) != 2 {
		t.Errorf("Content len = %d, want 2", len(resp.Content))
	}
	if resp.Content[1].Text != "second" {
		t.Errorf("Content[1].Text = %q, want second", resp.Content[1].Text)
	}
}

// ── ProcessedRecord エッジケース ──────────────────────────────

func TestProcessedRecord_ZeroValue(t *testing.T) {
	var rec ProcessedRecord
	if rec.Key != "" || rec.Status != "" || rec.Reason != "" {
		t.Error("zero value ProcessedRecord should have empty fields")
	}
}

func TestProcessedRecord_ErrorStatus(t *testing.T) {
	rec := ProcessedRecord{Key: "doc.pdf", Status: "error", Reason: "S3 取得エラー"}
	b, _ := json.Marshal(rec)
	var got ProcessedRecord
	json.Unmarshal(b, &got)
	if got.Status != "error" {
		t.Errorf("Status = %q, want error", got.Status)
	}
	if got.Reason != "S3 取得エラー" {
		t.Errorf("Reason = %q, want S3 取得エラー", got.Reason)
	}
}

func TestProcessedRecord_SkippedStatus(t *testing.T) {
	rec := ProcessedRecord{Key: "file.txt", Status: "skipped", Reason: "未対応の拡張子: .txt"}
	if rec.Status != "skipped" {
		t.Errorf("Status = %q, want skipped", rec.Status)
	}
}

// ── Handler エッジケース ──────────────────────────────────────

func TestHandler_EmptyKey_Skipped(t *testing.T) {
	// key="" → isAllowedExt("") = false → skipped
	event := events.S3Event{
		Records: []events.S3EventRecord{
			{S3: events.S3Entity{
				Bucket: events.S3Bucket{Name: "test-bucket"},
				Object: events.S3Object{Key: "", Size: 1024},
			}},
		},
	}
	result, err := Handler(context.Background(), event)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	records := result["body"].([]ProcessedRecord)
	if len(records) != 1 || records[0].Status != "skipped" {
		t.Errorf("empty key should be skipped: %+v", records)
	}
}

func TestHandler_ResultStatusCodeAlways200(t *testing.T) {
	// Handler は常に statusCode=200 を返す（個別エラーは body に含まれる）
	result, _ := Handler(context.Background(), events.S3Event{Records: []events.S3EventRecord{}})
	if result["statusCode"] != 200 {
		t.Errorf("statusCode = %v, want 200", result["statusCode"])
	}
}

func TestHandler_BodyIsProcessedRecordSlice(t *testing.T) {
	result, _ := Handler(context.Background(), events.S3Event{Records: []events.S3EventRecord{}})
	if _, ok := result["body"].([]ProcessedRecord); !ok {
		t.Error("body should be []ProcessedRecord")
	}
}

func TestHandler_MixedExtensions(t *testing.T) {
	// PNG（許可）と TXT（不許可）が混在 → TXT だけ skipped
	const fiveMB = 5 * 1024 * 1024
	event := events.S3Event{
		Records: []events.S3EventRecord{
			{S3: events.S3Entity{
				Bucket: events.S3Bucket{Name: "bucket"},
				Object: events.S3Object{Key: "img.png", Size: fiveMB + 1},
			}},
			{S3: events.S3Entity{
				Bucket: events.S3Bucket{Name: "bucket"},
				Object: events.S3Object{Key: "doc.txt", Size: 100},
			}},
		},
	}
	result, _ := Handler(context.Background(), event)
	records := result["body"].([]ProcessedRecord)
	if len(records) != 2 {
		t.Fatalf("expected 2 records, got %d", len(records))
	}
	// img.png は 5MB 超えで skipped、doc.txt は ext 不許可で skipped
	for _, r := range records {
		if r.Status != "skipped" && r.Status != "error" {
			t.Errorf("unexpected status %q for key %q", r.Status, r.Key)
		}
	}
}

// ── getEnv テーブル駆動 ───────────────────────────────────────

func TestGetEnv_TableDriven_Multi(t *testing.T) {
	cases := []struct {
		key      string
		set      bool
		val      string
		fallback string
		want     string
	}{
		{"MM_KEY_SET", true, "hello", "fb", "hello"},
		{"MM_KEY_EMPTY", true, "", "fb", "fb"},
		{"MM_KEY_NOSET", false, "", "default", "default"},
	}
	for _, tc := range cases {
		t.Run(tc.key, func(t *testing.T) {
			if tc.set {
				t.Setenv(tc.key, tc.val)
			}
			if got := getEnv(tc.key, tc.fallback); got != tc.want {
				t.Errorf("got %q, want %q", got, tc.want)
			}
		})
	}
}

// ── Benchmark ─────────────────────────────────────────────────

func BenchmarkIsAllowedExt(b *testing.B) {
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		isAllowedExt("invoices/2026/document.pdf")
	}
}

func BenchmarkBuildPrompt(b *testing.B) {
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		buildPrompt("invoice_2026.pdf")
	}
}

func BenchmarkGetExt(b *testing.B) {
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		getExt("invoices/2026/document.pdf")
	}
}

// ── isAllowedExt 追加テスト ───────────────────────────────

func TestIsAllowedExt_HiddenFile(t *testing.T) {
	// ".hidden" → ext = ".hidden" → allowedExtensions に含まれない
	if isAllowedExt(".hidden") {
		t.Error(".hidden should not be allowed")
	}
}

func TestIsAllowedExt_TarGz(t *testing.T) {
	// "archive.tar.gz" → ext = ".gz" → false
	if isAllowedExt("archive.tar.gz") {
		t.Error(".gz should not be allowed")
	}
}

func TestIsAllowedExt_Webp_NotAllowed(t *testing.T) {
	// デフォルトの allowedExtensions に .webp は含まれない
	if isAllowedExt("animation.webp") {
		t.Error(".webp should not be allowed with default extensions")
	}
}

func TestIsAllowedExt_ExeNotAllowed(t *testing.T) {
	if isAllowedExt("setup.exe") {
		t.Error(".exe should not be allowed")
	}
}

// ── getExt 追加テスト ─────────────────────────────────────

func TestGetExt_DotOnly(t *testing.T) {
	// "." → strings.LastIndex = 0 → "."[0:] = "." → lower = "."
	if got := getExt("."); got != "." {
		t.Errorf("got %q, want %q", got, ".")
	}
}

func TestGetExt_HiddenFile(t *testing.T) {
	// ".gitignore" → ext = ".gitignore"
	if got := getExt(".gitignore"); got != ".gitignore" {
		t.Errorf("got %q, want .gitignore", got)
	}
}

func TestGetExt_PNG(t *testing.T) {
	if got := getExt("image.png"); got != ".png" {
		t.Errorf("got %q, want .png", got)
	}
}

// ── buildPrompt 追加テスト ────────────────────────────────

func TestBuildPrompt_InvoiceContainsTotalAmount(t *testing.T) {
	prompt := buildPrompt("invoice.pdf")
	if !strings.Contains(prompt, "total_amount") {
		t.Error("invoice prompt should contain 'total_amount'")
	}
}

func TestBuildPrompt_EstimateContainsValidUntil(t *testing.T) {
	prompt := buildPrompt("estimate.pdf")
	if !strings.Contains(prompt, "valid_until") {
		t.Error("estimate prompt should contain 'valid_until'")
	}
}

func TestBuildPrompt_DefaultContainsNull(t *testing.T) {
	prompt := buildPrompt("general.pdf")
	if !strings.Contains(prompt, "null") {
		t.Error("default prompt should mention null for missing values")
	}
}

func TestBuildPrompt_ReportUsesDefault(t *testing.T) {
	prompt := buildPrompt("report_2026.pdf")
	if !strings.Contains(prompt, "業務文書") {
		t.Error("report key should use default prompt containing '業務文書'")
	}
}

func TestBuildPrompt_ContractUsesDefault(t *testing.T) {
	prompt := buildPrompt("contract.pdf")
	if !strings.Contains(prompt, "業務文書") {
		t.Error("contract key should use default prompt")
	}
}

// ── ProcessedRecord 追加テスト ────────────────────────────

func TestProcessedRecord_SuccessStatus_ReasonOmitted(t *testing.T) {
	rec := ProcessedRecord{Key: "invoice.pdf", Status: "success"}
	b, err := json.Marshal(rec)
	if err != nil {
		t.Fatalf("marshal error: %v", err)
	}
	if strings.Contains(string(b), "reason") {
		t.Error("reason should be omitted when empty (omitempty)")
	}
}

func TestProcessedRecord_KeyPreserved(t *testing.T) {
	rec := ProcessedRecord{Key: "invoices/2026/doc.pdf", Status: "skipped", Reason: "test"}
	b, _ := json.Marshal(rec)
	var got ProcessedRecord
	if err := json.Unmarshal(b, &got); err != nil {
		t.Fatalf("unmarshal error: %v", err)
	}
	if got.Key != "invoices/2026/doc.pdf" {
		t.Errorf("Key = %q, want invoices/2026/doc.pdf", got.Key)
	}
}

// ── Handler 追加テスト ────────────────────────────────────

func TestHandler_ZeroSizeFile_NotSkippedBySize(t *testing.T) {
	// size=0 は 0MB < 5MB でサイズチェック通過 → S3 取得エラーで "error" になる
	event := events.S3Event{
		Records: []events.S3EventRecord{
			{S3: events.S3Entity{
				Bucket: events.S3Bucket{Name: "test-bucket"},
				Object: events.S3Object{Key: "empty.png", Size: 0},
			}},
		},
	}
	result, err := Handler(context.Background(), event)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	records := result["body"].([]ProcessedRecord)
	if len(records) != 1 {
		t.Fatalf("expected 1 record, got %d", len(records))
	}
	// サイズ 0 は "skipped" にならない（S3エラーで "error"）
	if records[0].Status == "skipped" {
		t.Error("size=0 file should not be skipped by size check")
	}
}

func TestHandler_RecordCountMatchesInputCount(t *testing.T) {
	// 入力レコード数 = 出力レコード数（すべて skipped）
	event := events.S3Event{
		Records: []events.S3EventRecord{
			{S3: events.S3Entity{Bucket: events.S3Bucket{Name: "b"}, Object: events.S3Object{Key: "a.txt", Size: 100}}},
			{S3: events.S3Entity{Bucket: events.S3Bucket{Name: "b"}, Object: events.S3Object{Key: "b.csv", Size: 200}}},
			{S3: events.S3Entity{Bucket: events.S3Bucket{Name: "b"}, Object: events.S3Object{Key: "c.sh", Size: 50}}},
		},
	}
	result, _ := Handler(context.Background(), event)
	records := result["body"].([]ProcessedRecord)
	if len(records) != 3 {
		t.Errorf("expected 3 records, got %d", len(records))
	}
}

func TestHandler_SkippedRecordHasReason(t *testing.T) {
	// スキップされたレコードには Reason が設定される
	event := events.S3Event{
		Records: []events.S3EventRecord{
			{S3: events.S3Entity{
				Bucket: events.S3Bucket{Name: "test-bucket"},
				Object: events.S3Object{Key: "doc.txt", Size: 1024},
			}},
		},
	}
	result, _ := Handler(context.Background(), event)
	records := result["body"].([]ProcessedRecord)
	if len(records) != 1 {
		t.Fatalf("expected 1 record, got %d", len(records))
	}
	if records[0].Status != "skipped" {
		t.Errorf("expected skipped, got %q", records[0].Status)
	}
	if records[0].Reason == "" {
		t.Error("skipped record should have a non-empty Reason")
	}
}

// ── getEnv / 定数 追加テスト ──────────────────────────────

func TestGetEnv_SpaceValueNotFallback(t *testing.T) {
	// スペース文字は空文字でないため fallback しない
	t.Setenv("MM_SPACE_KEY", " ")
	if got := getEnv("MM_SPACE_KEY", "fallback"); got != " " {
		t.Errorf("space value should not fall back: got %q", got)
	}
}

func TestMaxFileSizeMB_IsPositive(t *testing.T) {
	if maxFileSizeMB <= 0 {
		t.Errorf("maxFileSizeMB should be positive, got %d", maxFileSizeMB)
	}
}

func TestTTLDays_IsPositive(t *testing.T) {
	if ttlDays <= 0 {
		t.Errorf("ttlDays should be positive, got %d", ttlDays)
	}
}
