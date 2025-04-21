#!/usr/bin/env python3
"""IT‑Digest Telegram bot — v13.0 (2025‑04‑22)

▲  Чистый минимал, правки по итогам теста
─────────────────────────────────────────
•  Формируем новости из NewsAPI (RU‑white‑list) **+** расширенный набор RSS.  
•  Дедуп + легкий «diverse» фильтр: стараемся взять разные домены.  
•  GPT‑4o получает готовый JSON и сам бьёт на секции.  
•  Отправка в Telegram через HTML — теги `<b>/<i>` работают, всё остальное экранируется.
"""

from __future__ import annotations

import os, json, sqlite3, datetime as dt, textwrap, html, re, requests, feedparser
from urllib.parse import urlparse
from dotenv import load_dotenv
from openai import OpenAI

# ───── CONFIG ─────
load_dotenv()
OPENAI_KEY  = os.getenv("OPENAI_API_KEY")
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
TG_TOKEN    = os.getenv("TG_TOKEN")
CHAT_ID     = os.getenv("CHAT_ID")
assert all([OPENAI_KEY, NEWS_API_KEY, TG_TOKEN, CHAT_ID]), "Missing env vars"

MODEL            = os.getenv("MODEL", "gpt-4o")
TZ               = dt.timezone(dt.timedelta(hours=3))
MAX_DAYS         = int(os.getenv("MAX_DAYS", 10))
MAX_ARTICLES     = int(os.getenv("MAX_ARTICLES", 40))
DIGEST_NEWS_CNT  = int(os.getenv("DIGEST_NEWS_CNT", 12))
SQLITE_PATH      = os.getenv("DB_PATH", "sent.db")
USE_DB           = int(os.getenv("USE_DB", 1))

client = OpenAI()

WHITELIST = {
    "cnews.ru", "tadviser.ru", "vc.ru", "rbc.ru", "gazeta.ru",
    "1c.ru", "infostart.ru", "odysseyconsgroup.com",
    "rusbase.ru", "trends.rbc.ru", "novostiitkanala.ru", "triafly.ru",
}

RSS_FEEDS = [
    # tech/it RU
    "https://habr.com/ru/rss/all/all/?fl=ru",
    "https://vc.ru/rss",
    "https://www.rbc.ru/technology/rss/full/",
    "https://tadviser.ru/index.php/Статья:Новости?feed=rss",
    "https://novostiitkanala.ru/feed/",
    # 1С‑сообщество
    "https://1c.ru/news/all.rss",
    "https://infostart.ru/rss/news/",
]

# ───── DB helpers ─────
if USE_DB:
    db = sqlite3.connect(SQLITE_PATH)
    db.execute("CREATE TABLE IF NOT EXISTS sent(url TEXT PRIMARY KEY)")
else:
    db = None

def already_sent(url: str) -> bool:
    if not db: return False
    return db.execute("SELECT 1 FROM sent WHERE url=?", (url,)).fetchone() is not None

def mark_sent(url: str):
    if db:
        db.execute("INSERT OR IGNORE INTO sent(url) VALUES (?)", (url,))
        db.commit()

# ───── COLLECT NEWS ─────

def newsapi_fetch() -> list[dict]:
    url = "https://newsapi.org/v2/everything"
    params = {
        "q": " ",
        "language": "ru",
        "from": (dt.datetime.utcnow() - dt.timedelta(days=MAX_DAYS)).isoformat(),
        "pageSize": MAX_ARTICLES,
        "sortBy": "publishedAt",
        "domains": ",".join(WHITELIST),
        "apiKey": NEWS_API_KEY,
    }
    try:
        data = requests.get(url, params=params, timeout=12).json()
    except Exception:
        return []
    return [{"title": a["title"], "url": a["url"], "date": a["publishedAt"][:10]} for a in data.get("articles", [])]


def rss_fetch() -> list[dict]:
    res = []
    for feed in RSS_FEEDS:
        try:
            parsed = feedparser.parse(feed)
        except Exception:
            continue
        for e in parsed.entries:
            link = e.link
            res.append({
                "title": e.get("title", ""),
                "url": link,
                "date": e.get("published", "")[:10],
            })
    return res


def diverse(arts: list[dict], want: int) -> list[dict]:
    seen = set()
    out  = []
    for a in arts:
        dom = urlparse(a["url"]).netloc
        if dom in seen and len(out) < want - 3:
            continue
        seen.add(dom)
        out.append(a)
        if len(out) == want:
            break
    return out


def gather_articles() -> list[dict]:
    pool = {}
    for art in newsapi_fetch() + rss_fetch():
        url = art["url"]
        if already_sent(url):
            continue
        pool[url] = art
        if len(pool) >= MAX_ARTICLES:
            break
    arts = sorted(pool.values(), key=lambda a: a["date"], reverse=True)
    return diverse(arts, DIGEST_NEWS_CNT)

# ───── GPT PROMPT ─────

def build_prompt(arts: list[dict]) -> str:
    today = dt.datetime.now(TZ).strftime("%d %b %Y")
    return textwrap.dedent(f"""
        На входе JSON статей (title, url, date). Выведи дайджест Markdown‑HTML:
        • Раздели на три секции с пустой строкой:
          🌍 <b>GLOBAL IT</b>\n🇷🇺 <b>RU TECH</b>\n🟡 <b>1С ЭКОСИСТЕМА</b>
        • Строка: "- <b>Заголовок</b> — одно предложение. <a href=\"url\">Источник</a> (DD.MM.YYYY)".
        • Всего {DIGEST_NEWS_CNT} новостей.
        • В конце блок "💡 <b>Insight</b>:" — 2 предложения.
        Санитайзируй HTML. JSON: ```{json.dumps(arts, ensure_ascii=False)}```
    """).strip()

# ───── GPT call ─────

def build_digest(prompt: str) -> str:
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4,
        max_tokens=1100,
    )
    return resp.choices[0].message.content.strip()

# ───── Telegram (HTML) ─────

def send_tg(text: str):
    def chunker(s, n=3800):
        for i in range(0, len(s), n):
            yield s[i:i + n]
    api = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    for ch in chunker(text):
        payload = {
            "chat_id": CHAT_ID,
            "text": ch,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }
        r = requests.post(api, json=payload, timeout=15)
        print("TG", r.status_code, r.text[:100])
        r.raise_for_status()

# ───── Main ─────

def main():
    arts = gather_articles()
    if len(arts) < DIGEST_NEWS_CNT:
        raise RuntimeError("Недостаточно статей")
    digest = build_digest(build_prompt(arts))
    send_tg(digest)
    for a in arts:
        mark_sent(a["url"])
    print("Digest sent ✔︎")

if __name__ == "__main__":
    main()
