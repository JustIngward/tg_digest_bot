#!/usr/bin/env python3
"""IT‑Digest Telegram bot — v14.1 (2025‑04‑22)

📡  RSS‑only, приоритет 1С, без дубликатов кода‑ошибок
"""
from __future__ import annotations

import os, json, datetime as dt, textwrap, requests, feedparser
from urllib.parse import urlparse
from dotenv import load_dotenv
from openai import OpenAI

# ───── CONFIG ─────
load_dotenv()
TG_TOKEN   = os.getenv("TG_TOKEN")
CHAT_ID    = os.getenv("CHAT_ID")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
assert TG_TOKEN and CHAT_ID and OPENAI_KEY, "TG_TOKEN, CHAT_ID, OPENAI_API_KEY required"

MODEL            = os.getenv("MODEL", "gpt-4o")
TZ               = dt.timezone(dt.timedelta(hours=3))
MAX_DAYS         = int(os.getenv("MAX_DAYS", 7))        # за сколько дней берём RSS
DIGEST_NEWS_CNT  = int(os.getenv("DIGEST_NEWS_CNT", 8))

client = OpenAI()

# ───── Ключевые слова / домены ─────
ONEC_DOMAINS = {"1c.ru", "infostart.ru", "odysseyconsgroup.com"}
ONEC_KEYS = {
    "1с", "1c", "1‑с", "1-с", "1с:erp", "1с:предприятие", "erp", "wms",
    "зуп", "управление торговлей", "ут", "унф", "upp", "unf", "бухгалтерия",
    "odin es", "одинэс"
}

TECH_KEYWORDS = [
    "it", "ai", "искусствен", "цифров", "облач", "кибер", "безопас",
    "erp", "crm", "wms", "1с", "1c", "автоматиза", "интеграц", "миграц",
    "обновлен", "devops", "разработ", "api", "микросервис", "kubernetes"
]

# ───── RSS‑ленты ─────
RSS_FEEDS = [
    # Tech / IT Russia
    "https://habr.com/ru/rss/all/all/?fl=ru",
    "https://vc.ru/rss",
    "https://www.rbc.ru/technology/rss/full/",
    "https://tadviser.ru/index.php/Статья:Новости?feed=rss",
    "https://novostiitkanala.ru/feed/",
    "https://www.kommersant.ru/RSS/section-tech.xml",
    # Экосистема 1С
    "https://1c.ru/news/all.rss",
    "https://infostart.ru/rss/news/",
    # Бизнес / аналитика
    "https://trends.rbc.ru/trends.rss",
    "https://rusbase.com/feed/",
]

# ───── Парсинг RSS ─────

def _relevant(title: str) -> bool:
    t = title.lower()
    return any(k in t for k in TECH_KEYWORDS)


def rss_fetch() -> list[dict]:
    cutoff = dt.datetime.utcnow() - dt.timedelta(days=MAX_DAYS)
    arts: list[dict] = []
    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
        except Exception:
            continue
        for e in feed.entries:
            link = getattr(e, "link", "")
            title = getattr(e, "title", "")
            if not _relevant(title):
                continue
            date_str = getattr(e, "published", "")[:10]
            try:
                date_obj = dt.datetime.strptime(date_str, "%Y-%m-%d")
            except Exception:
                date_obj = cutoff
            if date_obj < cutoff:
                continue
            arts.append({"title": title, "url": link, "date": date_str})
    return sorted(arts, key=lambda a: a["date"], reverse=True)

# ───── Приоритет 1С ─────

def is_onec(art: dict) -> bool:
    t = art["title"].lower()
    return any(k in t for k in ONEC_KEYS) or urlparse(art["url"]).netloc in ONEC_DOMAINS


def select_articles(all_articles: list[dict]) -> list[dict]:
    onec = [a for a in all_articles if is_onec(a)]
    other = [a for a in all_articles if not is_onec(a)]
    # минимум 2 новости 1С если есть
    selected = (onec[:2] if len(onec) >= 2 else onec) + other
    return selected[:DIGEST_NEWS_CNT]

# ───── GPT‑Prompt ─────

def build_prompt(arts: list[dict]) -> str:
    today = dt.datetime.now(TZ).strftime("%d %b %Y")
    return textwrap.dedent(f"""
        На входе JSON статей (title, url, date). Составь дайджест HTML‑Markdown:
        • Три секции с пустой строкой:
          🌍 <b>GLOBAL IT</b>\n🇷🇺 <b>RU TECH</b>\n🟡 <b>1С ЭКОСИСТЕМА</b>
        • Формат: "- <b>Заголовок</b> — 1‑2 предложения. <a href=\"url\">Источник</a> (DD.MM.YYYY)".
        • Если статей меньше {DIGEST_NEWS_CNT} — выводи сколько есть.
        • В конце блок "💡 <b>Insight</b>:" — 2 предложения.
        JSON: ```{json.dumps(arts, ensure_ascii=False)}```
    """).strip()

# ───── GPT call ─────

def build_digest(prompt: str) -> str:
    r = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4,
        max_tokens=1000,
    )
    return r.choices[0].message.content.strip()

# ───── Telegram ─────

def send_telegram(html: str):
    api = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    for i in range(0, len(html), 3800):
        chunk = html[i:i+3800]
        r = requests.post(api, json={
            "chat_id": CHAT_ID,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }, timeout=20)
        print("TG", r.status_code, r.text[:70])
        r.raise_for_status()

# ───── MAIN ─────

def main():
    arts = select_articles(rss_fetch())
    digest = build_digest(build_prompt(arts))
    send_telegram(digest)
    print(f"Digest sent with {len(arts)} articles ✔︎")

if __name__ == "__main__":
    main()
