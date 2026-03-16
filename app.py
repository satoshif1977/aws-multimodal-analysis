"""
aws-multimodal-analysis: 業務文書解析 Streamlit Web UI
起動: aws-vault exec personal-dev-source -- streamlit run app.py
"""
import json
import time

import boto3
import streamlit as st
from botocore.exceptions import ClientError

# ── ページ設定 ───────────────────────────────────────────────
st.set_page_config(
    page_title="業務文書 AI 解析",
    page_icon="📄",
    layout="wide",
)

# ── サイドバー設定 ───────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ 設定")
    s3_bucket = st.text_input(
        "S3 バケット名",
        help="terraform output s3_bucket_name で確認",
    )
    dynamodb_table = st.text_input(
        "DynamoDB テーブル名",
        value="multimodal-dev-results",
        help="terraform output dynamodb_table_name で確認",
    )
    aws_region = st.selectbox(
        "リージョン",
        options=["ap-northeast-1", "us-east-1"],
    )
    st.divider()
    st.caption("aws-multimodal-analysis PoC")
    st.caption("S3 → Lambda → Bedrock → DynamoDB")

# ── メイン画面 ───────────────────────────────────────────────
st.title("📄 業務文書 AI 解析")
st.caption("S3 にアップロードするだけで Bedrock（Claude）が自動解析 → DynamoDB に構造化データ保存")

if not s3_bucket:
    st.warning("サイドバーで S3 バケット名を設定してください。")
    st.info("💡 `terraform output s3_bucket_name` で確認できます。")
    st.stop()

# ── AWS クライアント ─────────────────────────────────────────
s3_client = boto3.client("s3", region_name=aws_region)
dynamodb = boto3.resource("dynamodb", region_name=aws_region)

# ── タブ構成 ────────────────────────────────────────────────
tab_upload, tab_results = st.tabs(["📤 ファイルアップロード", "📊 解析結果一覧"])

# ────────────────────────────────────────────────────────────
# タブ1: ファイルアップロード
# ────────────────────────────────────────────────────────────
with tab_upload:
    st.subheader("文書をアップロードして解析")
    st.markdown("""
    対応ファイル: **PNG / JPG / PDF**（最大 5MB）

    ファイルをアップロードすると S3 経由で Lambda が自動起動し、
    Bedrock（Claude）がマルチモーダル解析を行います。
    """)

    uploaded_file = st.file_uploader(
        "ファイルを選択してください",
        type=["png", "jpg", "jpeg", "pdf"],
        help="請求書・見積書・報告書など業務文書の画像または PDF",
    )

    s3_prefix = st.selectbox(
        "フォルダ（プレフィックス）",
        options=["uploads/", "invoices/", "estimates/", "reports/"],
        help="ファイル名に invoice / estimate が含まれると専用プロンプトで解析されます",
    )

    if uploaded_file is not None:
        st.image(uploaded_file, caption=uploaded_file.name, width=400) if uploaded_file.type.startswith("image") else st.info(f"📄 {uploaded_file.name}（PDF）")

        col1, col2 = st.columns([1, 3])
        with col1:
            upload_btn = st.button("🚀 解析開始", type="primary")

        if upload_btn:
            s3_key = f"{s3_prefix}{uploaded_file.name}"
            with st.spinner(f"S3 にアップロード中... → Lambda 自動起動 → Bedrock 解析中..."):
                try:
                    s3_client.upload_fileobj(
                        uploaded_file,
                        s3_bucket,
                        s3_key,
                    )
                    st.success(f"✅ アップロード完了: `s3://{s3_bucket}/{s3_key}`")
                    st.info("⏳ Lambda が解析中です。10〜30秒後に「解析結果一覧」タブで結果を確認してください。")

                    # 自動リフレッシュのカウントダウン表示
                    progress = st.progress(0)
                    for i in range(20):
                        time.sleep(1)
                        progress.progress((i + 1) / 20)
                    progress.empty()
                    st.success("✅ 解析完了（推定）。「解析結果一覧」タブで確認してください。")

                except ClientError as e:
                    st.error(f"S3 アップロードエラー: {e}")

# ────────────────────────────────────────────────────────────
# タブ2: 解析結果一覧
# ────────────────────────────────────────────────────────────
with tab_results:
    st.subheader("DynamoDB 解析結果一覧")

    col_refresh, col_count = st.columns([1, 3])
    with col_refresh:
        refresh = st.button("🔄 最新に更新")

    try:
        table = dynamodb.Table(dynamodb_table)
        response = table.scan()
        items = response.get("Items", [])

        with col_count:
            st.metric("解析済み件数", len(items))

        if not items:
            st.info("解析結果がありません。ファイルをアップロードしてください。")
        else:
            # 新しい順にソート
            items_sorted = sorted(items, key=lambda x: x.get("analyzed_at", ""), reverse=True)

            for item in items_sorted:
                doc_id = item.get("document_id", "不明")
                status = item.get("status", "不明")
                analyzed_at = item.get("analyzed_at", "")[:19].replace("T", " ")
                result = item.get("result", {})

                # ステータスアイコン
                icon = "✅" if status == "success" else "❌"
                file_name = doc_id.split("/")[-1]

                with st.expander(f"{icon} {file_name}　（{analyzed_at}）"):
                    col_l, col_r = st.columns(2)

                    with col_l:
                        st.markdown("**📋 基本情報**")
                        st.write(f"- **ファイル**: `{doc_id}`")
                        st.write(f"- **ステータス**: {status}")
                        st.write(f"- **解析日時**: {analyzed_at}")
                        st.write(f"- **モデル**: {item.get('model_id', '不明')}")

                    with col_r:
                        st.markdown("**🤖 AI 解析結果**")
                        if "raw_text" in result:
                            # JSON パース失敗時のフォールバック
                            st.text_area("解析テキスト", result["raw_text"], height=200, key=doc_id)
                        else:
                            # 構造化データとして表示
                            doc_type = result.get("document_type", "")
                            if doc_type:
                                st.write(f"- **文書種別**: {doc_type}")

                            for key, label in [
                                ("invoice_number", "請求書番号"),
                                ("estimate_number", "見積番号"),
                                ("issue_date", "発行日"),
                                ("due_date", "支払期日"),
                                ("valid_until", "有効期限"),
                                ("vendor_name", "請求元/見積元"),
                                ("total_amount", "金額"),
                                ("currency", "通貨"),
                            ]:
                                if key in result and result[key] is not None:
                                    st.write(f"- **{label}**: {result[key]}")

                            # 明細テーブル
                            items_list = result.get("items", [])
                            if items_list:
                                st.markdown("**明細**")
                                st.table([
                                    {
                                        "品目": i.get("description", ""),
                                        "数量": i.get("quantity", ""),
                                        "単価": i.get("unit_price", ""),
                                        "金額": i.get("amount", ""),
                                    }
                                    for i in items_list
                                ])

                    # JSON 全体を折りたたみで表示
                    with st.expander("📦 JSON 全データ"):
                        st.json(result)

    except ClientError as e:
        st.error(f"DynamoDB エラー: {e}")
    except Exception as e:
        st.error(f"エラー: {e}")
