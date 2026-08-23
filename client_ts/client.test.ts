import {
  validateFile,
  getDocumentType,
  buildPrompt,
  getMediaType,
  buildBedrockPayload,
  extractJsonFromText,
  buildDocumentId,
  buildDynamoDbItem,
} from "./client";

// ── validateFile ──────────────────────────────────────────
describe("validateFile", () => {
  it("should accept .png files", () => {
    const result = validateFile("test.png", 1024);
    expect(result.valid).toBe(true);
    expect(result.error).toBeUndefined();
  });

  it("should accept .jpg files", () => {
    expect(validateFile("test.jpg", 1024).valid).toBe(true);
  });

  it("should accept .jpeg files", () => {
    expect(validateFile("test.jpeg", 1024).valid).toBe(true);
  });

  it("should accept .pdf files", () => {
    expect(validateFile("test.pdf", 1024).valid).toBe(true);
  });

  it("should accept uppercase extension .PNG", () => {
    expect(validateFile("test.PNG", 1024).valid).toBe(true);
  });

  it("should reject unsupported extension .txt", () => {
    const result = validateFile("test.txt", 1024);
    expect(result.valid).toBe(false);
    expect(result.error).toContain(".txt");
  });

  it("should reject unsupported extension .docx", () => {
    const result = validateFile("test.docx", 1024);
    expect(result.valid).toBe(false);
    expect(result.error).toContain(".docx");
  });

  it("should reject file without extension", () => {
    const result = validateFile("testfile", 1024);
    expect(result.valid).toBe(false);
  });

  it("should reject file exceeding 5MB", () => {
    const sizeBytes = 6 * 1024 * 1024;
    const result = validateFile("test.png", sizeBytes);
    expect(result.valid).toBe(false);
    expect(result.error).toContain("超過");
  });

  it("should accept file exactly at 5MB", () => {
    const sizeBytes = 5 * 1024 * 1024;
    expect(validateFile("test.png", sizeBytes).valid).toBe(true);
  });

  it("should accept file just under 5MB", () => {
    const sizeBytes = 5 * 1024 * 1024 - 1;
    expect(validateFile("test.png", sizeBytes).valid).toBe(true);
  });

  it("should accept file with 0 bytes", () => {
    expect(validateFile("test.png", 0).valid).toBe(true);
  });

  it("should reject .gif extension", () => {
    const result = validateFile("animation.gif", 1024);
    expect(result.valid).toBe(false);
    expect(result.error).toContain(".gif");
  });
});

// ── getDocumentType ───────────────────────────────────────
describe("getDocumentType", () => {
  it('should return "invoice" for key containing "invoice"', () => {
    expect(getDocumentType("invoice_2024.png")).toBe("invoice");
  });

  it('should return "invoice" for key containing "請求"', () => {
    expect(getDocumentType("請求書_202401.pdf")).toBe("invoice");
  });

  it('should return "estimate" for key containing "estimate"', () => {
    expect(getDocumentType("estimate_001.png")).toBe("estimate");
  });

  it('should return "estimate" for key containing "見積"', () => {
    expect(getDocumentType("見積書.png")).toBe("estimate");
  });

  it('should return "generic" for unrecognized key', () => {
    expect(getDocumentType("document.pdf")).toBe("generic");
  });

  it("should be case-insensitive for English keywords", () => {
    expect(getDocumentType("INVOICE_001.png")).toBe("invoice");
    expect(getDocumentType("Estimate_001.png")).toBe("estimate");
  });
});

// ── buildPrompt ───────────────────────────────────────────
describe("buildPrompt", () => {
  it("should return invoice prompt for invoice key", () => {
    const prompt = buildPrompt("invoice_2024.png");
    expect(prompt).toContain("請求書");
    expect(prompt).toContain("invoice_number");
    expect(prompt).toContain("total_amount");
  });

  it("should return estimate prompt for estimate key", () => {
    const prompt = buildPrompt("estimate_001.png");
    expect(prompt).toContain("見積書");
    expect(prompt).toContain("estimate_number");
    expect(prompt).toContain("valid_until");
  });

  it("should return generic prompt for unknown key", () => {
    const prompt = buildPrompt("report.pdf");
    expect(prompt).toContain("業務文書");
    expect(prompt).toContain("JSON");
  });

  it("should include JSON format instruction in all prompts", () => {
    expect(buildPrompt("invoice.png")).toContain("JSON");
    expect(buildPrompt("estimate.png")).toContain("JSON");
    expect(buildPrompt("other.png")).toContain("JSON");
  });

  it("should return invoice prompt for Japanese 請求 keyword", () => {
    const prompt = buildPrompt("請求書_202401.pdf");
    expect(prompt).toContain("請求書");
    expect(prompt).toContain("invoice_number");
  });

  it("should return estimate prompt containing total_amount", () => {
    const prompt = buildPrompt("estimate_001.png");
    expect(prompt).toContain("total_amount");
  });
});

// ── getMediaType ──────────────────────────────────────────
describe("getMediaType", () => {
  it("should return image/jpeg for .jpg", () => {
    expect(getMediaType(".jpg")).toBe("image/jpeg");
  });

  it("should return image/jpeg for .jpeg", () => {
    expect(getMediaType(".jpeg")).toBe("image/jpeg");
  });

  it("should return image/png for .png", () => {
    expect(getMediaType(".png")).toBe("image/png");
  });

  it("should return application/pdf for .pdf", () => {
    expect(getMediaType(".pdf")).toBe("application/pdf");
  });

  it("should return image/jpeg as default for unknown extension", () => {
    expect(getMediaType(".bmp")).toBe("image/jpeg");
  });

  it("should be case-insensitive", () => {
    expect(getMediaType(".PNG")).toBe("image/png");
    expect(getMediaType(".JPG")).toBe("image/jpeg");
  });

  it("should return image/jpeg for .JPEG (uppercase)", () => {
    expect(getMediaType(".JPEG")).toBe("image/jpeg");
  });
});

// ── buildBedrockPayload ───────────────────────────────────
describe("buildBedrockPayload", () => {
  const sampleBase64 = "dGVzdA==";

  it("should build valid payload structure", () => {
    const payload = buildBedrockPayload(sampleBase64, "test.png");
    expect(payload.anthropic_version).toBe("bedrock-2023-05-31");
    expect(payload.max_tokens).toBe(1000);
    expect(payload.messages).toHaveLength(1);
    expect(payload.messages[0].role).toBe("user");
  });

  it("should include image content with correct media type", () => {
    const payload = buildBedrockPayload(sampleBase64, "document.png");
    const imageContent = payload.messages[0].content.find((c) => c.type === "image");
    expect(imageContent).toBeDefined();
    expect(imageContent?.source?.media_type).toBe("image/png");
    expect(imageContent?.source?.data).toBe(sampleBase64);
    expect(imageContent?.source?.type).toBe("base64");
  });

  it("should include text content with prompt", () => {
    const payload = buildBedrockPayload(sampleBase64, "invoice.png");
    const textContent = payload.messages[0].content.find((c) => c.type === "text");
    expect(textContent).toBeDefined();
    expect(textContent?.text).toContain("JSON");
  });

  it("should use PDF media type for .pdf files", () => {
    const payload = buildBedrockPayload(sampleBase64, "document.pdf");
    const imageContent = payload.messages[0].content.find((c) => c.type === "image");
    expect(imageContent?.source?.media_type).toBe("application/pdf");
  });

  it("should include exactly 2 content items (image + text)", () => {
    const payload = buildBedrockPayload(sampleBase64, "test.png");
    expect(payload.messages[0].content).toHaveLength(2);
  });
});

// ── extractJsonFromText ───────────────────────────────────
describe("extractJsonFromText", () => {
  it("should extract JSON object from surrounding text", () => {
    const text = 'Here is the result: {"key": "value"} End.';
    expect(extractJsonFromText(text)).toEqual({ key: "value" });
  });

  it("should extract JSON when it is the entire text", () => {
    const text = '{"document_type": "請求書", "total_amount": 10000}';
    expect(extractJsonFromText(text)).toEqual({ document_type: "請求書", total_amount: 10000 });
  });

  it("should return raw_text when no JSON found", () => {
    const text = "No JSON here";
    expect(extractJsonFromText(text)).toEqual({ raw_text: text });
  });

  it("should return raw_text for invalid JSON syntax", () => {
    const text = "{ invalid json }";
    expect(extractJsonFromText(text)).toEqual({ raw_text: text });
  });

  it("should extract nested JSON", () => {
    const text = 'Result: {"items": [{"name": "Item1", "amount": 100}]}';
    expect(extractJsonFromText(text)).toEqual({ items: [{ name: "Item1", amount: 100 }] });
  });

  it("should return raw_text for empty string", () => {
    expect(extractJsonFromText("")).toEqual({ raw_text: "" });
  });
});

// ── buildDocumentId ───────────────────────────────────────
describe("buildDocumentId", () => {
  it("should combine bucket and key with slash", () => {
    expect(buildDocumentId("my-bucket", "uploads/test.png")).toBe(
      "my-bucket/uploads/test.png",
    );
  });

  it("should handle keys without prefix", () => {
    expect(buildDocumentId("bucket", "file.pdf")).toBe("bucket/file.pdf");
  });
});

// ── buildDynamoDbItem ─────────────────────────────────────
describe("buildDynamoDbItem", () => {
  const fixedNow = new Date("2026-01-01T00:00:00.000Z");

  beforeEach(() => {
    jest.useFakeTimers();
    jest.setSystemTime(fixedNow);
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it("should build item with all required fields", () => {
    const result = { document_type: "請求書" };
    const item = buildDynamoDbItem("bucket/test.png", "bucket", "test.png", result);
    expect(item.document_id).toBe("bucket/test.png");
    expect(item.s3_bucket).toBe("bucket");
    expect(item.s3_key).toBe("test.png");
    expect(item.status).toBe("success");
    expect(item.result).toEqual(result);
  });

  it("should set analyzed_at to current ISO timestamp", () => {
    const item = buildDynamoDbItem("bucket/test.png", "bucket", "test.png", {});
    expect(item.analyzed_at).toBe("2026-01-01T00:00:00.000Z");
  });

  it("should set expires_at to 90 days from now", () => {
    const item = buildDynamoDbItem("bucket/test.png", "bucket", "test.png", {});
    const expectedExpiry = Math.floor(fixedNow.getTime() / 1000) + 90 * 24 * 60 * 60;
    expect(item.expires_at).toBe(expectedExpiry);
  });

  it("should use default model ID when not specified", () => {
    const item = buildDynamoDbItem("bucket/test.png", "bucket", "test.png", {});
    expect(item.model_id).toBe("jp.anthropic.claude-haiku-4-5-20251001-v1:0");
  });

  it("should use custom model ID when specified", () => {
    const item = buildDynamoDbItem(
      "bucket/test.png",
      "bucket",
      "test.png",
      {},
      "custom-model-id",
    );
    expect(item.model_id).toBe("custom-model-id");
  });

  it("should preserve complex nested result object as-is", () => {
    const result = {
      document_type: "請求書",
      total_amount: 100000,
      items: [{ name: "item1", amount: 50000 }],
    };
    const item = buildDynamoDbItem("b/k", "b", "k", result);
    expect(item.result).toEqual(result);
  });
});

// ── validateFile / 追加エッジケース ───────────────────────────

describe("validateFile / 追加エッジケース", () => {
  it("should reject .svg extension", () => {
    const result = validateFile("diagram.svg", 1024);
    expect(result.valid).toBe(false);
  });

  it("should reject .bmp extension", () => {
    const result = validateFile("image.bmp", 1024);
    expect(result.valid).toBe(false);
  });

  it("should reject .webp extension", () => {
    const result = validateFile("photo.webp", 1024);
    expect(result.valid).toBe(false);
  });

  it("should accept .JPEG uppercase", () => {
    expect(validateFile("photo.JPEG", 1024).valid).toBe(true);
  });

  it("should accept .PDF uppercase", () => {
    expect(validateFile("doc.PDF", 1024).valid).toBe(true);
  });

  it("should accept .JPG uppercase", () => {
    expect(validateFile("photo.JPG", 1024).valid).toBe(true);
  });

  it("should reject file just over 5MB", () => {
    const sizeBytes = 5 * 1024 * 1024 + 1;
    const result = validateFile("test.png", sizeBytes);
    expect(result.valid).toBe(false);
    expect(result.error).toContain("超過");
  });

  it("error message should include actual size", () => {
    const sizeBytes = 10 * 1024 * 1024;
    const result = validateFile("test.png", sizeBytes);
    expect(result.error).toContain("10.0MB");
  });

  it("error message should include max size", () => {
    const sizeBytes = 6 * 1024 * 1024;
    const result = validateFile("test.png", sizeBytes);
    expect(result.error).toContain("5MB");
  });

  it("should handle multi-dot filenames", () => {
    expect(validateFile("photo.2024.01.png", 1024).valid).toBe(true);
  });

  it("should handle path with directories", () => {
    expect(validateFile("uploads/images/test.jpg", 1024).valid).toBe(true);
  });

  it("should handle Japanese filename", () => {
    expect(validateFile("請求書_2024.pdf", 1024).valid).toBe(true);
  });
});

// ── getDocumentType / 追加パターン ────────────────────────────

describe("getDocumentType / 追加パターン", () => {
  it('should return "invoice" for "INVOICE" uppercase', () => {
    expect(getDocumentType("INVOICE_001.png")).toBe("invoice");
  });

  it('should return "invoice" for path containing invoice', () => {
    expect(getDocumentType("docs/invoices/2024/doc.pdf")).toBe("invoice");
  });

  it('should return "estimate" for path containing 見積', () => {
    expect(getDocumentType("見積もり/2024_01.pdf")).toBe("estimate");
  });

  it('should return "generic" for empty string', () => {
    expect(getDocumentType("")).toBe("generic");
  });

  it('should return "generic" for plain filename', () => {
    expect(getDocumentType("report_2024.pdf")).toBe("generic");
  });

  it('should prioritize invoice over estimate when both present', () => {
    expect(getDocumentType("invoice_estimate_combined.pdf")).toBe("invoice");
  });

  it('should return "invoice" for mixed case "Invoice"', () => {
    expect(getDocumentType("Invoice_Q4.pdf")).toBe("invoice");
  });

  it('should return "estimate" for "Estimate" mixed case', () => {
    expect(getDocumentType("Estimate_Draft.pdf")).toBe("estimate");
  });
});

// ── getMediaType / 追加パターン ───────────────────────────────

describe("getMediaType / 追加パターン", () => {
  it("should return image/jpeg for .PDF uppercase", () => {
    expect(getMediaType(".PDF")).toBe("application/pdf");
  });

  it("should return default for empty string", () => {
    expect(getMediaType("")).toBe("image/jpeg");
  });

  it("should return default for .gif", () => {
    expect(getMediaType(".gif")).toBe("image/jpeg");
  });

  it("should return default for .svg", () => {
    expect(getMediaType(".svg")).toBe("image/jpeg");
  });

  it("should return default for .webp", () => {
    expect(getMediaType(".webp")).toBe("image/jpeg");
  });

  it("should handle .Png mixed case", () => {
    expect(getMediaType(".Png")).toBe("image/png");
  });
});

// ── buildBedrockPayload / 追加パターン ────────────────────────

describe("buildBedrockPayload / 追加パターン", () => {
  it("should set correct media type for .jpeg files", () => {
    const payload = buildBedrockPayload("dGVzdA==", "photo.jpeg");
    const img = payload.messages[0].content.find((c) => c.type === "image");
    expect(img?.source?.media_type).toBe("image/jpeg");
  });

  it("should use invoice prompt for invoice files", () => {
    const payload = buildBedrockPayload("dGVzdA==", "invoice_2024.png");
    const text = payload.messages[0].content.find((c) => c.type === "text");
    expect(text?.text).toContain("請求書");
  });

  it("should use estimate prompt for estimate files", () => {
    const payload = buildBedrockPayload("dGVzdA==", "estimate_001.jpg");
    const text = payload.messages[0].content.find((c) => c.type === "text");
    expect(text?.text).toContain("見積書");
  });

  it("should use generic prompt for generic files", () => {
    const payload = buildBedrockPayload("dGVzdA==", "document.pdf");
    const text = payload.messages[0].content.find((c) => c.type === "text");
    expect(text?.text).toContain("業務文書");
  });

  it("should handle empty base64 string", () => {
    const payload = buildBedrockPayload("", "test.png");
    const img = payload.messages[0].content.find((c) => c.type === "image");
    expect(img?.source?.data).toBe("");
  });

  it("should handle file without extension", () => {
    const payload = buildBedrockPayload("dGVzdA==", "noext");
    const img = payload.messages[0].content.find((c) => c.type === "image");
    expect(img?.source?.media_type).toBe("image/jpeg");
  });
});

// ── extractJsonFromText / 追加パターン ────────────────────────

describe("extractJsonFromText / 追加パターン", () => {
  it("should extract JSON with multiple levels of nesting", () => {
    const text = '{"a": {"b": {"c": 1}}}';
    expect(extractJsonFromText(text)).toEqual({ a: { b: { c: 1 } } });
  });

  it("should handle JSON with arrays", () => {
    const text = '{"items": [1, 2, 3]}';
    expect(extractJsonFromText(text)).toEqual({ items: [1, 2, 3] });
  });

  it("should return raw_text for text with only opening brace", () => {
    expect(extractJsonFromText("{ no close")).toEqual({ raw_text: "{ no close" });
  });

  it("should return raw_text for text with only closing brace", () => {
    expect(extractJsonFromText("no open }")).toEqual({ raw_text: "no open }" });
  });

  it("should extract JSON surrounded by markdown code block", () => {
    const text = '```json\n{"key": "value"}\n```';
    expect(extractJsonFromText(text)).toEqual({ key: "value" });
  });

  it("should handle JSON with null values", () => {
    const text = '{"field": null}';
    expect(extractJsonFromText(text)).toEqual({ field: null });
  });

  it("should handle JSON with boolean values", () => {
    const text = '{"active": true, "deleted": false}';
    expect(extractJsonFromText(text)).toEqual({ active: true, deleted: false });
  });

  it("should handle JSON with Japanese strings", () => {
    const text = '{"名前": "テスト", "金額": 1000}';
    expect(extractJsonFromText(text)).toEqual({ 名前: "テスト", 金額: 1000 });
  });

  it("should return raw_text for text with braces in wrong order", () => {
    expect(extractJsonFromText("}wrong order{")).toEqual({ raw_text: "}wrong order{" });
  });
});

// ── buildDocumentId / 追加パターン ────────────────────────────

describe("buildDocumentId / 追加パターン", () => {
  it("should handle nested key paths", () => {
    expect(buildDocumentId("bucket", "a/b/c/file.png")).toBe("bucket/a/b/c/file.png");
  });

  it("should handle empty key", () => {
    expect(buildDocumentId("bucket", "")).toBe("bucket/");
  });

  it("should handle empty bucket", () => {
    expect(buildDocumentId("", "key.png")).toBe("/key.png");
  });

  it("should handle Japanese characters", () => {
    expect(buildDocumentId("my-bucket", "請求書/2024.pdf")).toBe("my-bucket/請求書/2024.pdf");
  });

  it("should handle special characters in key", () => {
    expect(buildDocumentId("bucket", "file with spaces.png")).toBe("bucket/file with spaces.png");
  });
});

// ── buildDynamoDbItem / 追加パターン ──────────────────────────

describe("buildDynamoDbItem / 追加パターン", () => {
  beforeEach(() => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date("2026-06-15T12:00:00.000Z"));
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it("should set status to success", () => {
    const item = buildDynamoDbItem("id", "b", "k", {});
    expect(item.status).toBe("success");
  });

  it("should set analyzed_at to current time", () => {
    const item = buildDynamoDbItem("id", "b", "k", {});
    expect(item.analyzed_at).toBe("2026-06-15T12:00:00.000Z");
  });

  it("expires_at should be greater than current timestamp", () => {
    const item = buildDynamoDbItem("id", "b", "k", {});
    const nowEpoch = Math.floor(new Date("2026-06-15T12:00:00.000Z").getTime() / 1000);
    expect(item.expires_at).toBeGreaterThan(nowEpoch);
  });

  it("should handle empty result object", () => {
    const item = buildDynamoDbItem("id", "b", "k", {});
    expect(item.result).toEqual({});
  });

  it("should handle result with raw_text", () => {
    const result = { raw_text: "could not parse" };
    const item = buildDynamoDbItem("id", "b", "k", result);
    expect(item.result).toEqual({ raw_text: "could not parse" });
  });

  it("should preserve all input parameters", () => {
    const item = buildDynamoDbItem("doc-123", "my-bucket", "uploads/file.png", { type: "invoice" }, "custom-model");
    expect(item.document_id).toBe("doc-123");
    expect(item.s3_bucket).toBe("my-bucket");
    expect(item.s3_key).toBe("uploads/file.png");
    expect(item.model_id).toBe("custom-model");
  });
});
