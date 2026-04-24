# ── 業務文書解析パイプライン ───────────────────────────────
# 構成:
#   S3（ファイルアップロード先）
#   S3 Event → Lambda トリガー
#   Lambda（ファイル取得・Bedrock 解析・DynamoDB 保存）
#   DynamoDB（解析結果の保存）
#   IAM Role（Lambda 実行権限）
#   CloudWatch Logs（ログ保存）
# ──────────────────────────────────────────────────────────

locals {
  name_prefix = "${var.project_name}-${var.environment}"
  bucket_name = var.s3_bucket_suffix != "" ? "${local.name_prefix}-docs-${var.s3_bucket_suffix}" : "${local.name_prefix}-docs"
}

# ── S3 バケット（アップロード先） ──────────────────────────
resource "aws_s3_bucket" "docs" {
  bucket = local.bucket_name

  # TODO: 本番では lifecycle_rule で古いファイルを自動削除する
  # TODO: 機密文書を扱う場合は S3 Object Lock を検討する
}

# パブリックアクセスを全てブロック（セキュリティ必須）
resource "aws_s3_bucket_public_access_block" "docs" {
  bucket = aws_s3_bucket.docs.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# サーバーサイド暗号化（デフォルト有効化）
resource "aws_s3_bucket_server_side_encryption_configuration" "docs" {
  bucket = aws_s3_bucket.docs.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
      # TODO: 本番では KMS カスタマーキーを使用する（aws:kms）
    }
  }
}

# バージョニング（誤削除対策）
resource "aws_s3_bucket_versioning" "docs" {
  bucket = aws_s3_bucket.docs.id
  versioning_configuration {
    status = "Enabled"
    # TODO: 本番では lifecycle_rule と組み合わせて古いバージョンを削除する
  }
}

# ── DynamoDB（解析結果の保存） ─────────────────────────────
resource "aws_dynamodb_table" "results" {
  name         = "${local.name_prefix}-results"
  billing_mode = var.dynamodb_billing_mode
  hash_key     = "document_id" # S3 オブジェクトキー
  range_key    = "analyzed_at" # 解析日時（ISO 8601）

  attribute {
    name = "document_id"
    type = "S"
  }

  attribute {
    name = "analyzed_at"
    type = "S"
  }

  # TTL 設定（古いレコードを自動削除）
  ttl {
    attribute_name = "expires_at"
    enabled        = true
    # TODO: Lambda 側で expires_at（例: 90日後の Unix タイム）を設定する
  }

  # TODO: 本番では point_in_time_recovery を有効にする
  # point_in_time_recovery { enabled = true }

  # TODO: 本番では KMS 暗号化を有効にする
  # server_side_encryption { enabled = true }
}

# ── IAM ロール ─────────────────────────────────────────────
resource "aws_iam_role" "lambda" {
  name        = "${local.name_prefix}-lambda-role"
  description = "IAM role for multimodal analysis Lambda"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "lambda_permissions" {
  name = "${local.name_prefix}-lambda-policy"
  role = aws_iam_role.lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # S3 からのファイル読み取り
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${aws_s3_bucket.docs.arn}/*"
        # TODO: 本番では特定プレフィックスのみに絞る
      },
      {
        # Bedrock 呼び出し
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel"]
        Resource = "*"
        # TODO: 本番では使用モデルの ARN に絞る
      },
      {
        # DynamoDB への書き込み
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem",
          "dynamodb:GetItem",
          "dynamodb:UpdateItem"
        ]
        Resource = aws_dynamodb_table.results.arn
      }
    ]
  })
}

# ── CloudWatch Logs ────────────────────────────────────────
resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${local.name_prefix}"
  retention_in_days = var.log_retention_days
}

# ── Lambda 関数 ────────────────────────────────────────────
data "archive_file" "lambda" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda"
  output_path = "${path.module}/../lambda.zip"
}

resource "aws_lambda_function" "main" {
  function_name = local.name_prefix
  description   = "業務文書解析 PoC - S3 アップロード → Bedrock → DynamoDB"
  role          = aws_iam_role.lambda.arn
  handler       = "index.handler"
  runtime       = "python3.11"
  timeout       = var.lambda_timeout
  memory_size   = var.lambda_memory_size

  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256

  environment {
    variables = {
      BEDROCK_MODEL_ID   = var.bedrock_model_id
      DYNAMODB_TABLE     = aws_dynamodb_table.results.name
      ALLOWED_EXTENSIONS = join(",", var.s3_allowed_extensions)
      LOG_LEVEL          = "INFO"
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.lambda_basic,
    aws_cloudwatch_log_group.lambda
  ]
}

# ── S3 → Lambda トリガー ───────────────────────────────────
resource "aws_lambda_permission" "s3" {
  statement_id  = "AllowS3Invoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.main.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.docs.arn
}

resource "aws_s3_bucket_notification" "trigger" {
  bucket = aws_s3_bucket.docs.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.main.arn
    events              = ["s3:ObjectCreated:*"]
    # TODO: filter_prefix / filter_suffix で対象フォルダ・拡張子を絞る
    # filter_prefix = "uploads/"
    # filter_suffix = ".pdf"
  }

  depends_on = [aws_lambda_permission.s3]
}
