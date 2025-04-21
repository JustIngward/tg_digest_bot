#!/usr/bin/env python3
"""IT‑Digest Telegram bot — v1.0 (2025‑04‑22, rewrite)

▶  Минималистичный «с нуля»
──────────────────────────
▪ Python собирает новости (NewsAPI + RSS) → одно обращение к GPT‑4o
  формирует готовый Markdown‑дайджест.
▪ Без циклов tool‑calling, без сложных regex: проверяем только кол‑во пунктов.
▪ SQLite‑память оставили, но можно выключить `USE_DB=0`.
"""

from __future__ import annotations

import os, datetime as dt, json, sqlite3, textwrap, requests, feedparser
from urllib.parse import urlparse
from dotenv import load_dotenv
from openai import OpenAI

# ───── CONFIG ─────
load_dotenv()
TZ               = dt.timezone(dt.timedelta(hours=3))
MODEL            = os.getenv("MODEL", "gpt-4o")
OPENAI_KEY       = os.getenv("OPENAI_API_KEY")
NEWS_API_KEY     = os.getenv("NEWS_API_KEY")
TG_TOKEN         = os.getenv("TG_TOKEN")
CHAT_ID          = os.getenv("CHAT_ID")
MAX_DAYS         = int(os.getenv("MAX_DAYS", 10))
MAX_ARTICLES     = int(os.getenv("MAX_ARTICLES", 30))
DIGEST_NEWS_CNT  = int(os.getenv("DIGEST_NEWS_CNT", 12))  # итоговых строк
USE_DB           = int(os.getenv("USE_DB", 1))
SQLITE_PATH      = os.getenv("DB_PATH", "sent.db")

assert all([OPENAI_KEY, TG_TOKEN, CHAT_ID, NEWS_API_KEY]), "Missing env vars"

client = OpenAI()

WHITELIST = {
    "cnews.ru", "tadviser.ru", "vc.ru", "rbc.ru", "gazeta.ru",
    "1c.ru", "infostart.ru", "odysseyconsgroup.com",
    "rusbase.ru", "trends.rbc.ru"
}

RSS_FEEDS = [
    "https://habr.com/ru/rss/all/all/?fl=ru",
    "https://1c.ru/news/all.rss",
    "https://infostart.ru/rss/news/",
    "https://vc.ru/rss",
]

# ───── DB helpers ─────
if USE_DB:
    conn = sqlite3.connect(SQLITE_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS sent(url TEXT PRIMARY KEY)")
else:
    conn = None

def already_sent(url: str) -> bool:
    if not conn:
        return False
    return conn.execute("SELECT 1 FROM sent WHERE url=?", (url,)).fetchone() is not None

def mark_sent(url: str):
    if conn:
        conn.execute("INSERT OR IGNORE INTO sent(url) VALUES (?)", (url,))
        conn.commit()

# ───── COLLECT NEWS ─────

def newsapi_fetch(q: str) -> list[dict]:
    url = "https://newsapi.org/v2/everything"
    params = {
        "q": q,
        "language": "ru",
        "from": (dt.datetime.utcnow() - dt.timedelta(days=MAX_DAYS)).isoformat(),
        "pageSize": MAX_ARTICLES,
        "sortBy": "publishedAt",
        "domains": ",".join(WHITELIST),
        "apiKey": NEWS_API_KEY,
    }
    data = requests.get(url, params=params, timeout=12).json()
    return [{
        "title": a["title"],
        "url": a["url"],
        "date": a["publishedAt"][:10]
    } for a in data.get("articles", [])]


def rss_fetch(keywords: list[str]) -> list[dict]:
    res = []
    for feed in RSS_FEEDS:
        parsed = feedparser.parse(feed)
        for e in parsed.entries:
            title = e.get("title", "")
            if all(k in title.lower() for k in keywords):
                url = e.link
                res.append({"title": title, "url": url, "date": e.get("published", "")[:10]})
    return res


def gather_articles() -> list[dict]:
    topics = ["it", "российский it", "1с"]
    pool = {}
    for t in topics:
        for art in newsapi_fetch(t) + rss_fetch(t.split()):
            if already_sent(art["url"]):
                continue
            pool[art["url"]] = art
            if len(pool) >= MAX_ARTICLES:
                break
    # сортировка по дате
    articles = sorted(pool.values(), key=lambda a: a["date"], reverse=True)
    return articles[:MAX_ARTICLES]

# ───── GPT PROMPT ─────

def build_prompt(articles: list[dict]) -> str:
    today = dt.datetime.now(TZ).strftime("%d %b %Y")
    json_blob = json.dumps(articles, ensure_ascii=False)
    return textwrap.dedent(f"""
        Ты — профессиональный новостной редактор.
        На вход получаешь JSON со статьями (title, url, date).
        Отбери {DIGEST_NEWS_CNT} самых значимых для рынка IT РФ и экосистемы 1С.
        Сформируй Markdown‑дайджест:
        - Каждая строка: "- **Заголовок** — 1 предложение сути. [Источник](url) (DD.MM.YYYY)".
        - Заголовок документа: "🗞️ **IT‑Digest • {today}**".
        - В конце блок «💡 Insight» — 2 предложения о главной тенденции.
        Всегда соблюдай формат.
        JSON статей: ```{json_blob}```
    """)

# ───── BUILD DIGEST ─────

def build_digest(md_prompt: str) -> str:
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": md_prompt}],
        temperature=0.4,
        max_tokens=1024,
    )
    return resp.choices[0].message.content.strip()

# ───── TELEGRAM ─────

def send_tg(text: str):
    api = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    for chunk in (text[i:i+3800] for i in range(0, len(text), 3800)):
        resp = requests.post(api, json={
            "chat_id": CHAT_ID,
            "text": chunk,
            "parse_mode": "MarkdownV2",
            "disable_web_page_preview": False,
        }, timeout=15)
        resp.raise_for_status()

# ───── MAIN ─────

def main():
    arts = gather_articles()
    if len(arts) < DIGEST_NEWS_CNT:
        raise RuntimeError("Недостаточно свежих статей")
    md = build_digest(build_prompt(arts))
    send_tg(md)
    for a in arts[:DIGEST_NEWS_CNT]:
        mark_sent(a["url"])
    print("Digest sent ✔︎")

if __name__ == "__main__":
    main()
