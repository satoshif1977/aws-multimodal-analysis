# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

## [1.10.0] - 2026-07-10

### Added
- `lambda/test_index_detail.py`: Python ユニットテスト 16 件 → 44 件に拡充（詳細ケース・エラー系・マルチモーダル入力検証追加）

### Changed
- docs: README に TypeScript テストバッジ・CI テーブル・テスト手順を追記
- Dependabot: `boto3` / `actions/setup-node` v4 → v6 / `actions/checkout` v4 → v7 更新

## [1.9.0] - 2026-06-18

### Changed
- `go-test.yml`: actions/checkout v4→v6・go-version 1.21→1.22 に更新（go.mod との整合）
- README に Go Test CI バッジ・Go 1.22 言語バッジを追加

## [1.8.0] - 2026-06-16

### Changed
- boto3 >=1.43.14 -> >=1.43.29
- actions/setup-go v5 -> v6
- streamlit >=1.57.0 -> >=1.58.0

## [1.7.0] - 2026-06-15

### Added
- README に Go ユニットテスト（24件）・CI ワークフロー表を追記

## [1.6.0] - 2026-06-11

### Added
- `lambda_go/main_test.go`: Go Lambda ユニットテスト 24 件追加
  - getEnv / isAllowedExt / getExt / mediaType / buildPrompt / Handler（空イベント・未対応拡張子・サイズ超過）
- `lambda_go/go.sum`: 依存関係ロックファイル追加
- `.github/workflows/go-test.yml`: Go テスト CI 追加（`lambda_go/**` 変更時に自動実行）

### Changed
- Terraform IaC セキュリティ強化（`terraform/main.tf`）
  - DynamoDB: PITR・`deletion_protection_enabled = true` を追加
  - Lambda: `tracing_config { mode = "PassThrough" }` を追加（X-Ray コスト無料）
  - Bedrock IAM: `Resource="*"` → 特定 ARN パターンに絞る（foundation-model / inference-profile）
  - S3: ライフサイクルルールでマルチパートアップロード未完了を 7 日後に自動削除（CKV_AWS_300 対応）
- Python 品質向上
  - `lambda/index.py`: boto3 クライアント変数名を `_` プレフィックスに統一
  - `app.py`: `@st.cache_resource` でクライアントを再利用（毎 rerun での再生成を防止）
- ユニットテスト強化（10 件→16 件）: TestAnalyzeWithBedrock / TestSaveToDynamoDB を各 2〜3 ケース追加

## [1.5.0] - 2026-06-01

### Changed
- Bedrock モデルを `Claude 3.5 Haiku`（ap-northeast-1 で使用不可）→ `Claude Haiku 4.5` に更新
  （`jp.anthropic.claude-haiku-4-5-20251001-v1:0`）

## [1.4.0] - 2026-05-29

### Changed
- デフォルトブランチを `master` → `main` に統一・`master` ブランチ削除
- CI ワークフローのブランチ指定を `main` のみに修正
- Dependabot: `hashicorp/aws` v5→v6（`terraform plan 0c/0d` 確認済み）・`streamlit` >=1.57.0・`boto3` >=1.43.14 を更新
- Dependabot: `actions/checkout` v6・`actions/setup-python` v6・`hashicorp/setup-terraform` v4 を更新

## [1.3.1] - 2026-05-26

### Added
- `docs/multimodal-document-analysis.drawio` / `.png` を追加（未追跡だったため commit）
- `.gitignore` に draw.io バックアップ（`.*.bkp`）パターンを追加

### Fixed
- README のモデル名を `Claude 3 Haiku` → `Claude 3.5 Haiku` に統一（5か所）
- 推定コストの単価を Claude 3.5 Haiku の実際の料金（$0.80/1M tokens）に修正

## [1.3.0] - 2026-05-19

### Added
- CONTRIBUTING.md 追加（PR プロセス・スタイルガイド）

## [1.2.0] - 2026-05-15

### Added
- SECURITY.md 追加
- Dependabot 設定追加
- README にトラブルシューティング・ローカル開発テスト方法セクション追加
- デモ GIF 追加（S3 → Bedrock → DynamoDB フロー）

### Changed
- Claude 3 Haiku → Claude 3.5 Haiku（`anthropic.claude-3-5-haiku-20241022-v1:0`）に移行（EOL: 2026-09-10）
- 型ヒントを改善・`requirements.txt` に上限バージョン制約を追加

## [1.1.0] - 2026-04-24

### Added
- GitHub Actions CI 追加（Terraform / pytest / Checkov セキュリティスキャン）
- Lambda ユニットテスト追加（`pytest` + `moto` による S3/DynamoDB モック）
- MIT License 追加

## [1.0.0] - 2026-03-16

### Added
- 初回実装：S3 + Lambda + DynamoDB + Amazon Bedrock（Claude 3 Haiku）による業務文書解析パイプライン
  - S3 に PDF/テキストをアップロード → Lambda が Bedrock を呼び出し要約・分類
  - 解析結果を DynamoDB に保存
  - Streamlit Web UI（boto3 経由で Lambda を直接 Invoke）
- Terraform IaC（S3 / Lambda / DynamoDB / IAM / CloudWatch Logs）
- draw.io アーキテクチャ構成図
