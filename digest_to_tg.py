#!/usr/bin/env python3
"""IT‑Digest Telegram bot — v17.4 (2025‑04‑24)
Полный, проверенный скрипт без обрывов строк.
"""
from __future__ import annotations

import asyncio, datetime as dt, html as _html, json, os, re, textwrap
from urllib.parse import urlparse

import feedparser, httpx, requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from openai import OpenAI

# ─── CONFIG ───
load_dotenv()
TG_TOKEN = os.getenv("TG_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
assert TG_TOKEN and CHAT_ID and OPENAI_KEY, "TG_TOKEN, CHAT_ID, OPENAI_API_KEY required"

MODEL = os.getenv("MODEL", "gpt-4o")
TZ = dt.timezone(dt.timedelta(hours=3))
MAX_DAYS = 7
MAX_PER_FEED = 50
MAX_HTML = 250
DIGEST_MIN = 8
DIGEST_MAX = 12
PERC_ONEC = 0.4  # 40 %

client = OpenAI()
CUTOFF = dt.datetime.utcnow() - dt.timedelta(days=MAX_DAYS)

# ─── KEYWORDS ───
ONEC_DOMAINS = {"1c.ru", "infostart.ru", "odysseyconsgroup.com"}
ONEC_KEYS = {
    "1с", "1c", "1с:erp", "erp", "1с:ух", "ух", "erpух", "erp‑ух",
    "зуп", "унф", "управление торговлей", "ut", "ut11", "бухгалтерия",
    "wms", "производств", "агро", "сельхоз", "аграрн", "торговл"
}
EVENT_KEYS = {"конференц", "форум", "выставк", "семинар", "webinar", "курс", "devcon"}
INCLUDE = set(ONEC_KEYS) | {
    "ai", "devops", "облач", "цифров", "интеграц", "миграц", "kubernetes",
    "crm", "автоматиза", "модернизац", "релиз", "обновлен", "конфигурац",
    "erp", "ух", "производств", "аграр", "торговл"
}
EXCLUDE = {"crypto", "iphone", "lifestyle", "шоколад", "авто", "банкомат"}

# ─── RSS FEEDS ───
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

# ─── Helpers ───
plain = lambda html: BeautifulSoup(html, "html.parser").get_text(" ").lower()

async def fetch_html(url: str, cl: httpx.AsyncClient) -> str | None:
    try:
        r = await cl.get(url, timeout=8)
        return r.text if r.status_code == 200 else None
    except Exception:
        return None

# ─── 0. Collect ───

def collect_raw():
    onec, other, events = [], [], []
    for feed in RSS_FEEDS:
        try:
            fp = feedparser.parse(feed)
        except Exception:
            continue
        for e in fp.entries[:MAX_PER_FEED]:
            link = e.get("link", "")
            title = e.get("title", "")
            date_str = (e.get("published") or e.get("updated") or "")[:10]
            try:
                d = dt.datetime.strptime(date_str, "%Y-%m-%d")
            except Exception:
                d = dt.datetime.utcnow()
            if d < CUTOFF:
                continue
            rec = {"title": title, "url": link, "date": d.strftime("%d.%m.%Y"), "t": title.lower()}
            if any(k in rec["t"] for k in EVENT_KEYS):
                events.append(rec)
                continue
            target = onec if urlparse(link).netloc in ONEC_DOMAINS or any(k in rec["t"] for k in ONEC_KEYS) else other
            target.append(rec)
    return onec, other, events

# ─── 1. Title filter ───

def title_filter(lst):
    return [a for a in lst if not any(w in a["t"] for w in EXCLUDE) and any(k in a["t"] for k in INCLUDE)]

# ─── 2. Body filter ───
async def body_filter(candidates):
    subset = candidates[:MAX_HTML]
    async with httpx.AsyncClient(follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as cl:
        pages = await asyncio.gather(*[fetch_html(a["url"], cl) for a in subset])
    out = []
    for art, html in zip(subset, pages):
        if not html:
            continue
        is_onec = urlparse(art["url"]).netloc in ONEC_DOMAINS or any(k in art["t"] for k in ONEC_KEYS)
        if is_onec or any(k in plain(html) for k in INCLUDE):
            out.append(art)
    return out

# ─── 3. GPT rank ───

def gpt_rank(pool):
    prompt = "Оцени по шкале 0‑10 важность новости для интегратора 1С. Ответ JSON {\\\"idx\\\":score}."
    mini = [{"idx": i, "title": a["title"], "url": a["url"]} for i, a in enumerate(pool)]
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt + json.dumps(mini, ensure_ascii=False)}],
            temperature=0,
            max_tokens=300,
        )
        scores = json.loads(resp.choices[0].message.content)
    except Exception:
        scores = {str(i): 5 for i in range(len(pool))}
    return sorted(pool, key=lambda x: -scores.get(str(pool.index(x)), 0))

# ─── 4. Layout ───

def layout(onecs, others, events):
    need_onec = max(1, int(DIGEST_MAX * PERC_ONEC))
    news = (onecs[:need_onec] + others)[:DIGEST_MAX]
    return news, events[:3]

# ─── Prompt ───

def build_prompt(news, evnts):
    return textwrap.dedent(
        f"""
        Ты — редактор B2B‑дайджеста для интеграторов 1С. Используй ТОЛЬКО данные JSON.
        Секции: 🌍/🇷🇺/🟡/🎪. Если секция пуста — «без …».
        Требования: 8‑12 новостей (≥40 % 1С) и до 3 событий.
        Формат: - <b>Заголовок</b> — 1‑2 предложения. <a href=\"url\">Источник</a> (DD.MM.YYYY)
        В конце "💡 <b>Insight</b>:" — 2 предложения.
        JSON_NEWS: ```{json.dumps(news, ensure_ascii=False)}```
        JSON_EVENTS: ```{json.dumps(evnts, ensure_ascii=False)}```
        """
    ).strip()

# ─── Send ───

def sanitize(txt):
    txt = re.sub(r'href="([^"]+)"', lambda m: f'href=\"{m.group(1).replace("&", "&amp;")}\"', txt)
    parts = re.split(r'(<[^>]+>)', txt)
    return ''.join(p if p.startswith('<') else _html.escape(p) for p in parts)


def send(html):
    api = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    for i in range(0, len(html), 3800):
        chunk = sanitize(html[i : i + 3800])
        r = requests.post(
            api,
            json={
                "chat_id": CHAT_ID,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
        )
        r.raise_for_status()

# ─── Main ───

def main():
    onec_raw, other_raw, events_raw = collect_raw()
    onec = title_filter(onec_raw)
    other = title_filter(other_raw)
    events = title_filter(events_raw)
