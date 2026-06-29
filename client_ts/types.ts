export interface S3EventRecord {
  s3: {
    bucket: { name: string };
    object: { key: string; size: number };
  };
}

export interface S3Event {
  Records: S3EventRecord[];
}

export type DocumentType = "invoice" | "estimate" | "generic";

export interface FileValidationResult {
  valid: boolean;
  error?: string;
}

export interface DocumentItem {
  description: string;
  quantity: number;
  unit_price: number;
  amount: number;
}

export interface AnalysisResult {
  [key: string]: unknown;
}

export interface BedrockImageSource {
  type: "base64";
  media_type: string;
  data: string;
}

export interface BedrockContent {
  type: "image" | "text";
  source?: BedrockImageSource;
  text?: string;
}

export interface BedrockMessage {
  role: "user";
  content: BedrockContent[];
}

export interface BedrockPayload {
  anthropic_version: string;
  max_tokens: number;
  messages: BedrockMessage[];
}

export interface DynamoDbItem {
  document_id: string;
  analyzed_at: string;
  expires_at: number;
  s3_bucket: string;
  s3_key: string;
  model_id: string;
  status: "success" | "error";
  result: AnalysisResult;
}
