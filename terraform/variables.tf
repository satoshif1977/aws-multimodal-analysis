variable "project_name" {
  description = "プロジェクト名（リソース命名に使用）"
  type        = string
  default     = "multimodal"
}

variable "environment" {
  description = "環境名（dev / stg / prod）"
  type        = string
  default     = "dev"
}

variable "aws_region" {
  description = "デプロイ先 AWS リージョン"
  type        = string
  default     = "ap-northeast-1"
}

# ── Bedrock 設定 ───────────────────────────────────────────
variable "bedrock_model_id" {
  description = "使用する Bedrock マルチモーダルモデル ID"
  type        = string
  default     = "anthropic.claude-3-5-sonnet-20241022-v2:0"
  # 候補（マルチモーダル対応モデル）:
  #   anthropic.claude-3-5-sonnet-20241022-v2:0  （高精度・推奨）
  #   anthropic.claude-3-haiku-20240307-v1:0     （低コスト・簡易解析向け）
  # TODO: 本番では解析精度とコストのバランスで選定する
}

# ── S3 設定 ────────────────────────────────────────────────
variable "s3_bucket_suffix" {
  description = "S3 バケット名のサフィックス（グローバル一意にするため）"
  type        = string
  default     = ""
  # TODO: AWS アカウント ID などを使ってユニークにする
  # 例: terraform.tfvars に "580983239795" を設定
}

variable "s3_allowed_extensions" {
  description = "解析を許可するファイル拡張子"
  type        = list(string)
  default     = [".png", ".jpg", ".jpeg", ".pdf"]
  # TODO: 本番では業務要件に合わせて絞る
}

# ── DynamoDB 設定 ──────────────────────────────────────────
variable "dynamodb_billing_mode" {
  description = "DynamoDB の課金モード"
  type        = string
  default     = "PAY_PER_REQUEST"
  # PAY_PER_REQUEST: 従量課金（PoC・低トラフィック向け）
  # PROVISIONED: 固定スループット（本番・高トラフィック向け）
}

# ── Lambda 設定 ────────────────────────────────────────────
variable "lambda_timeout" {
  description = "Lambda タイムアウト秒数"
  type        = number
  default     = 60
  # PDF 解析は時間がかかるため長めに設定
  # TODO: Bedrock の応答時間を計測して調整する
}

variable "lambda_memory_size" {
  description = "Lambda メモリサイズ（MB）"
  type        = number
  default     = 512
  # TODO: PDF 処理のメモリ使用量に応じて調整する
}

variable "log_retention_days" {
  description = "CloudWatch Logs の保持日数"
  type        = number
  default     = 3
  # TODO: 本番では 30〜90 日に延長する
}
