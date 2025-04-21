#!/usr/bin/env python3
"""IT‑Digest Telegram bot — v15.0 (2025‑04‑22)

🔍  RSS‑only + анализ тела статьи
─────────────────────────────────
1.  Скачиваем *все* записи из RSS‑лент за последние N дней.  
2.  Отфильтровываем заголовком (ключевые слова).  
3.  Если дайджест ещё не набран → подтягиваем HTML страницы и ищем ключ‑слова в теле.  
4.  Приоритет 1С — минимум 2 пункта, если реально есть контент.  
5.  Отправляем HTML‑дайджест в Telegram.

Зависимости: `requests`, `feedparser`, `beautifulsoup4`, `python-dotenv`, `openai`.
"""
from __future__ import annotations

import os, re, json, datetime as dt, textwrap, requests, feedparser
from urllib.parse import urlparse
from bs4 import BeautifulSoup
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
MAX_DAYS         = int(os.getenv("MAX_DAYS", 7))
DIGEST_NEWS_CNT  = int(os.getenv("DIGEST_NEWS_CNT", 8))

client = OpenAI()

# ───── Ключевые слова ─────
ONEC_DOMAINS = {"1c.ru", "infostart.ru", "odysseyconsgroup.com"}
ONEC_KEYS = {
    "1с", "1c", "1‑с", "1-с", "1с:erp", "1с:предприятие", "wms", "зуп",
    "управление торговлей", "ут", "унф", "upp", "unf", "бухгалтерия", "odin es", "одинэс"
}

TECH_KEYWORDS = [
    "it", "ai", "искусствен", "цифров", "облач", "кибер", "безопас",
    "erp", "crm", "wms", "1с", "1c", "автоматиза", "интеграц", "миграц",
    "обновлен", "devops", "разработ", "api", "микросервис", "kubernetes",
] + list(ONEC_KEYS)

# ───── RSS‑ленты ─────
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

# ───── Helpers ─────
CUTOFF = dt.datetime.utcnow() - dt.timedelta(days=MAX_DAYS)
RE_TAG = re.compile(r"<[^>]+>")


def simple_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text(separator=" ").lower()


def has_kw(text: str) -> bool:
    return any(k in text for k in TECH_KEYWORDS)


def rss_fetch() -> list[dict]:
    arts: list[dict] = []
    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
        except Exception:
            continue
        for e in feed.entries:
            link  = getattr(e, "link", "")
            title = getattr(e, "title", "")
            date_str = getattr(e, "published", "")[:10]
            try:
                date_obj = dt.datetime.strptime(date_str, "%Y-%m-%d")
            except Exception:
                date_obj = CUTOFF
            if date_obj < CUTOFF:
                continue
            arts.append({"title": title, "url": link, "date": date_str})
    # сортировка новее‑вверх
    return sorted(arts, key=lambda a: a["date"], reverse=True)

# ───── Фильтрация 2‑этапа ─────

def filter_stage(arts: list[dict]) -> list[dict]:
    # 1. по заголовку
    flagged = [a for a in arts if any(k in a["title"].lower() for k in TECH_KEYWORDS)]
    if len(flagged) >= DIGEST_NEWS_CNT:
        return flagged[:DIGEST_NEWS_CNT]

    # 2. докидываем по содержимому
    for a in arts:
        if a in flagged:  # уже есть
            continue
        try:
            html = requests.get(a["url"], timeout=10).text
        except Exception:
            continue
        if has_kw(simple_text(html)):
            flagged.append(a)
        if len(flagged) >= DIGEST_NEWS_CNT:
            break
    return flagged

# ───── Приоритет 1С ─────

def is_onec(a: dict) -> bool:
    t = a["title"].lower()
    return any(k in t for k in ONEC_KEYS) or urlparse(a["url"]).netloc in ONEC_DOMAINS


def select_articles(all_articles: list[dict]) -> list[dict]:
    filtered = filter_stage(all_articles)
    onec = [a for a in filtered if is_onec(a)]
    other = [a for a in filtered if not is_onec(a)]
    # минимум 2 1С‑новости, если есть
    selected = (onec[:2] if len(onec) >= 2 else onec) + other
    return selected[:DIGEST_NEWS_CNT]

# ───── GPT‑Prompt & call ─────

def build_prompt(arts: list[dict]) -> str:
    today = dt.datetime.now(TZ).strftime("%d %b %Y")
    return textwrap.dedent(f"""
        На входе JSON статей (title, url, date). Составь дайджест HTML‑Markdown:
        • Три секции:
          🌍 <b>GLOBAL IT</b>\n🇷🇺 <b>RU TECH</b>\n🟡 <b>1С ЭКОСИСТЕМА</b>
        • Формат строки: "- <b>Заголовок</b> — 1‑2 предложения. <a href=\"url\">Источник</a> (DD.MM.YYYY)".
        • Если статей меньше {DIGEST_NEWS_CNT}, выводи столько, сколько есть.
        • В конце блок "💡 <b>Insight</b>:" — 2 предложения.
        JSON: ```{json.dumps(arts, ensure_ascii=False)}```
    """).strip()


def build_digest(prompt: str) -> str:
    r = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4,
        max_tokens=1000,
    )
    return r.choices[0].message.content.strip()

# ───── Telegram ─────

import html as _html, re as _re

def _sanitize(html_txt: str) -> str:
    """Экранируем & внутри href и < > внутри текста, оставляя теги <b>, <i>, <a>."""
    # 1) & в ссылках → &amp;
    def _amp(m):
        url = m.group(1).replace("&", "&amp;")
        return f'href="{url}"'
    html_txt = _re.sub(r'href="([^"]+)"', _amp, html_txt)
    # 2) Экранируем < и > вне тегов
    safe = []
    for part in _re.split(r'(<[^>]+>)', html_txt):
        if part.startswith('<'):
            safe.append(part)
        else:
            safe.append(_html.escape(part))
    return ''.join(safe)

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
        print("TG", r.status_code, r.text[:90])
        r.raise_for_status()(0, len(html), 3800):
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
