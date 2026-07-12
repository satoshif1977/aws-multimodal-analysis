package main

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/aws/aws-lambda-go/events"
)

// ── buildPrompt 追加 ─────────────────────────────────────────────────────────

func TestBuildPrompt_InvoiceUpperCase(t *testing.T) {
	prompt := buildPrompt("INVOICE_2026.PNG")
	if !strings.Contains(prompt, "請求書") {
		t.Error("uppercase INVOICE prompt should contain 請求書")
	}
}

func TestBuildPrompt_EstimateUpperCase(t *testing.T) {
	prompt := buildPrompt("ESTIMATE_PROJECT.PDF")
	if !strings.Contains(prompt, "見積書") {
		t.Error("uppercase ESTIMATE prompt should contain 見積書")
	}
}

func TestBuildPrompt_ReturnsNonEmpty(t *testing.T) {
	if buildPrompt("") == "" {
		t.Error("buildPrompt should return non-empty string even for empty key")
	}
}

// ── isAllowedExt 追加 ────────────────────────────────────────────────────────

func TestIsAllowedExt_WithSubdirectory(t *testing.T) {
	if !isAllowedExt("invoices/2026/document.pdf") {
		t.Error("pdf with subdirectory path should be allowed")
	}
}

func TestIsAllowedExt_MultipleDots(t *testing.T) {
	if !isAllowedExt("file.backup.pdf") {
		t.Error("file with multiple dots ending in .pdf should be allowed")
	}
}

func TestIsAllowedExt_GIF_NotAllowed(t *testing.T) {
	if isAllowedExt("animation.gif") {
		t.Error("gif should not be allowed")
	}
}

// ── getExt 追加 ──────────────────────────────────────────────────────────────

func TestGetExt_JPG(t *testing.T) {
	if got := getExt("photo.jpg"); got != ".jpg" {
		t.Errorf("got %q, want %q", got, ".jpg")
	}
}

func TestGetExt_MultipleDots(t *testing.T) {
	if got := getExt("file.backup.pdf"); got != ".pdf" {
		t.Errorf("got %q, want %q", got, ".pdf")
	}
}

// ── mediaType 追加 ───────────────────────────────────────────────────────────

func TestMediaType_Default(t *testing.T) {
	// 未知の拡張子はデフォルト image/jpeg を返す
	if got := mediaType("document.xyz"); got != "image/jpeg" {
		t.Errorf("got %q, want image/jpeg", got)
	}
}

// ── BedrockBody / BedrockResponse JSON ──────────────────────────────────────

func TestBedrockBody_JSONMarshal(t *testing.T) {
	body := BedrockBody{
		AnthropicVersion: "bedrock-2023-05-31",
		MaxTokens:        1000,
		Messages: []BedrockMessage{
			{
				Role: "user",
				Content: []map[string]interface{}{
					{"type": "text", "text": "Hello"},
				},
			},
		},
	}
	data, err := json.Marshal(body)
	if err != nil {
		t.Fatalf("json.Marshal failed: %v", err)
	}
	s := string(data)
	if !strings.Contains(s, "bedrock-2023-05-31") {
		t.Error("marshaled JSON should contain anthropic_version")
	}
	if !strings.Contains(s, "1000") {
		t.Error("marshaled JSON should contain max_tokens")
	}
}

func TestBedrockResponse_JSONUnmarshal(t *testing.T) {
	raw := `{"content":[{"text":"Hello World"}]}`
	var resp BedrockResponse
	if err := json.Unmarshal([]byte(raw), &resp); err != nil {
		t.Fatalf("json.Unmarshal failed: %v", err)
	}
	if len(resp.Content) != 1 || resp.Content[0].Text != "Hello World" {
		t.Errorf("unexpected content: %+v", resp.Content)
	}
}

func TestBedrockResponse_EmptyContent(t *testing.T) {
	raw := `{"content":[]}`
	var resp BedrockResponse
	if err := json.Unmarshal([]byte(raw), &resp); err != nil {
		t.Fatalf("json.Unmarshal failed: %v", err)
	}
	if len(resp.Content) != 0 {
		t.Errorf("expected empty content, got %d items", len(resp.Content))
	}
}

// ── ProcessedRecord JSON ─────────────────────────────────────────────────────

func TestProcessedRecord_JSONMarshal_Success(t *testing.T) {
	rec := ProcessedRecord{Key: "invoice.pdf", Status: "success"}
	data, err := json.Marshal(rec)
	if err != nil {
		t.Fatalf("json.Marshal failed: %v", err)
	}
	s := string(data)
	if !strings.Contains(s, "success") {
		t.Error("marshaled JSON should contain status")
	}
	// reason は omitempty なので含まれないはず
	if strings.Contains(s, "reason") {
		t.Error("empty reason should be omitted from JSON (omitempty)")
	}
}

func TestProcessedRecord_JSONMarshal_WithReason(t *testing.T) {
	rec := ProcessedRecord{Key: "file.txt", Status: "skipped", Reason: "未対応の拡張子: .txt"}
	data, err := json.Marshal(rec)
	if err != nil {
		t.Fatalf("json.Marshal failed: %v", err)
	}
	if !strings.Contains(string(data), "reason") {
		t.Error("marshaled JSON should contain reason when set")
	}
}

// ── Handler 追加 ─────────────────────────────────────────────────────────────

func TestHandler_MultipleRecordsSkipped(t *testing.T) {
	event := events.S3Event{
		Records: []events.S3EventRecord{
			{S3: events.S3Entity{
				Bucket: events.S3Bucket{Name: "test-bucket"},
				Object: events.S3Object{Key: "readme.txt", Size: 1024},
			}},
			{S3: events.S3Entity{
				Bucket: events.S3Bucket{Name: "test-bucket"},
				Object: events.S3Object{Key: "script.sh", Size: 512},
			}},
		},
	}
	result, err := Handler(context.Background(), event)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	records := result["body"].([]ProcessedRecord)
	if len(records) != 2 {
		t.Errorf("expected 2 records, got %d", len(records))
	}
	for _, r := range records {
		if r.Status != "skipped" {
			t.Errorf("expected skipped status, got %q", r.Status)
		}
	}
}
