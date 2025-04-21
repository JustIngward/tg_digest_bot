#!/usr/bin/env python3
"""IT‑Digest Telegram bot — v15.2 (2025‑04‑22)

• RSS‑only
• Двухэтапный фильтр (title + HTML)
• Приоритет 1С (≥ 2 пункта, если доступны)
• HTML‑safe отправка в Telegram
"""
from __future__ import annotations

import os, re, json, datetime as dt, textwrap, requests, feedparser, html as _html
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from openai import OpenAI

# ───── CONFIG ─────
load_dotenv()
TG_TOKEN = os.getenv("TG_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
assert TG_TOKEN and CHAT_ID and OPENAI_KEY, "TG_TOKEN, CHAT_ID, OPENAI_API_KEY required"

MODEL = os.getenv("MODEL", "gpt-4o")
TZ = dt.timezone(dt.timedelta(hours=3))
MAX_DAYS = int(os.getenv("MAX_DAYS", 7))
DIGEST_NEWS_CNT = int(os.getenv("DIGEST_NEWS_CNT", 8))

client = OpenAI()

# ───── KEYWORDS ─────
ONEC_DOMAINS = {"1c.ru", "infostart.ru", "odysseyconsgroup.com"}
ONEC_KEYS = {
    "1с", "1c", "1‑с", "1-с", "1с:erp", "1с:предприятие", "wms", "зуп",
    "управление торговлей", "ут", "унф", "upp", "unf", "бухгалтерия", "odin es", "одинэс"
}
TECH_INCLUDE = [
    "1с", "1c", "erp", "crm", "wms", "зуп", "devops", "kubernetes", "облач",
    "цифров", "интеграци", "миграци", "автоматиза", "искусствен", "ai",
]
TECH_EXCLUDE = [
    "iphone", "crypto", "биткоин", "ethereum", "самолет", "авто", "электромобил",
    "шоколад", "фисташк", "биржа", "lifestyle", "здоровье", "банкомат",
]

# ───── RSS LIST ─────
RSS_FEEDS = [
    "https://habr.com/ru/rss/all/all/?fl=ru",
    "https://vc.ru/rss",
    "https://www.rbc.ru/technology/rss/full/",
    "https://tadviser.ru/index.php/Статья:Новости?feed=rss",
    "https://novostiitkanala.ru/feed/",
    "https://www.kommersant.ru/RSS/section-tech.xml",
    "https://1c.ru/news/all.rss",
    "https://infostart.ru/rss/news/",
    "https://trends.rbc.ru/trends.rss",
    "https://rusbase.com/feed/",
]

CUTOFF = dt.datetime.utcnow() - dt.timedelta(days=MAX_DAYS)

# ───── FETCH & FILTER ─────

def _plain(html: str) -> str:
    return BeautifulSoup(html, "html.parser").get_text(" ").lower()

def _hit(text: str) -> bool:
    if any(w in text for w in TECH_EXCLUDE):
        return False
    return any(k in text for k in TECH_INCLUDE) or any(k in text for k in ONEC_KEYS)


def rss_fetch() -> list[dict]:
    out = []
    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
        except Exception:
            continue
        for e in feed.entries:
            link, title = e.get("link", ""), e.get("title", "")
            date_str = (e.get("published") or e.get("updated") or e.get("dc_date") or "")[:10]
            try:
                date_obj = dt.datetime.strptime(date_str, "%Y-%m-%d")
            except Exception:
                date_obj = dt.datetime.utcnow()  # считай свежей
            if date_obj < CUTOFF:
                continue
            out.append({
                "title": title,
                "url": link,
                "date": date_obj.strftime("%d.%m.%Y"),
                "_t": title.lower(),
            })
    return sorted(out, key=lambda a: a["date"], reverse=True)


def filter_stage(arts: list[dict]) -> list[dict]:
    first = [a for a in arts if _hit(a["_t"])]
    if len(first) >= DIGEST_NEWS_CNT:
        return first[:DIGEST_NEWS_CNT]
    for a in arts:
        if a in first:
            continue
        try:
            page = requests.get(a["url"], timeout=10).text
        except Exception:
            continue
        if _hit(_plain(page)):
            first.append(a)
        if len(first) >= DIGEST_NEWS_CNT:
            break
    return first


def is_onec(a: dict) -> bool:
    return (any(k in a["title"].lower() for k in ONEC_KEYS) or
            urlparse(a["url"]).netloc in ONEC_DOMAINS)


def select_articles(all_arts: list[dict]) -> list[dict]:
    arts = filter_stage(all_arts)
    onec = [a for a in arts if is_onec(a)]
    other = [a for a in arts if not is_onec(a)]
    sel = (onec[:2] if len(onec) >= 2 else onec) + other
    return sel[:DIGEST_NEWS_CNT]

# ───── GPT ─────

def build_prompt(arts: list[dict]) -> str:
    today = dt.datetime.now(TZ).strftime("%d %b %Y")
    return textwrap.dedent(f"""
        На входе JSON статей (title, url, date). Составь дайджест HTML‑Markdown c тремя секциями:
        🌍 <b>GLOBAL IT</b>\n🇷🇺 <b>RU TECH</b>\n🟡 <b>1С ЭКОСИСТЕМА</b>
        Формат: "- <b>Заголовок</b> — 1‑2 предложения. <a href=\"url\">Источник</a> (DD.MM.YYYY)".
        Если статей меньше {DIGEST_NEWS_CNT}, выводи столько, сколько есть.
        В конце блок "💡 <b>Insight</b>:" — 2 предложения.
        JSON: ```{json.dumps(arts, ensure_ascii=False)}```
    """).strip()


def build_digest(prompt: str) -> str:
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4,
        max_tokens=1000,
    )
    return resp.choices[0].message.content.strip()

# ───── TELEGRAM ─────

def _sanitize(html_txt: str) -> str:
    html_txt = re.sub(r'href="([^"]+)"', lambda m: f'href="{m.group(1).replace("&", "&amp;")}"', html_txt)
    parts = re.split(r'(<[^>]+>)', html_txt)
    return ''.join(p if p.startswith('<') else _html.escape(p) for p in parts)


def send_telegram(html: str):
    api = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    for i in range(0, len(html), 3800):
        chunk = _sanitize(html[i:i+3800])
        r = requests.post(api, json={
            "chat_id": CHAT_ID,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }, timeout=20)
        print("TG", r.status_code, r.text[:80])
        r.raise_for_status()

# ───── MAIN ─────

def main():
    arts = select_articles(rss_fetch())
    digest = build_digest(build_prompt(arts))
    send_telegram(digest)
    print(f"Digest sent with {len(arts)} articles ✔︎")

if __name__ == "__main__":
    main()
