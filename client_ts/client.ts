import type {
  FileValidationResult,
  BedrockPayload,
  DynamoDbItem,
  AnalysisResult,
  DocumentType,
} from "./types";

// ── 定数 ──────────────────────────────────────────────────
const ALLOWED_EXTENSIONS = [".png", ".jpg", ".jpeg", ".pdf"];
const MAX_FILE_SIZE_MB = 5;
const TTL_DAYS = 90;
const DEFAULT_MODEL_ID = "jp.anthropic.claude-haiku-4-5-20251001-v1:0";

// ── ファイル検証 ───────────────────────────────────────────
export function validateFile(key: string, sizeBytes: number): FileValidationResult {
  const dotIndex = key.lastIndexOf(".");
  const ext = dotIndex !== -1 ? key.substring(dotIndex).toLowerCase() : "";

  if (!ALLOWED_EXTENSIONS.includes(ext)) {
    return { valid: false, error: `未対応の拡張子: ${ext}` };
  }

  const sizeMb = sizeBytes / (1024 * 1024);
  if (sizeMb > MAX_FILE_SIZE_MB) {
    return {
      valid: false,
      error: `ファイルサイズ超過: ${sizeMb.toFixed(1)}MB（上限 ${MAX_FILE_SIZE_MB}MB）`,
    };
  }

  return { valid: true };
}

// ── 文書種別判定 ────────────────────────────────────────────
export function getDocumentType(key: string): DocumentType {
  const keyLower = key.toLowerCase();
  if (keyLower.includes("invoice") || keyLower.includes("請求")) return "invoice";
  if (keyLower.includes("estimate") || keyLower.includes("見積")) return "estimate";
  return "generic";
}

// ── 解析プロンプト生成 ─────────────────────────────────────
export function buildPrompt(key: string): string {
  const docType = getDocumentType(key);

  if (docType === "invoice") {
    return `この請求書画像から以下の情報を JSON 形式で抽出してください。
該当する情報がない場合は null としてください。

{
  "document_type": "請求書",
  "invoice_number": "請求書番号",
  "issue_date": "発行日（YYYY-MM-DD）",
  "due_date": "支払期日（YYYY-MM-DD）",
  "vendor_name": "請求元会社名",
  "total_amount": 請求金額（数値）,
  "currency": "通貨（JPY/USD等）",
  "items": [
    {"description": "品目", "quantity": 数量, "unit_price": 単価, "amount": 金額}
  ]
}`;
  }

  if (docType === "estimate") {
    return `この見積書画像から以下の情報を JSON 形式で抽出してください。
{
  "document_type": "見積書",
  "estimate_number": "見積番号",
  "issue_date": "発行日（YYYY-MM-DD）",
  "valid_until": "有効期限（YYYY-MM-DD）",
  "vendor_name": "見積元会社名",
  "total_amount": 見積金額（数値）,
  "items": [
    {"description": "品目", "quantity": 数量, "unit_price": 単価, "amount": 金額}
  ]
}`;
  }

  return `この業務文書から重要な情報を JSON 形式で抽出してください。
文書の種類、日付、金額、関係者名、主要な項目を含めてください。
情報が読み取れない場合は null としてください。`;
}

// ── メディアタイプ取得 ─────────────────────────────────────
export function getMediaType(ext: string): string {
  const mediaTypeMap: Record<string, string> = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".pdf": "application/pdf",
  };
  return mediaTypeMap[ext.toLowerCase()] ?? "image/jpeg";
}

// ── Bedrock ペイロード構築 ──────────────────────────────────
export function buildBedrockPayload(fileBase64: string, key: string): BedrockPayload {
  const dotIndex = key.lastIndexOf(".");
  const ext = dotIndex !== -1 ? key.substring(dotIndex).toLowerCase() : "";
  const mediaType = getMediaType(ext);
  const prompt = buildPrompt(key);

  return {
    anthropic_version: "bedrock-2023-05-31",
    max_tokens: 1000,
    messages: [
      {
        role: "user",
        content: [
          {
            type: "image",
            source: {
              type: "base64",
              media_type: mediaType,
              data: fileBase64,
            },
          },
          { type: "text", text: prompt },
        ],
      },
    ],
  };
}

// ── Bedrock レスポンスから JSON 抽出 ────────────────────────
export function extractJsonFromText(text: string): AnalysisResult {
  const start = text.indexOf("{");
  const end = text.lastIndexOf("}");

  if (start === -1 || end === -1 || start >= end) {
    return { raw_text: text };
  }

  try {
    return JSON.parse(text.substring(start, end + 1)) as AnalysisResult;
  } catch {
    return { raw_text: text };
  }
}

// ── ドキュメント ID 生成 ────────────────────────────────────
export function buildDocumentId(bucket: string, key: string): string {
  return `${bucket}/${key}`;
}

// ── DynamoDB アイテム構築 ───────────────────────────────────
export function buildDynamoDbItem(
  documentId: string,
  bucket: string,
  key: string,
  result: AnalysisResult,
  modelId: string = DEFAULT_MODEL_ID,
): DynamoDbItem {
  const now = new Date();
  const expiresAt = Math.floor(now.getTime() / 1000) + TTL_DAYS * 24 * 60 * 60;

  return {
    document_id: documentId,
    analyzed_at: now.toISOString(),
    expires_at: expiresAt,
    s3_bucket: bucket,
    s3_key: key,
    model_id: modelId,
    status: "success",
    result,
  };
}
