/**
 * aws-multimodal-analysis: TypeScript クライアントユーティリティ
 *
 * Python 版 app/app.py の invoke_bedrock_agent() に対応する
 * 型安全なユーティリティ関数群。
 * AWS 呼び出し部分を分離し、ビジネスロジックをテスト可能に設計。
 */

// ── re-export ────────────────────────────────────────────────
export {
  validateFile,
  getDocumentType,
  buildPrompt,
  getMediaType,
  buildBedrockPayload,
  extractJsonFromText,
  buildDocumentId,
  buildDynamoDbItem,
} from "./helpers";

export {
  RETRYABLE_ERROR_CODES,
  RETRYABLE_STATUS_CODES,
  DEFAULT_RETRY_CONFIG,
  validateRetryConfig,
  resolveRetryConfig,
  extractErrorCode,
  extractStatusCode,
  isRetryableError,
  computeDelay,
  delay,
  retryAsync,
  withRetry,
} from "./retry";
export type { RetryConfig, RetryOptions } from "./retry";
