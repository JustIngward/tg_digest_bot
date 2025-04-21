#!/usr/bin/env python3
"""IT‑Digest Telegram bot — v16.0 (2025‑04‑22)

🆕  «50×10» RSS‑захват → async‑HTML → GPT‑ранж
──────────────────────────────────────────────
*  **Широкий сбор**: до 50 элементов из каждой ленты (≈500).
*  **Баланс 40 % 1С**: два отдельных пула, добор «хвостом» из other.
*  **Двухуровневый отбор**
   1. fast — фильтр по title (include/exclude ключи).
   2. slow — async скрейп HTML (httpx, 20 коннектов).
*  **GPT‑ранж**: один вызов 4o возвращает score 0‑10; берём верхнюю половину.
*  Итог — 8‑12 строк, ≥ 40 % 1С (мин 3 пункта).
*  Жёстко: «используй ТОЛЬКО факты из входного JSON, не придумывай».  
*  HTML‑safe отправка.
"""
from __future__ import annotations

import os, re, json, asyncio, datetime as dt, textwrap, html as _html
from collections import defaultdict
from urllib.parse import urlparse

import feedparser, httpx, python_dotenv
from bs4 import BeautifulSoup
from openai import OpenAI

# ───── CONFIG ─────
python_dotenv.load_dotenv()
TG_TOKEN  = os.getenv("TG_TOKEN")
CHAT_ID   = os.getenv("CHAT_ID")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
assert TG_TOKEN and CHAT_ID and OPENAI_KEY, "env vars missing"

MODEL      = os.getenv("MODEL", "gpt-4o")
TZ         = dt.timezone(dt.timedelta(hours=3))
MAX_DAYS   = 7
MAX_PER_FEED = 50
MAX_HTML   = 250
DIGEST_MIN = 8
DIGEST_MAX = 12
PERC_ONEC  = 0.4   # 40 %

client = OpenAI()

# ───── KEYS ─────
ONEC_DOMAINS = {"1c.ru", "infostart.ru", "odysseyconsgroup.com"}
ONEC_KEYS = {"1с", "1c", "1с:erp", "erp", "зуп", "унф", "управление торговлей", "бухгалтерия"}
INCLUDE = set(ONEC_KEYS)|{"ai","devops","облач","цифров","интеграц","миграц","kubernetes","crm","wms"}
EXCLUDE = {"crypto","iphone","шоколад","lifestyle","авто","биржа","банкомат"}

# ───── RSS FEEDS ─────
RSS_FEEDS = [
    "https://habr.com/ru/rss/all/all/?fl=ru","https://vc.ru/rss","https://www.rbc.ru/technology/rss/full/",
    "https://tadviser.ru/index.php/Статья:Новости?feed=rss","https://novostiitkanala.ru/feed/",
    "https://www.kommersant.ru/RSS/section-tech.xml","https://1c.ru/news/all.rss",
    "https://infostart.ru/rss/news/","https://trends.rbc.ru/trends.rss","https://rusbase.com/feed/",
]
CUTOFF = dt.datetime.utcnow() - dt.timedelta(days=MAX_DAYS)

# ───── helpers ─────
RE_TAG = re.compile(r"<[^>]+>")

def plain(html:str)->str:
    return BeautifulSoup(html,"html.parser").get_text(" ").lower()

async def fetch_html(url:str, client:httpx.AsyncClient)->str|None:
    try:
        r = await client.get(url, timeout=6)
        if r.status_code==200:
            return r.text
    except Exception:
        return None

# ───── stage 0 : collect 50 per feed ─────

def collect_raw():
    onec_pool, other_pool = [], []
    for feed in RSS_FEEDS:
        try:
            fp = feedparser.parse(feed)
        except Exception:
            continue
        count=0
        for e in fp.entries:
            if count>=MAX_PER_FEED: break
            link=e.get("link","")
            title=e.get("title","")
            date_str=(e.get("published") or e.get("updated") or "")[:10]
            try:
                d=dt.datetime.strptime(date_str,"%Y-%m-%d")
            except: d=dt.datetime.utcnow()
            if d<CUTOFF: continue
            rec={"title":title,"url":link,"date":d.strftime("%d.%m.%Y"),"t":title.lower()}
            (onec_pool if any(dom in link for dom in ONEC_DOMAINS) or any(k in rec["t"] for k in ONEC_KEYS) else other_pool).append(rec)
            count+=1
    return onec_pool, other_pool

# ───── stage 1 : title filter — keep relevant ─────

def title_filter(lst):
    out=[]
    for a in lst:
        t=a["t"]
        if any(x in t for x in EXCLUDE):
            continue
        if any(k in t for k in INCLUDE):
            out.append(a)
    return out

# ───── stage 2 : async body filter ─────

async def body_filter(candidates:list[dict]):
    selected=candidates.copy()
    if len(selected)>=MAX_HTML: selected=selected[:MAX_HTML]
    async with httpx.AsyncClient(follow_redirects=True, headers={"User-Agent":"Mozilla/5.0"}) as c:
        tasks=[fetch_html(a["url"],c) for a in selected]
        pages=await asyncio.gather(*tasks)
    out=[]
    for a,html in zip(selected,pages):
        if html and any(k in plain(html) for k in INCLUDE):
            out.append(a)
    return out

# ───── stage 3 : GPT ranking ─────

def gpt_rank(pool:list[dict]):
    prompt="Оцени по шкале 0‑10 важность новости для интегратора 1С. Ответ JSON вида {\"idx\":score}. Не добавляй ничего.""
    mini=[{"idx":i,"title":a["title"],"url":a["url"]} for i,a in enumerate(pool)]
    resp=client.chat.completions.create(model=MODEL,messages=[{"role":"user","content":prompt+json.dumps(mini,ensure_ascii=False)}],temperature=0,max_tokens=300)
    try:
        scores=json.loads(resp.choices[0].message.content)
    except: scores={str(i):5 for i in range(len(pool))}
    ranked=sorted(pool,key=lambda x:-scores.get(str(pool.index(x)),0))
    return ranked[:DIGEST_MAX*2]  # оставим запас

# ───── layout ─────

def layout(arts:list[dict]):
    onec=[a for a in arts if any(k in a["t"] for k in ONEC_KEYS) or urlparse(a["url"]).netloc in ONEC_DOMAINS]
    other=[a for a in arts if a not in onec]
    need_onec=max(3,int(DIGEST_MAX*PERC_ONEC))
    final=(onec[:need_onec]+other)[:DIGEST_MAX]
    return final

# ───── prompt / digest / send (reuse v15.2) ─────

def build_prompt(arts):
    today=dt.datetime.now(TZ).strftime("%d %b %Y")
    return textwrap.dedent(f"""
        Ты — редактор B2B‑дайджеста для интеграторов 1С. Используй ТОЛЬКО данные JSON, не выдумывай.
        Требования: 8‑12 строк; секции 🌍/🇷🇺/🟡; 40 % строк про 1С; формат "- <b>Заголовок</b> — …".
        В конце блок Insight.
        JSON: ```{json.dumps(arts,ensure_ascii=False)}```
    """)

def sanitize(html_txt:str):
    html_txt=re.sub(r'href="([^"]+)"',lambda m:f'href="{m.group(1).replace("&","&amp;")}"',html_txt)
    parts=re.split(r'(<[^>]+>)',html_txt)
    return ''.join(p if p.startswith('<') else _html.escape(p) for p in parts)


def send(html:str):
    api=f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    for i in range(0,len(html),3800):
        chunk=sanitize(html[i:i+3800])
        r=requests.post(api,json={"chat_id":CHAT_ID,"text":chunk,"parse_mode":"HTML"})
        r.raise_for_status()

# ───── MAIN ─────

def main():
    pool_onec,pool_other=collect_raw()
    pool_onec=title_filter(pool_onec)
    pool_other=title_filter(pool_other)
    # баланс
    target_onec=int(PERC_ONEC*500)
    pool_other=pool_other[:500-len(pool_onec)]
    merged=pool_onec+pool_other
    # html stage
    filtered=asyncio.run(body_filter(merged))
    ranked=gpt_rank(filtered)
    digest_list=layout(ranked)
    prompt=build_prompt(digest_list)
    result=client.chat.completions.create(model=MODEL,messages=[{"role":"user","content":prompt}],temperature=0.3,max_tokens=1200)
    send(result.choices[0].message.content.strip())

if __name__=="__main__":
    main()
