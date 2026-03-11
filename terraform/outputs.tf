output "s3_bucket_name" {
  description = "ファイルアップロード先 S3 バケット名"
  value       = aws_s3_bucket.docs.bucket
}

output "dynamodb_table_name" {
  description = "解析結果保存先 DynamoDB テーブル名"
  value       = aws_dynamodb_table.results.name
}

output "lambda_function_name" {
  description = "Lambda 関数名"
  value       = aws_lambda_function.main.function_name
}

output "lambda_log_group" {
  description = "CloudWatch Logs グループ名"
  value       = aws_cloudwatch_log_group.lambda.name
}

output "upload_command_example" {
  description = "ファイルアップロードのサンプルコマンド"
  value       = "aws s3 cp invoice.pdf s3://${aws_s3_bucket.docs.bucket}/uploads/invoice.pdf"
}
