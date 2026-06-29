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
});
