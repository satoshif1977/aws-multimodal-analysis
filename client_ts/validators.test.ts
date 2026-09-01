import {
  // 定数
  BUCKET_NAME_PATTERN,
  VALID_ANTHROPIC_VERSIONS,
  MIN_MAX_TOKENS,
  MAX_MAX_TOKENS,
  VALID_DOCUMENT_TYPES,
  VALID_DB_STATUSES,
  INVOICE_FIELDS,
  ESTIMATE_FIELDS,
  VALID_CONTENT_TYPES,
  VALID_MEDIA_TYPES,
  // S3
  isValidBucketName,
  isNonEmptyKey,
  validateS3Record,
  validateS3Event,
  // Bedrock
  validateBedrockContent,
  validateBedrockPayload,
  // DynamoDB
  validateDynamoDbItem,
  // 文書種別
  isValidDocumentType,
  validateDocumentTypeConsistency,
  // 解析結果
  validateInvoiceResult,
  validateEstimateResult,
  validateAnalysisResult,
  // ユーティリティ
  hasErrors,
  formatErrors,
  ValidationError,
} from "./validators";

import type {
  S3Event,
  S3EventRecord,
  BedrockPayload,
  DynamoDbItem,
} from "./types";

// ── テストヘルパー ────────────────────────────────────────────

function errorsOnly(errors: ValidationError[]): ValidationError[] {
  return errors.filter((e) => e.severity === "error");
}

function warningsOnly(errors: ValidationError[]): ValidationError[] {
  return errors.filter((e) => e.severity === "warning");
}

function makeRecord(
  bucket: string,
  key: string,
  size: number
): S3EventRecord {
  return { s3: { bucket: { name: bucket }, object: { key, size } } };
}

function makePayload(overrides?: Partial<BedrockPayload>): BedrockPayload {
  return {
    anthropic_version: "bedrock-2023-05-31",
    max_tokens: 1000,
    messages: [
      {
        role: "user",
        content: [
          {
            type: "image",
            source: { type: "base64", media_type: "image/jpeg", data: "AAAA" },
          },
          { type: "text", text: "この画像を解析してください" },
        ],
      },
    ],
    ...overrides,
  };
}

function makeDbItem(overrides?: Partial<DynamoDbItem>): DynamoDbItem {
  return {
    document_id: "my-bucket/invoice.jpg",
    analyzed_at: "2026-09-01T00:00:00.000Z",
    expires_at: Math.floor(Date.now() / 1000) + 86400,
    s3_bucket: "my-bucket",
    s3_key: "invoice.jpg",
    model_id: "jp.anthropic.claude-haiku-4-5-20251001-v1:0",
    status: "success",
    result: { document_type: "請求書" },
    ...overrides,
  };
}

// ── 定数 ─────────────────────────────────────────────────────

describe("定数", () => {
  test("VALID_ANTHROPIC_VERSIONS は bedrock-2023-05-31 を含む", () => {
    expect(VALID_ANTHROPIC_VERSIONS).toContain("bedrock-2023-05-31");
  });

  test("VALID_DOCUMENT_TYPES は 3 種類", () => {
    expect(VALID_DOCUMENT_TYPES).toHaveLength(3);
  });

  test("VALID_DB_STATUSES は success と error", () => {
    expect(VALID_DB_STATUSES).toContain("success");
    expect(VALID_DB_STATUSES).toContain("error");
  });

  test("INVOICE_FIELDS は 5 項目", () => {
    expect(INVOICE_FIELDS).toHaveLength(5);
    expect(INVOICE_FIELDS).toContain("total_amount");
  });

  test("ESTIMATE_FIELDS は 5 項目", () => {
    expect(ESTIMATE_FIELDS).toHaveLength(5);
    expect(ESTIMATE_FIELDS).toContain("estimate_number");
  });

  test("MAX_MAX_TOKENS は 8192", () => {
    expect(MAX_MAX_TOKENS).toBe(8192);
  });
});

// ── isValidBucketName ────────────────────────────────────────

describe("isValidBucketName", () => {
  test("通常のバケット名は有効", () => {
    expect(isValidBucketName("my-bucket")).toBe(true);
  });

  test("ドット付きバケット名は有効", () => {
    expect(isValidBucketName("my.bucket.name")).toBe(true);
  });

  test("数字で始まるバケット名は有効", () => {
    expect(isValidBucketName("123-bucket")).toBe(true);
  });

  test("大文字は無効", () => {
    expect(isValidBucketName("My-Bucket")).toBe(false);
  });

  test("2文字以下は無効", () => {
    expect(isValidBucketName("ab")).toBe(false);
  });

  test("空文字は無効", () => {
    expect(isValidBucketName("")).toBe(false);
  });
});

// ── validateS3Record ─────────────────────────────────────────

describe("validateS3Record", () => {
  test("正常なレコードはエラーなし", () => {
    expect(
      validateS3Record(makeRecord("my-bucket", "file.jpg", 1024), 0)
    ).toHaveLength(0);
  });

  test("バケット名が空は error", () => {
    const record = makeRecord("", "file.jpg", 1024);
    const result = errorsOnly(validateS3Record(record, 0));
    expect(result.length).toBeGreaterThan(0);
  });

  test("無効なバケット名は error", () => {
    const record = makeRecord("INVALID", "file.jpg", 1024);
    const result = errorsOnly(validateS3Record(record, 0));
    expect(result.some((e) => e.field.includes("bucket"))).toBe(true);
  });

  test("キーが空は error", () => {
    const record = makeRecord("my-bucket", "", 1024);
    const result = errorsOnly(validateS3Record(record, 0));
    expect(result.some((e) => e.field.includes("key"))).toBe(true);
  });

  test("負のサイズは error", () => {
    const record = makeRecord("my-bucket", "file.jpg", -1);
    const result = errorsOnly(validateS3Record(record, 0));
    expect(result.some((e) => e.field.includes("size"))).toBe(true);
  });
});

// ── validateS3Event ──────────────────────────────────────────

describe("validateS3Event", () => {
  test("正常なイベントはエラーなし", () => {
    const event: S3Event = {
      Records: [makeRecord("my-bucket", "invoice.jpg", 2048)],
    };
    expect(validateS3Event(event)).toHaveLength(0);
  });

  test("Records 未定義は error", () => {
    const result = errorsOnly(validateS3Event({} as S3Event));
    expect(result.some((e) => e.field === "Records")).toBe(true);
  });

  test("Records 空配列は error", () => {
    const result = errorsOnly(validateS3Event({ Records: [] }));
    expect(result.some((e) => e.field === "Records")).toBe(true);
  });

  test("複数レコードのエラーが個別に報告される", () => {
    const event: S3Event = {
      Records: [
        makeRecord("my-bucket", "", 100),
        makeRecord("", "file.jpg", 100),
      ],
    };
    const result = errorsOnly(validateS3Event(event));
    expect(result.some((e) => e.field.includes("Records[0]"))).toBe(true);
    expect(result.some((e) => e.field.includes("Records[1]"))).toBe(true);
  });
});

// ── validateBedrockContent ───────────────────────────────────

describe("validateBedrockContent", () => {
  test("正常な image content はエラーなし", () => {
    const content = {
      type: "image" as const,
      source: { type: "base64" as const, media_type: "image/jpeg", data: "AAAA" },
    };
    expect(validateBedrockContent(content, 0)).toHaveLength(0);
  });

  test("正常な text content はエラーなし", () => {
    const content = { type: "text" as const, text: "テスト" };
    expect(validateBedrockContent(content, 0)).toHaveLength(0);
  });

  test("無効な content type は error", () => {
    const content = { type: "video" as any };
    const result = errorsOnly(validateBedrockContent(content, 0));
    expect(result.some((e) => e.field.includes("type"))).toBe(true);
  });

  test("image に source なしは error", () => {
    const content = { type: "image" as const };
    const result = errorsOnly(validateBedrockContent(content, 0));
    expect(result.some((e) => e.field.includes("source"))).toBe(true);
  });

  test("無効な media_type は error", () => {
    const content = {
      type: "image" as const,
      source: { type: "base64" as const, media_type: "image/bmp", data: "AA" },
    };
    const result = errorsOnly(validateBedrockContent(content, 0));
    expect(result.some((e) => e.field.includes("media_type"))).toBe(true);
  });

  test("data が空は error", () => {
    const content = {
      type: "image" as const,
      source: { type: "base64" as const, media_type: "image/jpeg", data: "" },
    };
    const result = errorsOnly(validateBedrockContent(content, 0));
    expect(result.some((e) => e.field.includes("data"))).toBe(true);
  });

  test("text が空は error", () => {
    const content = { type: "text" as const, text: "" };
    const result = errorsOnly(validateBedrockContent(content, 0));
    expect(result.some((e) => e.field.includes("text"))).toBe(true);
  });
});

// ── validateBedrockPayload ───────────────────────────────────

describe("validateBedrockPayload", () => {
  test("正常なペイロードはエラーなし", () => {
    expect(errorsOnly(validateBedrockPayload(makePayload()))).toHaveLength(0);
  });

  test("無効な anthropic_version は error", () => {
    const result = errorsOnly(
      validateBedrockPayload(makePayload({ anthropic_version: "v1" }))
    );
    expect(result.some((e) => e.field === "anthropic_version")).toBe(true);
  });

  test("max_tokens が 0 は error", () => {
    const result = errorsOnly(
      validateBedrockPayload(makePayload({ max_tokens: 0 }))
    );
    expect(result.some((e) => e.field === "max_tokens")).toBe(true);
  });

  test("max_tokens が 8193 は error", () => {
    const result = errorsOnly(
      validateBedrockPayload(makePayload({ max_tokens: 8193 }))
    );
    expect(result.some((e) => e.field === "max_tokens")).toBe(true);
  });

  test("messages 空は error", () => {
    const result = errorsOnly(
      validateBedrockPayload(makePayload({ messages: [] }))
    );
    expect(result.some((e) => e.field === "messages")).toBe(true);
  });

  test("画像なしは warning", () => {
    const payload = makePayload();
    payload.messages[0].content = [
      { type: "text", text: "テキストのみ" },
    ];
    const result = warningsOnly(validateBedrockPayload(payload));
    expect(result.some((e) => e.message.includes("画像"))).toBe(true);
  });

  test("テキストなしは warning", () => {
    const payload = makePayload();
    payload.messages[0].content = [
      {
        type: "image",
        source: { type: "base64", media_type: "image/jpeg", data: "AA" },
      },
    ];
    const result = warningsOnly(validateBedrockPayload(payload));
    expect(result.some((e) => e.message.includes("プロンプト"))).toBe(true);
  });
});

// ── validateDynamoDbItem ─────────────────────────────────────

describe("validateDynamoDbItem", () => {
  test("正常なアイテムはエラーなし", () => {
    expect(validateDynamoDbItem(makeDbItem())).toHaveLength(0);
  });

  test("document_id 空は error", () => {
    const result = errorsOnly(
      validateDynamoDbItem(makeDbItem({ document_id: "" }))
    );
    expect(result.some((e) => e.field === "document_id")).toBe(true);
  });

  test("analyzed_at 空は error", () => {
    const result = errorsOnly(
      validateDynamoDbItem(makeDbItem({ analyzed_at: "" }))
    );
    expect(result.some((e) => e.field === "analyzed_at")).toBe(true);
  });

  test("expires_at が 0 は error", () => {
    const result = errorsOnly(
      validateDynamoDbItem(makeDbItem({ expires_at: 0 }))
    );
    expect(result.some((e) => e.field === "expires_at")).toBe(true);
  });

  test("s3_bucket 空は error", () => {
    const result = errorsOnly(
      validateDynamoDbItem(makeDbItem({ s3_bucket: "" }))
    );
    expect(result.some((e) => e.field === "s3_bucket")).toBe(true);
  });

  test("無効な status は error", () => {
    const result = errorsOnly(
      validateDynamoDbItem(makeDbItem({ status: "pending" as any }))
    );
    expect(result.some((e) => e.field === "status")).toBe(true);
  });
});

// ── isValidDocumentType ──────────────────────────────────────

describe("isValidDocumentType", () => {
  test.each(["invoice", "estimate", "generic"])(
    '"%s" は有効',
    (t) => {
      expect(isValidDocumentType(t)).toBe(true);
    }
  );

  test.each(["receipt", "contract", ""])(
    '"%s" は無効',
    (t) => {
      expect(isValidDocumentType(t)).toBe(false);
    }
  );
});

// ── validateDocumentTypeConsistency ──────────────────────────

describe("validateDocumentTypeConsistency", () => {
  test("invoice ファイルと invoice 種別は一致", () => {
    expect(
      validateDocumentTypeConsistency("invoice_2026.jpg", "invoice")
    ).toHaveLength(0);
  });

  test("請求書ファイルと invoice 種別は一致", () => {
    expect(
      validateDocumentTypeConsistency("請求書_001.pdf", "invoice")
    ).toHaveLength(0);
  });

  test("invoice ファイルと generic 種別は warning", () => {
    const result = warningsOnly(
      validateDocumentTypeConsistency("invoice_2026.jpg", "generic")
    );
    expect(result).toHaveLength(1);
  });

  test("見積ファイルと estimate 種別は一致", () => {
    expect(
      validateDocumentTypeConsistency("見積書_001.pdf", "estimate")
    ).toHaveLength(0);
  });

  test("estimate ファイルと invoice 種別は warning", () => {
    const result = warningsOnly(
      validateDocumentTypeConsistency("estimate_2026.jpg", "invoice")
    );
    expect(result).toHaveLength(1);
  });

  test("ヒントなしファイルは warning なし", () => {
    expect(
      validateDocumentTypeConsistency("document.jpg", "generic")
    ).toHaveLength(0);
  });
});

// ── validateInvoiceResult ────────────────────────────────────

describe("validateInvoiceResult", () => {
  test("全フィールドありは warning なし", () => {
    const result = {
      document_type: "請求書",
      invoice_number: "INV-001",
      issue_date: "2026-09-01",
      vendor_name: "テスト株式会社",
      total_amount: 10000,
    };
    expect(validateInvoiceResult(result)).toHaveLength(0);
  });

  test("フィールド欠落は warning", () => {
    const result = warningsOnly(validateInvoiceResult({}));
    expect(result).toHaveLength(INVOICE_FIELDS.length);
  });

  test("負の total_amount は warning", () => {
    const result = warningsOnly(
      validateInvoiceResult({
        document_type: "請求書",
        invoice_number: "INV-001",
        issue_date: "2026-09-01",
        vendor_name: "テスト",
        total_amount: -100,
      })
    );
    expect(result.some((e) => e.field === "result.total_amount")).toBe(true);
  });
});

// ── validateEstimateResult ───────────────────────────────────

describe("validateEstimateResult", () => {
  test("全フィールドありは warning なし", () => {
    const result = {
      document_type: "見積書",
      estimate_number: "EST-001",
      issue_date: "2026-09-01",
      vendor_name: "テスト株式会社",
      total_amount: 50000,
    };
    expect(validateEstimateResult(result)).toHaveLength(0);
  });

  test("フィールド欠落は warning", () => {
    const result = warningsOnly(validateEstimateResult({}));
    expect(result).toHaveLength(ESTIMATE_FIELDS.length);
  });
});

// ── validateAnalysisResult ───────────────────────────────────

describe("validateAnalysisResult", () => {
  test("raw_text のみは warning", () => {
    const result = warningsOnly(
      validateAnalysisResult({ raw_text: "何か" }, "invoice")
    );
    expect(result.some((e) => e.message.includes("raw_text"))).toBe(true);
  });

  test("invoice 結果は invoice 検証が走る", () => {
    const result = warningsOnly(validateAnalysisResult({}, "invoice"));
    expect(result.length).toBe(INVOICE_FIELDS.length);
  });

  test("estimate 結果は estimate 検証が走る", () => {
    const result = warningsOnly(validateAnalysisResult({}, "estimate"));
    expect(result.length).toBe(ESTIMATE_FIELDS.length);
  });

  test("generic 結果は追加検証なし", () => {
    expect(validateAnalysisResult({}, "generic")).toHaveLength(0);
  });
});

// ── hasErrors / formatErrors ─────────────────────────────────

describe("hasErrors", () => {
  test("error ありは true", () => {
    expect(hasErrors([{ field: "x", message: "e", severity: "error" }])).toBe(
      true
    );
  });

  test("warning のみは false", () => {
    expect(
      hasErrors([{ field: "x", message: "w", severity: "warning" }])
    ).toBe(false);
  });

  test("空配列は false", () => {
    expect(hasErrors([])).toBe(false);
  });
});

describe("formatErrors", () => {
  test("空配列は通過メッセージ", () => {
    expect(formatErrors([])).toBe("すべてのチェックが通過しました");
  });

  test("error フォーマット", () => {
    const errors: ValidationError[] = [
      { field: "bucket", message: "テスト", severity: "error" },
    ];
    expect(formatErrors(errors)).toBe("[ERROR] bucket: テスト");
  });

  test("複数件は改行区切り", () => {
    const errors: ValidationError[] = [
      { field: "a", message: "e1", severity: "error" },
      { field: "b", message: "w1", severity: "warning" },
    ];
    expect(formatErrors(errors).split("\n")).toHaveLength(2);
  });
});
