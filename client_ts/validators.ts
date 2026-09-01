/**
 * 文書解析パイプライン バリデーター
 *
 * S3 イベント入力・Bedrock ペイロード・DynamoDB アイテム・解析結果を
 * 検証する純粋関数群。AWS SDK に依存しないため単体テストが容易。
 *
 * 検証内容:
 *   - S3 イベント構造（Records / bucket / object）
 *   - Bedrock ペイロード（version / max_tokens / messages）
 *   - DynamoDB アイテム（document_id / TTL / status）
 *   - 解析結果の必須フィールド（請求書・見積書）
 *   - 文書種別とファイル名の整合性
 */

import type {
  S3Event,
  S3EventRecord,
  DocumentType,
  BedrockPayload,
  BedrockContent,
  DynamoDbItem,
  AnalysisResult,
} from "./types";

// ── 型定義 ────────────────────────────────────────────────────

export interface ValidationError {
  field: string;
  message: string;
  severity: "error" | "warning";
}

// ── 定数 ─────────────────────────────────────────────────────

/** S3 バケット名の正規表現（AWS 命名規則） */
export const BUCKET_NAME_PATTERN = /^[a-z0-9][a-z0-9.\-]{1,61}[a-z0-9]$/;

/** 有効な Anthropic API バージョン */
export const VALID_ANTHROPIC_VERSIONS = ["bedrock-2023-05-31"] as const;

/** max_tokens の許容範囲 */
export const MIN_MAX_TOKENS = 1;
export const MAX_MAX_TOKENS = 8192;

/** 有効な文書種別 */
export const VALID_DOCUMENT_TYPES: readonly DocumentType[] = [
  "invoice",
  "estimate",
  "generic",
];

/** DynamoDB TTL の最小値（現在時刻より未来であるべき） */
export const MIN_TTL_OFFSET_SECONDS = 0;

/** 有効な DynamoDB ステータス */
export const VALID_DB_STATUSES = ["success", "error"] as const;

/** 請求書解析結果の推奨フィールド */
export const INVOICE_FIELDS = [
  "document_type",
  "invoice_number",
  "issue_date",
  "vendor_name",
  "total_amount",
] as const;

/** 見積書解析結果の推奨フィールド */
export const ESTIMATE_FIELDS = [
  "document_type",
  "estimate_number",
  "issue_date",
  "vendor_name",
  "total_amount",
] as const;

/** 有効な Bedrock content type */
export const VALID_CONTENT_TYPES = ["image", "text"] as const;

/** 有効な画像ソースタイプ */
export const VALID_IMAGE_SOURCE_TYPES = ["base64"] as const;

/** 有効なメディアタイプ */
export const VALID_MEDIA_TYPES = [
  "image/jpeg",
  "image/png",
  "application/pdf",
] as const;

// ── S3 イベントバリデーション ─────────────────────────────────

/** S3 バケット名が有効か */
export function isValidBucketName(name: string): boolean {
  return BUCKET_NAME_PATTERN.test(name);
}

/** S3 オブジェクトキーが空でないか */
export function isNonEmptyKey(key: string): boolean {
  return key.trim().length > 0;
}

/** S3 EventRecord を検証する */
export function validateS3Record(
  record: S3EventRecord,
  index: number
): ValidationError[] {
  const errors: ValidationError[] = [];
  const prefix = `Records[${index}]`;

  if (!record.s3) {
    errors.push({
      field: `${prefix}.s3`,
      message: "s3 フィールドが未定義です",
      severity: "error",
    });
    return errors;
  }

  if (!record.s3.bucket?.name) {
    errors.push({
      field: `${prefix}.s3.bucket.name`,
      message: "バケット名が未定義です",
      severity: "error",
    });
  } else if (!isValidBucketName(record.s3.bucket.name)) {
    errors.push({
      field: `${prefix}.s3.bucket.name`,
      message: `無効なバケット名: "${record.s3.bucket.name}"`,
      severity: "error",
    });
  }

  if (!record.s3.object) {
    errors.push({
      field: `${prefix}.s3.object`,
      message: "object フィールドが未定義です",
      severity: "error",
    });
  } else {
    if (!isNonEmptyKey(record.s3.object.key ?? "")) {
      errors.push({
        field: `${prefix}.s3.object.key`,
        message: "オブジェクトキーが空です",
        severity: "error",
      });
    }

    if (
      record.s3.object.size !== undefined &&
      record.s3.object.size < 0
    ) {
      errors.push({
        field: `${prefix}.s3.object.size`,
        message: `ファイルサイズが負の値です: ${record.s3.object.size}`,
        severity: "error",
      });
    }
  }

  return errors;
}

/** S3 イベント全体を検証する */
export function validateS3Event(event: S3Event): ValidationError[] {
  const errors: ValidationError[] = [];

  if (!event.Records || !Array.isArray(event.Records)) {
    errors.push({
      field: "Records",
      message: "Records 配列が未定義または配列ではありません",
      severity: "error",
    });
    return errors;
  }

  if (event.Records.length === 0) {
    errors.push({
      field: "Records",
      message: "Records 配列が空です",
      severity: "error",
    });
    return errors;
  }

  event.Records.forEach((record, idx) => {
    errors.push(...validateS3Record(record, idx));
  });

  return errors;
}

// ── Bedrock ペイロードバリデーション ──────────────────────────

/** Bedrock content を検証する */
export function validateBedrockContent(
  content: BedrockContent,
  index: number
): ValidationError[] {
  const errors: ValidationError[] = [];
  const prefix = `content[${index}]`;

  if (
    !(VALID_CONTENT_TYPES as readonly string[]).includes(content.type)
  ) {
    errors.push({
      field: `${prefix}.type`,
      message: `無効な content type: "${content.type}"`,
      severity: "error",
    });
  }

  if (content.type === "image") {
    if (!content.source) {
      errors.push({
        field: `${prefix}.source`,
        message: "image content に source が未定義です",
        severity: "error",
      });
    } else {
      if (
        !(VALID_IMAGE_SOURCE_TYPES as readonly string[]).includes(
          content.source.type
        )
      ) {
        errors.push({
          field: `${prefix}.source.type`,
          message: `無効な source type: "${content.source.type}"`,
          severity: "error",
        });
      }
      if (
        !(VALID_MEDIA_TYPES as readonly string[]).includes(
          content.source.media_type
        )
      ) {
        errors.push({
          field: `${prefix}.source.media_type`,
          message: `無効な media_type: "${content.source.media_type}"`,
          severity: "error",
        });
      }
      if (!content.source.data) {
        errors.push({
          field: `${prefix}.source.data`,
          message: "Base64 データが空です",
          severity: "error",
        });
      }
    }
  }

  if (content.type === "text" && !content.text) {
    errors.push({
      field: `${prefix}.text`,
      message: "text content が空です",
      severity: "error",
    });
  }

  return errors;
}

/** Bedrock ペイロードを検証する */
export function validateBedrockPayload(
  payload: BedrockPayload
): ValidationError[] {
  const errors: ValidationError[] = [];

  if (
    !(VALID_ANTHROPIC_VERSIONS as readonly string[]).includes(
      payload.anthropic_version
    )
  ) {
    errors.push({
      field: "anthropic_version",
      message: `無効な API バージョン: "${payload.anthropic_version}"`,
      severity: "error",
    });
  }

  if (
    !Number.isInteger(payload.max_tokens) ||
    payload.max_tokens < MIN_MAX_TOKENS ||
    payload.max_tokens > MAX_MAX_TOKENS
  ) {
    errors.push({
      field: "max_tokens",
      message: `max_tokens は ${MIN_MAX_TOKENS}〜${MAX_MAX_TOKENS} の整数にしてください（現在: ${payload.max_tokens}）`,
      severity: "error",
    });
  }

  if (!payload.messages || payload.messages.length === 0) {
    errors.push({
      field: "messages",
      message: "messages が空です",
      severity: "error",
    });
    return errors;
  }

  const firstMsg = payload.messages[0];
  if (firstMsg.role !== "user") {
    errors.push({
      field: "messages[0].role",
      message: `最初のメッセージの role は "user" である必要があります（現在: "${firstMsg.role}"）`,
      severity: "error",
    });
  }

  if (!firstMsg.content || firstMsg.content.length === 0) {
    errors.push({
      field: "messages[0].content",
      message: "content が空です",
      severity: "error",
    });
  } else {
    const hasImage = firstMsg.content.some((c) => c.type === "image");
    const hasText = firstMsg.content.some((c) => c.type === "text");

    if (!hasImage) {
      errors.push({
        field: "messages[0].content",
        message: "マルチモーダルリクエストに画像が含まれていません",
        severity: "warning",
      });
    }
    if (!hasText) {
      errors.push({
        field: "messages[0].content",
        message: "プロンプト（text）が含まれていません",
        severity: "warning",
      });
    }

    firstMsg.content.forEach((c, idx) => {
      errors.push(...validateBedrockContent(c, idx));
    });
  }

  return errors;
}

// ── DynamoDB アイテムバリデーション ───────────────────────────

/** DynamoDB アイテムを検証する */
export function validateDynamoDbItem(item: DynamoDbItem): ValidationError[] {
  const errors: ValidationError[] = [];

  if (!item.document_id) {
    errors.push({
      field: "document_id",
      message: "document_id が空です",
      severity: "error",
    });
  }

  if (!item.analyzed_at) {
    errors.push({
      field: "analyzed_at",
      message: "analyzed_at が空です",
      severity: "error",
    });
  }

  if (
    !Number.isInteger(item.expires_at) ||
    item.expires_at <= MIN_TTL_OFFSET_SECONDS
  ) {
    errors.push({
      field: "expires_at",
      message: `expires_at は正の整数（UNIX タイムスタンプ）である必要があります（現在: ${item.expires_at}）`,
      severity: "error",
    });
  }

  if (!item.s3_bucket) {
    errors.push({
      field: "s3_bucket",
      message: "s3_bucket が空です",
      severity: "error",
    });
  }

  if (!item.s3_key) {
    errors.push({
      field: "s3_key",
      message: "s3_key が空です",
      severity: "error",
    });
  }

  if (!(VALID_DB_STATUSES as readonly string[]).includes(item.status)) {
    errors.push({
      field: "status",
      message: `無効な status: "${item.status}"。有効値: ${VALID_DB_STATUSES.join(", ")}`,
      severity: "error",
    });
  }

  return errors;
}

// ── 文書種別バリデーション ────────────────────────────────────

/** 文書種別が有効か */
export function isValidDocumentType(docType: string): boolean {
  return (VALID_DOCUMENT_TYPES as readonly string[]).includes(docType);
}

/** ファイル名から推定される文書種別とバリデーション対象の一致チェック */
export function validateDocumentTypeConsistency(
  key: string,
  docType: DocumentType
): ValidationError[] {
  const errors: ValidationError[] = [];
  const keyLower = key.toLowerCase();

  const hasInvoiceHint =
    keyLower.includes("invoice") || keyLower.includes("請求");
  const hasEstimateHint =
    keyLower.includes("estimate") || keyLower.includes("見積");

  if (hasInvoiceHint && docType !== "invoice") {
    errors.push({
      field: "documentType",
      message: `ファイル名に請求書のヒントがありますが、文書種別が "${docType}" です`,
      severity: "warning",
    });
  }

  if (hasEstimateHint && docType !== "estimate") {
    errors.push({
      field: "documentType",
      message: `ファイル名に見積書のヒントがありますが、文書種別が "${docType}" です`,
      severity: "warning",
    });
  }

  return errors;
}

// ── 解析結果バリデーション ────────────────────────────────────

/** 請求書の解析結果に推奨フィールドがあるか */
export function validateInvoiceResult(
  result: AnalysisResult
): ValidationError[] {
  const errors: ValidationError[] = [];

  for (const field of INVOICE_FIELDS) {
    if (!(field in result)) {
      errors.push({
        field: `result.${field}`,
        message: `請求書の推奨フィールドが欠落: ${field}`,
        severity: "warning",
      });
    }
  }

  if ("total_amount" in result && typeof result.total_amount === "number") {
    if (result.total_amount < 0) {
      errors.push({
        field: "result.total_amount",
        message: "合計金額が負の値です",
        severity: "warning",
      });
    }
  }

  return errors;
}

/** 見積書の解析結果に推奨フィールドがあるか */
export function validateEstimateResult(
  result: AnalysisResult
): ValidationError[] {
  const errors: ValidationError[] = [];

  for (const field of ESTIMATE_FIELDS) {
    if (!(field in result)) {
      errors.push({
        field: `result.${field}`,
        message: `見積書の推奨フィールドが欠落: ${field}`,
        severity: "warning",
      });
    }
  }

  return errors;
}

/** 解析結果を文書種別に応じて検証する */
export function validateAnalysisResult(
  result: AnalysisResult,
  docType: DocumentType
): ValidationError[] {
  const errors: ValidationError[] = [];

  if ("raw_text" in result && Object.keys(result).length === 1) {
    errors.push({
      field: "result",
      message:
        "JSON 抽出に失敗し raw_text のみが含まれています。Bedrock の回答が JSON 形式でなかった可能性があります",
      severity: "warning",
    });
    return errors;
  }

  if (docType === "invoice") {
    errors.push(...validateInvoiceResult(result));
  } else if (docType === "estimate") {
    errors.push(...validateEstimateResult(result));
  }

  return errors;
}

// ── ユーティリティ ────────────────────────────────────────────

/** エラーの有無を判定する（warning は含まない） */
export function hasErrors(errors: ValidationError[]): boolean {
  return errors.some((e) => e.severity === "error");
}

/** エラーをフォーマットする */
export function formatErrors(errors: ValidationError[]): string {
  if (errors.length === 0) return "すべてのチェックが通過しました";
  return errors
    .map((e) => `[${e.severity.toUpperCase()}] ${e.field}: ${e.message}`)
    .join("\n");
}
