package main

import (
	"context"
	"os"
	"strings"
	"testing"

	"github.com/aws/aws-lambda-go/events"
)

// ── getEnv ──────────────────────────────────────────────────

func TestGetEnv_WithValue(t *testing.T) {
	t.Setenv("TEST_KEY", "hello")
	if got := getEnv("TEST_KEY", "default"); got != "hello" {
		t.Errorf("got %q, want %q", got, "hello")
	}
}

func TestGetEnv_Fallback(t *testing.T) {
	os.Unsetenv("TEST_KEY_MISSING")
	if got := getEnv("TEST_KEY_MISSING", "fallback"); got != "fallback" {
		t.Errorf("got %q, want %q", got, "fallback")
	}
}

// ── isAllowedExt ────────────────────────────────────────────

func TestIsAllowedExt_PNG(t *testing.T) {
	if !isAllowedExt("document.png") {
		t.Error("png should be allowed")
	}
}

func TestIsAllowedExt_JPG(t *testing.T) {
	if !isAllowedExt("photo.jpg") {
		t.Error("jpg should be allowed")
	}
}

func TestIsAllowedExt_JPEG(t *testing.T) {
	if !isAllowedExt("photo.jpeg") {
		t.Error("jpeg should be allowed")
	}
}

func TestIsAllowedExt_PDF(t *testing.T) {
	if !isAllowedExt("invoice.pdf") {
		t.Error("pdf should be allowed")
	}
}

func TestIsAllowedExt_UpperCase(t *testing.T) {
	if !isAllowedExt("document.PNG") {
		t.Error("PNG (uppercase) should be allowed")
	}
}

func TestIsAllowedExt_TXT_NotAllowed(t *testing.T) {
	if isAllowedExt("readme.txt") {
		t.Error("txt should not be allowed")
	}
}

func TestIsAllowedExt_NoExtension(t *testing.T) {
	if isAllowedExt("filewithoutextension") {
		t.Error("no extension should not be allowed")
	}
}

// ── getExt ──────────────────────────────────────────────────

func TestGetExt_PDF(t *testing.T) {
	if got := getExt("invoice.pdf"); got != ".pdf" {
		t.Errorf("got %q, want %q", got, ".pdf")
	}
}

func TestGetExt_UpperCase(t *testing.T) {
	if got := getExt("image.PNG"); got != ".png" {
		t.Errorf("got %q, want %q", got, ".png")
	}
}

func TestGetExt_NoExtension(t *testing.T) {
	if got := getExt("noextension"); got != "" {
		t.Errorf("got %q, want %q", got, "")
	}
}

// ── mediaType ───────────────────────────────────────────────

func TestMediaType_JPG(t *testing.T) {
	if got := mediaType("photo.jpg"); got != "image/jpeg" {
		t.Errorf("got %q, want image/jpeg", got)
	}
}

func TestMediaType_JPEG(t *testing.T) {
	if got := mediaType("photo.jpeg"); got != "image/jpeg" {
		t.Errorf("got %q, want image/jpeg", got)
	}
}

func TestMediaType_PNG(t *testing.T) {
	if got := mediaType("image.png"); got != "image/png" {
		t.Errorf("got %q, want image/png", got)
	}
}

func TestMediaType_PDF(t *testing.T) {
	if got := mediaType("doc.pdf"); got != "application/pdf" {
		t.Errorf("got %q, want application/pdf", got)
	}
}

// ── buildPrompt ─────────────────────────────────────────────

func TestBuildPrompt_Invoice(t *testing.T) {
	prompt := buildPrompt("invoice_2026.png")
	if !strings.Contains(prompt, "請求書") {
		t.Error("invoice prompt should contain 請求書")
	}
}

func TestBuildPrompt_JapaneseInvoice(t *testing.T) {
	prompt := buildPrompt("請求書_202606.pdf")
	if !strings.Contains(prompt, "請求書") {
		t.Error("Japanese invoice prompt should contain 請求書")
	}
}

func TestBuildPrompt_Estimate(t *testing.T) {
	prompt := buildPrompt("estimate_project.pdf")
	if !strings.Contains(prompt, "見積書") {
		t.Error("estimate prompt should contain 見積書")
	}
}

func TestBuildPrompt_JapaneseEstimate(t *testing.T) {
	prompt := buildPrompt("見積書_202606.pdf")
	if !strings.Contains(prompt, "見積書") {
		t.Error("Japanese estimate prompt should contain 見積書")
	}
}

func TestBuildPrompt_Default(t *testing.T) {
	prompt := buildPrompt("general_document.pdf")
	if !strings.Contains(prompt, "業務文書") {
		t.Error("default prompt should contain 業務文書")
	}
}

// ── Handler（AWS 呼び出し前にスキップされるケース） ─────────

func TestHandler_EmptyEvent(t *testing.T) {
	result, err := Handler(context.Background(), events.S3Event{Records: []events.S3EventRecord{}})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result["statusCode"] != 200 {
		t.Errorf("got statusCode %v, want 200", result["statusCode"])
	}
}

func TestHandler_UnsupportedExtension(t *testing.T) {
	event := events.S3Event{
		Records: []events.S3EventRecord{
			{S3: events.S3Entity{
				Bucket: events.S3Bucket{Name: "test-bucket"},
				Object: events.S3Object{Key: "readme.txt", Size: 1024},
			}},
		},
	}
	result, err := Handler(context.Background(), event)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	records := result["body"].([]ProcessedRecord)
	if len(records) != 1 || records[0].Status != "skipped" {
		t.Errorf("expected 1 skipped record, got %+v", records)
	}
}

func TestHandler_FileTooLarge(t *testing.T) {
	const sixMB = 6 * 1024 * 1024
	event := events.S3Event{
		Records: []events.S3EventRecord{
			{S3: events.S3Entity{
				Bucket: events.S3Bucket{Name: "test-bucket"},
				Object: events.S3Object{Key: "large.png", Size: sixMB},
			}},
		},
	}
	result, err := Handler(context.Background(), event)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	records := result["body"].([]ProcessedRecord)
	if len(records) != 1 || records[0].Status != "skipped" {
		t.Errorf("expected 1 skipped record, got %+v", records)
	}
}
