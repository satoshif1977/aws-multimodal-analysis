// aws-multimodal-analysis: Go 実装（Python 版との並置）
//
// Python 版との比較ポイント:
//   - init() でクライアント初期化 → Python のモジュールトップ変数と同等
//   - events.S3Event で型安全にイベントを受け取る（Python の event dict より安全）
//   - base64 エンコードは encoding/base64 標準ライブラリで実施
//   - JSON の抽出は strings.Index/LastIndex で { } を特定（Python と同ロジック）
//   - DynamoDB への保存は types.AttributeValue を直接構築（attributevalue.MarshalMap 不使用）
//   - コールドスタートが Python より高速（バイナリ実行・ランタイム起動なし）
//
// ビルド方法:
//
//	GOOS=linux GOARCH=arm64 go build -o bootstrap main.go
//	zip lambda_go.zip bootstrap
package main

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"os"
	"strings"
	"time"

	"github.com/aws/aws-lambda-go/events"
	"github.com/aws/aws-lambda-go/lambda"
	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/service/bedrockruntime"
	"github.com/aws/aws-sdk-go-v2/service/dynamodb"
	"github.com/aws/aws-sdk-go-v2/service/dynamodb/types"
	"github.com/aws/aws-sdk-go-v2/service/s3"
)

// ── 環境変数 ──────────────────────────────────────────────
var (
	bedrockModelID    = getEnv("BEDROCK_MODEL_ID", "jp.anthropic.claude-haiku-4-5-20251001-v1:0")
	dynamoTableName   = getEnv("DYNAMODB_TABLE", "multimodal-dev-results")
	allowedExtensions = strings.Split(getEnv("ALLOWED_EXTENSIONS", ".png,.jpg,.jpeg,.pdf"), ",")
)

const (
	maxFileSizeMB = 5
	ttlDays       = 90
)

// ── AWS クライアント（init で初期化・コンテナ再利用時に再生成しない） ──
var (
	s3Client      *s3.Client
	bedrockClient *bedrockruntime.Client
	dynamoClient  *dynamodb.Client
)

func init() {
	cfg, err := config.LoadDefaultConfig(context.Background())
	if err != nil {
		NewLoggerFromEnv(LoggerOptions{}).Error("AWS 設定の読み込みに失敗しました", "error", err.Error())
		os.Exit(1)
	}
	s3Client = s3.NewFromConfig(cfg)
	bedrockClient = bedrockruntime.NewFromConfig(cfg)
	dynamoClient = dynamodb.NewFromConfig(cfg)
}

// ── Bedrock リクエスト / レスポンス型 ────────────────────
type BedrockBody struct {
	AnthropicVersion string           `json:"anthropic_version"`
	MaxTokens        int              `json:"max_tokens"`
	Messages         []BedrockMessage `json:"messages"`
}

type BedrockMessage struct {
	Role    string                   `json:"role"`
	Content []map[string]interface{} `json:"content"`
}

type BedrockResponse struct {
	Content []struct {
		Text string `json:"text"`
	} `json:"content"`
}

// ── 処理結果型 ────────────────────────────────────────────
type ProcessedRecord struct {
	Key    string `json:"key"`
	Status string `json:"status"`
	Reason string `json:"reason,omitempty"`
}

// ── ヘルパー ──────────────────────────────────────────────
func getEnv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func isAllowedExt(key string) bool {
	idx := strings.LastIndex(key, ".")
	if idx < 0 {
		return false
	}
	ext := strings.ToLower(key[idx:])
	for _, a := range allowedExtensions {
		if ext == a {
			return true
		}
	}
	return false
}

func getExt(key string) string {
	idx := strings.LastIndex(key, ".")
	if idx < 0 {
		return ""
	}
	return strings.ToLower(key[idx:])
}

func mediaType(key string) string {
	switch getExt(key) {
	case ".jpg", ".jpeg":
		return "image/jpeg"
	case ".png":
		return "image/png"
	case ".pdf":
		return "application/pdf"
	default:
		return "image/jpeg"
	}
}

// ── 解析プロンプト生成（Python 版と同ロジック） ──────────
func buildPrompt(key string) string {
	keyLower := strings.ToLower(key)
	if strings.Contains(keyLower, "invoice") || strings.Contains(keyLower, "請求") {
		return `この請求書画像から以下の情報をJSON形式で抽出してください。該当する情報がない場合はnullとしてください。
{"document_type":"請求書","invoice_number":"請求書番号","issue_date":"発行日（YYYY-MM-DD）","due_date":"支払期日（YYYY-MM-DD）","vendor_name":"請求元会社名","total_amount":0,"currency":"JPY"}`
	} else if strings.Contains(keyLower, "estimate") || strings.Contains(keyLower, "見積") {
		return `この見積書画像から以下の情報をJSON形式で抽出してください。
{"document_type":"見積書","estimate_number":"見積番号","issue_date":"発行日（YYYY-MM-DD）","valid_until":"有効期限（YYYY-MM-DD）","vendor_name":"見積元会社名","total_amount":0}`
	}
	return `この業務文書から重要な情報をJSON形式で抽出してください。文書の種類、日付、金額、関係者名、主要な項目を含めてください。情報が読み取れない場合はnullとしてください。`
}

// ── S3 からファイル取得 ───────────────────────────────────
func getFileFromS3(ctx context.Context, bucket, key string) ([]byte, error) {
	out, err := s3Client.GetObject(ctx, &s3.GetObjectInput{
		Bucket: aws.String(bucket),
		Key:    aws.String(key),
	})
	if err != nil {
		return nil, fmt.Errorf("S3 取得エラー: %w", err)
	}
	defer out.Body.Close()
	return io.ReadAll(out.Body)
}

// ── Bedrock でファイル解析 ────────────────────────────────
func analyzeWithBedrock(ctx context.Context, fileBytes []byte, key string) (map[string]interface{}, error) {
	fileB64 := base64.StdEncoding.EncodeToString(fileBytes)
	mt := mediaType(key)
	prompt := buildPrompt(key)

	body := BedrockBody{
		AnthropicVersion: "bedrock-2023-05-31",
		MaxTokens:        1000,
		Messages: []BedrockMessage{
			{
				Role: "user",
				Content: []map[string]interface{}{
					{
						"type": "image",
						"source": map[string]interface{}{
							"type":       "base64",
							"media_type": mt,
							"data":       fileB64,
						},
					},
					{
						"type": "text",
						"text": prompt,
					},
				},
			},
		},
	}

	bodyBytes, err := json.Marshal(body)
	if err != nil {
		return nil, fmt.Errorf("リクエスト JSON 変換エラー: %w", err)
	}

	out, err := bedrockClient.InvokeModel(ctx, &bedrockruntime.InvokeModelInput{
		ModelId:     aws.String(bedrockModelID),
		Body:        bodyBytes,
		ContentType: aws.String("application/json"),
		Accept:      aws.String("application/json"),
	})
	if err != nil {
		return nil, fmt.Errorf("Bedrock 呼び出しエラー: %w", err)
	}

	var resp BedrockResponse
	if err := json.Unmarshal(out.Body, &resp); err != nil {
		return nil, fmt.Errorf("レスポンス JSON 解析エラー: %w", err)
	}
	if len(resp.Content) == 0 {
		return nil, fmt.Errorf("Bedrock レスポンスが空")
	}

	rawText := resp.Content[0].Text

	// JSON 部分を抽出（Bedrock が余分なテキストを付けることがある）
	start := strings.Index(rawText, "{")
	end := strings.LastIndex(rawText, "}")
	var extracted map[string]interface{}
	if start >= 0 && end > start {
		if err := json.Unmarshal([]byte(rawText[start:end+1]), &extracted); err != nil {
			extracted = map[string]interface{}{"raw_text": rawText}
		}
	} else {
		extracted = map[string]interface{}{"raw_text": rawText}
	}

	return extracted, nil
}

// ── DynamoDB に保存 ────────────────────────────────────────
func saveToDynamoDB(ctx context.Context, documentID, bucket, key string, result map[string]interface{}) error {
	now := time.Now().UTC()
	expiresAt := now.AddDate(0, 0, ttlDays).Unix()
	analyzedAt := now.Format(time.RFC3339)

	resultJSON, err := json.Marshal(result)
	if err != nil {
		return fmt.Errorf("result JSON 変換エラー: %w", err)
	}

	_, err = dynamoClient.PutItem(ctx, &dynamodb.PutItemInput{
		TableName: aws.String(dynamoTableName),
		Item: map[string]types.AttributeValue{
			"document_id": &types.AttributeValueMemberS{Value: documentID},
			"analyzed_at": &types.AttributeValueMemberS{Value: analyzedAt},
			"expires_at":  &types.AttributeValueMemberN{Value: fmt.Sprintf("%d", expiresAt)},
			"s3_bucket":   &types.AttributeValueMemberS{Value: bucket},
			"s3_key":      &types.AttributeValueMemberS{Value: key},
			"model_id":    &types.AttributeValueMemberS{Value: bedrockModelID},
			"status":      &types.AttributeValueMemberS{Value: "success"},
			"result":      &types.AttributeValueMemberS{Value: string(resultJSON)},
		},
	})
	return err
}

// ── 検証 ───────────────────────────────────────────────────

// validateRecord は S3 オブジェクトが処理対象かを判定する。
//
// 対象外の理由を文字列で返すのは、スキップ理由をそのまま
// レスポンスとログの双方に載せるため。
func validateRecord(key string, sizeBytes int64) (ok bool, reason string) {
	if !isAllowedExt(key) {
		return false, fmt.Sprintf("未対応の拡張子: %s", getExt(key))
	}
	sizeMB := float64(sizeBytes) / (1024 * 1024)
	if sizeMB > maxFileSizeMB {
		return false, fmt.Sprintf("ファイルサイズ超過: %.1fMB（上限 %dMB）", sizeMB, maxFileSizeMB)
	}
	return true, ""
}

// ── メトリクス ─────────────────────────────────────────────

// defaultMetricsNamespace は METRICS_NAMESPACE 未設定時に使う名前空間。
const defaultMetricsNamespace = "MultimodalAnalysis/Ingest"

// newMetricsFromEnv は環境変数から Metrics を生成する。
// 生成に失敗しても処理は続けたいので、エラー時は nil を返す。
func newMetricsFromEnv(logger *slog.Logger) *Metrics {
	ns := strings.TrimSpace(os.Getenv("METRICS_NAMESPACE"))
	if ns == "" {
		ns = defaultMetricsNamespace
	}
	m, err := NewMetrics(MetricsOptions{Namespace: ns})
	if err != nil {
		logger.Warn("メトリクスの初期化に失敗しました。計装なしで継続します", "error", err.Error())
		return nil
	}
	return m
}

// recordCount はカウントメトリクスを 1 件記録する。
//
// 記録に失敗しても処理は止めない。計装のために本体の可用性を
// 下げては本末転倒なため。
func recordCount(logger *slog.Logger, metrics *Metrics, name string, value float64) {
	if metrics == nil {
		return
	}
	if err := metrics.Count(name, value); err != nil {
		logger.Warn("メトリクスの記録に失敗しました", "metric", name, "error", err.Error())
	}
}

// ── 処理本体 ───────────────────────────────────────────────

// runHandler は S3 イベントを 1 件ずつ処理し、結果の一覧を返す。
//
// Handler からロガーとメトリクスの生成を切り離してあるため、
// テストではメモリ上の Writer を渡して出力まで検証できる。
func runHandler(
	ctx context.Context,
	logger *slog.Logger,
	metrics *Metrics,
	event events.S3Event,
) map[string]interface{} {
	logger.Info("S3 イベントを受信しました", "records", len(event.Records))
	recordCount(logger, metrics, "DocumentsReceived", float64(len(event.Records)))

	processed := make([]ProcessedRecord, 0, len(event.Records))
	var succeeded, skipped, failed int

	for _, record := range event.Records {
		bucket := record.S3.Bucket.Name
		key := record.S3.Object.Key
		documentID := fmt.Sprintf("%s/%s", bucket, key)
		recLogger := logger.With("bucket", bucket, "key", key)

		recLogger.Info("処理を開始しました")

		if ok, reason := validateRecord(key, record.S3.Object.Size); !ok {
			recLogger.Warn("対象外のファイルのためスキップしました", "reason", reason)
			processed = append(processed, ProcessedRecord{Key: key, Status: "skipped", Reason: reason})
			skipped++
			continue
		}

		fileBytes, err := getFileFromS3(ctx, bucket, key)
		if err != nil {
			recLogger.Error("S3 からの取得に失敗しました", "error", err.Error())
			processed = append(processed, ProcessedRecord{Key: key, Status: "error", Reason: err.Error()})
			failed++
			continue
		}
		recLogger.Debug("S3 からの取得が完了しました", "bytes", len(fileBytes))

		result, err := analyzeWithBedrock(ctx, fileBytes, key)
		if err != nil {
			recLogger.Error("Bedrock での解析に失敗しました", "error", err.Error())
			processed = append(processed, ProcessedRecord{Key: key, Status: "error", Reason: err.Error()})
			failed++
			continue
		}
		recLogger.Info("Bedrock での解析が完了しました", "fields", len(result))
		recordCount(logger, metrics, "ExtractedFields", float64(len(result)))

		if err := saveToDynamoDB(ctx, documentID, bucket, key, result); err != nil {
			recLogger.Error("DynamoDB への保存に失敗しました", "error", err.Error())
			processed = append(processed, ProcessedRecord{Key: key, Status: "error", Reason: err.Error()})
			failed++
			continue
		}

		recLogger.Info("処理が完了しました")
		processed = append(processed, ProcessedRecord{Key: key, Status: "success"})
		succeeded++
	}

	recordCount(logger, metrics, "DocumentsSucceeded", float64(succeeded))
	recordCount(logger, metrics, "DocumentsSkipped", float64(skipped))
	recordCount(logger, metrics, "DocumentsFailed", float64(failed))

	if failed > 0 {
		logger.Warn("処理に失敗したレコードがあります",
			"succeeded", succeeded, "skipped", skipped, "failed", failed)
	} else {
		logger.Info("すべてのレコードを処理しました",
			"succeeded", succeeded, "skipped", skipped, "failed", failed)
	}

	return map[string]interface{}{
		"statusCode": 200,
		"body":       processed,
	}
}

// ── Lambda ハンドラー ──────────────────────────────────────

func Handler(ctx context.Context, event events.S3Event) (map[string]interface{}, error) {
	logger := NewLoggerFromEnv(LoggerOptions{})
	metrics := newMetricsFromEnv(logger)

	resp := runHandler(ctx, logger, metrics, event)

	if metrics != nil {
		if err := metrics.Flush(); err != nil {
			logger.Warn("メトリクスの出力に失敗しました", "error", err.Error())
		}
	}
	return resp, nil
}

func main() {
	lambda.Start(Handler)
}
