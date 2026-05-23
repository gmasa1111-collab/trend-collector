import json
import urllib.request
import xml.etree.ElementTree as ET
import os
from datetime import datetime, timezone, timedelta
from google import genai  # 💡 Google公式の最新ライブラリをインポート

# ──────────────────────────────────────────────
# 設定：環境変数から読み込む
# ──────────────────────────────────────────────
GEMINI_API_KEY  = os.environ.get("GEMINI_API_KEY", "")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")

# 監視するRSSフィード一覧
RSS_FEEDS = [
    {"name": "Towards Data Science", "url": "https://towardsdatascience.com/feed"},
    {"name": "Data Mesh Radio (Medium)", "url": "https://medium.com/feed/tag/data-mesh"},
    {"name": "DATAVERSITY", "url": "https://www.dataversity.net/feed/"},
]

# データマネジメント関連キーワード（含まれていない記事は除外）
KEYWORDS = [
    "data governance", "data mesh", "data management", "data quality",
    "data catalog", "master data", "data lineage", "data fabric",
    "データガバナンス", "データマネジメント", "データ品質", "データカタログ",
]

MAX_ARTICLES = 5  # 1回の実行で要約する記事の最大数


# ──────────────────────────────────────────────
# RSSを取得して記事リストを返す
# ──────────────────────────────────────────────
def fetch_rss(feed_url: str, feed_name: str) -> list:
    articles = []
    try:
        req = urllib.request.Request(
            feed_url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; TrendBot/1.0)"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read()

        root = ET.fromstring(content)
        ns = {"atom": "http://www.w3.org/2005/Atom"}

        # RSS 2.0 形式
        for item in root.findall(".//item"):
            title = item.findtext("title", "").strip()
            link  = item.findtext("link", "").strip()
            desc  = item.findtext("description", "").strip()
            articles.append({
                "source": feed_name,
                "title": title,
                "link": link,
                "description": desc[:500],
            })

        # Atom 形式（RSS 2.0で取れなかった場合）
        if not articles:
            for entry in root.findall("atom:entry", ns):
                title = entry.findtext("atom:title", "", ns).strip()
                link_el = entry.find("atom:link", ns)
                link = link_el.get("href", "") if link_el is not None else ""
                summary = entry.findtext("atom:summary", "", ns).strip()
                articles.append({
                    "source": feed_name,
                    "title": title,
                    "link": link,
                    "description": summary[:500],
                })

    except Exception as e:
        print(f"[ERROR] RSS取得失敗: {feed_name} / {e}")

    return articles


# ──────────────────────────────────────────────
# キーワードフィルタリング
# ──────────────────────────────────────────────
def is_relevant(article: dict) -> bool:
    text = (article["title"] + " " + article["description"]).lower()
    return any(kw.lower() in text for kw in KEYWORDS)


# ──────────────────────────────────────────────
# Gemini API で記事を要約（公式SDKによる自動リトライ）
# ──────────────────────────────────────────────
def summarize_with_gemini(articles: list) -> str:
    articles_text = ""
    for i, a in enumerate(articles, 1):
        articles_text += (
            f"【記事{i}】\n"
            f"タイトル: {a['title']}\n"
            f"ソース: {a['source']}\n"
            f"URL: {a['link']}\n"
            f"概要: {a['description']}\n\n"
        )

    prompt = f"""あなたはデータマネジメント分野のコンサルタントをサポートするアシスタントです。
以下の記事を読み、日本語で簡潔にまとめてください。

出力形式：
- 各記事を1〜2行で要約
- データマネジメント観点での重要ポイントを末尾に1行添える
- 絵文字を使って読みやすくする

記事一覧：
{articles_text}"""

    # 💡 公式クライアントを初期化（自動でリトライやスマートなエラー処理を行ってくれます）
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    response = client.models.generate_content(
        model='gemini-2.0-flash',
        contents=prompt,
    )

    return response.text


# ──────────────────────────────────────────────
# Slack に投稿
# ──────────────────────────────────────────────
def post_to_slack(summary: str, articles: list):
    jst = timezone(timedelta(hours=9))
    today = datetime.now(jst).strftime("%Y年%m月%d日")

    links_text = "\n".join(
        [f"• <{a['link']}|{a['title']}>" for a in articles]
    )

    message = {
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"📊 データマネジメント トレンドまとめ｜{today}"
                }
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": summary}
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*📎 元記事リンク*\n{links_text}"
                }
            }
        ]
    }

    payload = json.dumps(message).encode("utf-8")
    req = urllib.request.Request(
        SLACK_WEBHOOK_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        print(f"[Slack] ステータス: {resp.status}")


# ──────────────────────────────────────────────
# メイン処理
# ──────────────────────────────────────────────
def main():
    print("=== トレンド収集 開始 ===")

    all_articles = []
    for feed in RSS_FEEDS:
        articles = fetch_rss(feed["url"], feed["name"])
        print(f"  {feed['name']}: {len(articles)}件取得")
        all_articles.extend(articles)

    relevant = [a for a in all_articles if is_relevant(a)]
    print(f"関連記事: {len(relevant)}件 / 全{len(all_articles)}件")

    if not relevant:
        print("関連記事なし。終了。")
        return

    target = relevant[:MAX_ARTICLES]

    print("Gemini APIで要約中...")
    summary = summarize_with_gemini(target)

    print("Slackに投稿中...")
    post_to_slack(summary, target)

    print(f"=== 完了：{len(target)}件投稿 ===")


if __name__ == "__main__":
    main()
