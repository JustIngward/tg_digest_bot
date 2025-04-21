#!/usr/bin/env python3
"""IT‑Digest Telegram bot — v12.0 (2025‑04‑22)

📡  Теперь + RSS‑ленты (Habr, 1C.ru, Infostart…) к NewsAPI
──────────────────────────────────────────────────────────
*  `fetch_news()` ⇒ сначала NewsAPI, потом (если мало) парсит RSS‑feeds.
*  RSS‑список задаётся переменной `RSS_FEEDS` (можно расширять без кода).
*  Используем `feedparser` (добавь в requirements.txt).
"""

from __future__ import annotations

import os, re, sqlite3, time, datetime as dt, requests, textwrap, json, feedparser
from urllib.parse import urlparse
from dotenv import load_dotenv
from openai import OpenAI

# ───── CONFIG ─────
load_dotenv()
TZ                = dt.timezone(dt.timedelta(hours=3))
MODEL             = os.getenv("MODEL", "gpt-4o")
TEMPERATURE       = float(os.getenv("TEMPERATURE", 0.7))
MAX_AGE_DAYS      = int(os.getenv("MAX_AGE_DAYS", 10))
MIN_NEWS_LINES    = int(os.getenv("MIN_NEWS_LINES", 6))
MAX_RETRIES       = int(os.getenv("MAX_RETRIES", 3))
MAX_FETCH         = int(os.getenv("MAX_FETCH", 25))
FALLBACK_MIN      = int(os.getenv("FALLBACK_MIN", 3))
SQLITE_PATH       = os.getenv("DB_PATH", "sent_news.db")
TG_TOKEN          = os.environ["TG_TOKEN"]
CHAT_ID           = os.environ["CHAT_ID"]
NEWS_API_KEY      = os.getenv("NEWS_API_KEY")
if not NEWS_API_KEY:
    raise EnvironmentError("NEWS_API_KEY is not set — добавь ключ NewsAPI в Secrets / .env")

WHITELIST = {
    "cnews.ru", "tadviser.ru", "vc.ru", "rbc.ru", "gazeta.ru",
    "1c.ru", "infostart.ru", "odysseyconsgroup.com",
    "rusbase.ru", "trends.rbc.ru", "novostiitkanala.ru", "triafly.ru",
}

RSS_FEEDS = [
    "https://habr.com/ru/rss/all/all/?fl=ru",
    "https://1c.ru/news/all.rss",
    "https://infostart.ru/rss/news/",
    "https://vc.ru/rss",
]

client = OpenAI()

# ───── DB helpers ─────
SCHEMA = """CREATE TABLE IF NOT EXISTS sent (
    fp TEXT PRIMARY KEY,
    ts DATETIME DEFAULT CURRENT_TIMESTAMP
);"""

def db_conn():
    conn = sqlite3.connect(SQLITE_PATH)
    conn.execute(SCHEMA)
    return conn

def fp(url: str) -> str:
    return str(hash(url))

def already_sent(url: str) -> bool:
    with db_conn() as c:
        return c.execute("SELECT 1 FROM sent WHERE fp=?", (fp(url),)).fetchone() is not None

def mark_sent(url: str):
    with db_conn() as c:
        c.execute("INSERT OR IGNORE INTO sent(fp) VALUES (?)", (fp(url),))

# ───── Tool: fetch_news (NewsAPI + RSS) ─────

def _newsapi_request(topic: str, use_domains: bool) -> list[dict]:
    url = "https://newsapi.org/v2/everything"
    from_date = (dt.datetime.utcnow() - dt.timedelta(days=MAX_AGE_DAYS)).isoformat("T", "seconds")
    params = {
        "q": topic,
        "from": from_date,
        "language": "ru",
        "pageSize": MAX_FETCH,
        "sortBy": "publishedAt",
        "apiKey": NEWS_API_KEY,
    }
    if use_domains:
        params["domains"] = ",".join(WHITELIST)
    try:
        data = requests.get(url, params=params, timeout=12).json()
        return data.get("articles", [])
    except Exception:
        return []


def _rss_request(topic: str) -> list[dict]:
    res = []
    keywords = topic.lower().split()
    for feed_url in RSS_FEEDS:
        fp_data = feedparser.parse(feed_url)
        for entry in fp_data.entries:
            if len(res) >= MAX_FETCH:
                return res
            title = entry.get("title", "")
            if all(k in title.lower() for k in keywords):
                link = entry.link
                res.append({
                    "title": title,
                    "url": link,
                    "published": entry.get("published", "")[:10],
                    "source": urlparse(link).netloc,
                    "description": (entry.get("summary") or "")[:200],
                })
    return res


def fetch_news(topic: str, n: int = 5):
    # 1) NewsAPI with whitelist
    articles = _newsapi_request(topic, True)
    # 2) NewsAPI open domains
    if len(articles) < FALLBACK_MIN:
        articles += _newsapi_request(topic, False)
    # 3) RSS feeds
    if len(articles) < FALLBACK_MIN:
        articles += _rss_request(topic)
    unique = {}
    for art in articles:
        url = art["url"]
        if url not in unique:
            unique[url] = art
        if len(unique) >= n:
            break
    return list(unique.values())

# ───── PROMPT / tools / regex — без изменений ─────

def build_prompt() -> str:
    today = dt.datetime.now(TZ).strftime("%d %b %Y")
    return textwrap.dedent(f"""
        Ты — русский IT‑аналитик. Используй fetch_news для поиска статей.
        • Формат: "- **Заголовок** — 1–2 предложения. [Источник](URL) (DD.MM.YYYY)".
        • Секции: 🌍 **ГЛОБАЛЬНЫЙ IT**, 🇷🇺 **РОССИЙСКИЙ TECH**, 🟡 **ЭКОСИСТЕМА 1С**.
        • Минимум {MIN_NEWS_LINES} новостей. После ссылки «(вне WL)» если домен не в whitelist.
        • Закрой дайджест блоком Insight.
        • Заголовок: "🗞️ **IT‑Digest • {today}**".
    """).strip()

TOOLS = [{
    "type": "function",
    "function": {
        "name": "fetch_news",
        "description": "Возвращает свежие статьи (NewsAPI+RSS)",
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {"type": "string"},
                "n": {"type": "integer", "default": 6},
            },
            "required": ["topic"]
        }
    }
}]

FUNCTIONS = {"fetch_news": fetch_news}

NEWS_RE = re.compile(r"^\s*[-*]\s+\*\*.+?\*\*.+\[(?P<text>.*?)\]\((?P<url>https?://[^)\s]+)\).+")

# chat_digest, validate, send_tg, main — остаются прежними
