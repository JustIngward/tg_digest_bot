#!/usr/bin/env python3
"""IT‑Digest Telegram bot — v17.1 (2025‑04‑24)

Фиксы после пустого дайджеста:
• body‑filter больше НЕ отбрасывает 1С‑статьи, даже если в HTML нет ключа.
• need_onec ≥ 1 (а не 3) — дайджест соберётся, даже если 1С‑контента мало.
• fallback: если после всех фильтров < DIGEST_MIN строк → отправляем короткое сообщение «за неделю нет релевантных новостей» и выходим без ошибки.
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
TG_TOKEN = os.getenv("TG_TOKEN"); CHAT_ID = os.getenv("CHAT_ID"); OPENAI_KEY = os.getenv("OPENAI_API_KEY")
assert TG_TOKEN and CHAT_ID and OPENAI_KEY, "TG_TOKEN, CHAT_ID, OPENAI_API_KEY required"
MODEL = os.getenv("MODEL", "gpt-4o"); TZ = dt.timezone(dt.timedelta(hours=3))
MAX_DAYS, MAX_PER_FEED, MAX_HTML = 7, 50, 250
DIGEST_MIN, DIGEST_MAX = 8, 12; PERC_ONEC = 0.4
client = OpenAI(); CUTOFF = dt.datetime.utcnow() - dt.timedelta(days=MAX_DAYS)

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

RSS_FEEDS = [
    "https://habr.com/ru/rss/all/all/?fl=ru", "https://vc.ru/rss", "https://www.rbc.ru/technology/rss/full/",
    "https://tadviser.ru/index.php/Статья:Новости?feed=rss", "https://novostiitkanala.ru/feed/",
    "https://www.kommersant.ru/RSS/section-tech.xml", "https://1c.ru/news/all.rss",
    "https://infostart.ru/rss/news/", "https://trends.rbc.ru/trends.rss", "https://rusbase.com/feed/",
]

# ─── helpers ───
plain = lambda html: BeautifulSoup(html, "html.parser").get_text(" ").lower()
async def fetch_html(url, cl):
    try:
        r = await cl.get(url, timeout=8)
        return r.text if r.status_code == 200 else None
    except Exception:
        return None

# ─── 0. COLLECT ───

def collect_raw():
    onec, other, events = [], [], []
    for feed in RSS_FEEDS:
        try:
            fp = feedparser.parse(feed)
        except Exception:
            continue
        for e in fp.entries[:MAX_PER_FEED]:
            link, title = e.get("link", ""), e.get("title", "")
            date_str = (e.get("published") or e.get("updated") or "")[:10]
            try:
                d = dt.datetime.strptime(date_str, "%Y-%m-%d")
            except Exception:
                d = dt.datetime.utcnow()
            if d < CUTOFF:
                continue
            rec = {"title": title, "url": link, "date": d.strftime("%d.%m.%Y"), "t": title.lower()}
            if any(k in rec["t"] for k in EVENT_KEYS):
                events.append(rec); continue
            (onec if urlparse(link).netloc in ONEC_DOMAINS or any(k in rec["t"] for k in ONEC_KEYS) else other).append(rec)
    return onec, other, events

# ─── 1. TITLE FILTER ───

def title_filter(lst):
    return [a for a in lst if not any(w in a["t"] for w in EXCLUDE) and any(k in a["t"] for k in INCLUDE)]

# ─── 2. BODY FILTER ───
async def body_filter(cand):
    subset = cand[:MAX_HTML]
    async with httpx.AsyncClient(follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as cl:
        pages = await asyncio.gather(*[fetch_html(a["url"], cl) for a in subset])
    out = []
    for art, html in zip(subset, pages):
        is_onec = urlparse(art["url"]).netloc in ONEC_DOMAINS or any(k in art["t"] for k in ONEC_KEYS)
        if not html:
            continue
        if is_onec or any(k in plain(html) for k in INCLUDE):
            out.append(art)
    return out

# ─── 3. GPT RANK ───

def gpt_rank(pool):
    prompt = "Оцени по шкале 0‑10 важность новости для интегратора 1С. Ответ JSON {\"idx\":score}."
    mini = [{"idx": i, "title": a["title"], "url": a["url"]} for i, a in enumerate(pool)]
    try:
        resp = client.chat.completions.create(model=MODEL, messages=[{"role": "user", "content": prompt + json.dumps(mini, ensure_ascii=False)}], temperature=0, max_tokens=300)
        scores = json.loads(resp.choices[0].message.content)
    except Exception:
        scores = {str(i): 5 for i in range(len(pool))}
    return sorted(pool, key=lambda x: -scores.get(str(pool.index(x)), 0))

# ─── 4. LAYOUT ───

def layout(news_onec, news_other, events):
    need_onec = max(1, int(DIGEST_MAX * PERC_ONEC))
    news = (news_onec[:need_onec] + news_other)[:DIGEST_MAX]
    return news, events[:3]

# ─── PROMPT ───

def build_prompt(news, evnts):
    today = dt.datetime.now(TZ).strftime("%d %b %Y")
    return textwrap.dedent(f"""
        Ты — редактор B2B‑дайджеста для интеграторов 1С. Используй ТОЛЬКО данные JSON, ничего не выдумывай.
        Секции: 🌍/🇷🇺/🟡/🎪. Если секция пуста — вставь строку «без …».
        Требования: 8‑12 новостей (≥40 % 1С) + до 3 событий. Формат строки: - <b>Заголовок</b> — 1‑2 предложения. <a href=\"url\">Источник</a> (DD.MM.YYYY)
        В конце "💡 <b>Insight</b>:" — 2 предложения.
        JSON_NEWS: ```{json.dumps(news, ensure_ascii=False)}```
        JSON_EVENTS: ```{json.dumps(evnts, ensure_ascii=False)}```
    """).strip()

# ─── SEND ───

def sanitize(txt):
    txt = re.sub(r'href="([^"]+)"', lambda m: f'href=\"{m.group(1).replace("&", "&amp;")}\"', txt)
    parts = re.split(r'(<[^>]+>)', txt)
    return ''.join(p if p.startswith('<') else _html.escape(p) for p in parts)


def send(html):
    if not html.strip():
        return
    api = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    for i in range(0, len(html), 3800):
        chunk = sanitize(html[i:i + 3800])
        requests.post(api, json={"
