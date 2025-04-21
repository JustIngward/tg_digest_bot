#!/usr/bin/env python3
"""IT‑Digest Telegram bot — v17.0 (2025‑04‑24)

• RSS‑only 50×10, async‑HTML, GPT‑ранж
• 4 секции: Global IT / RU Tech / 1C Экосистема / Events & Courses
• ≥ 40 % строк — 1С; ≥ 1 блок мероприятий, если найдены.
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
TG_TOKEN=os.getenv("TG_TOKEN"); CHAT_ID=os.getenv("CHAT_ID"); OPENAI_KEY=os.getenv("OPENAI_API_KEY")
assert TG_TOKEN and CHAT_ID and OPENAI_KEY, "TG_TOKEN, CHAT_ID, OPENAI_API_KEY required"
MODEL=os.getenv("MODEL","gpt-4o"); TZ=dt.timezone(dt.timedelta(hours=3))
MAX_DAYS, MAX_PER_FEED, MAX_HTML = 7, 50, 250
DIGEST_MIN, DIGEST_MAX = 8, 12; PERC_ONEC=0.4
client=OpenAI(); CUTOFF=dt.datetime.utcnow()-dt.timedelta(days=MAX_DAYS)

# ─── KEYS ───
ONEC_DOMAINS={"1c.ru","infostart.ru","odysseyconsgroup.com"}
ONEC_KEYS={"1с","1c","1с:erp","erp","зуп","унф","управление торговлей","бухгалтерия","wms"}
EVENT_KEYS={"конференц","форум","выставк","семинар","webinar","курс","devcon"}
INCLUDE=set(ONEC_KEYS)|{"ai","devops","облач","цифров","интеграц","миграц","kubernetes","crm","автоматиза"}
EXCLUDE={"crypto","iphone","lifestyle","шоколад","авто","банкомат"}

RSS_FEEDS=[
 "https://habr.com/ru/rss/all/all/?fl=ru","https://vc.ru/rss","https://www.rbc.ru/technology/rss/full/",
 "https://tadviser.ru/index.php/Статья:Новости?feed=rss","https://novostiitkanala.ru/feed/",
 "https://www.kommersant.ru/RSS/section-tech.xml","https://1c.ru/news/all.rss",
 "https://infostart.ru/rss/news/","https://trends.rbc.ru/trends.rss","https://rusbase.com/feed/",
]

# ─── helpers ───
def plain(html:str)->str: return BeautifulSoup(html,"html.parser").get_text(" ").lower()
async def fetch_html(url:str,cl:httpx.AsyncClient):
 try: r=await cl.get(url,timeout=6); return r.text if r.status_code==200 else None
 except: return None

def collect_raw():
 onec,other,events=[],[],[]
 for feed in RSS_FEEDS:
  try: fp=feedparser.parse(feed)
  except: continue
  for e in fp.entries[:MAX_PER_FEED]:
   link=e.get("link",""); title=e.get("title","")
   date_str=(e.get("published") or e.get("updated") or "")[:10]
   try: d=dt.datetime.strptime(date_str,"%Y-%m-%d")
   except: d=dt.datetime.utcnow()
   if d<CUTOFF: continue
   rec={"title":title,"url":link,"date":d.strftime("%d.%m.%Y"),"t":title.lower()}
   if any(k in rec["t"] for k in EVENT_KEYS): events.append(rec); continue
   (onec if urlparse(link).netloc in ONEC_DOMAINS or any(k in rec["t"] for k in ONEC_KEYS) else other).append(rec)
 return onec,other,events

def title_filter(lst):
 out=[]
 for a in lst:
  t=a["t"]
  if any(w in t for w in EXCLUDE): continue
  if any(k in t for k in INCLUDE): out.append(a)
 return out

async def body_filter(cand):
 subset=cand[:MAX_HTML]
 async with httpx.AsyncClient(follow_redirects=True,headers={"User-Agent":"Mozilla/5.0"}) as cl:
  pages=await asyncio.gather(*[fetch_html(a["url"],cl) for a in subset])
 out=[]
 for art,html in zip(subset,pages):
  if html and any(k in plain(html) for k in INCLUDE): out.append(art)
 return out

def gpt_rank(pool):
 prompt="Оцени по шкале 0‑10 важность новости для интегратора 1С. Ответ JSON {\"idx\":score}."
 mini=[{"idx":i,"title":a["title"],"url":a["url"]} for i,a in enumerate(pool)]
 resp=client.chat.completions.create(model=MODEL,messages=[{"role":"user","content":prompt+json.dumps(mini,ensure_ascii=False)}],temperature=0,max_tokens=300)
 try: scores=json.loads(resp.choices[0].message.content)
 except: scores={str(i):5 for i in range(len(pool))}
 return sorted(pool,key=lambda x:-scores.get(str(pool.index(x)),0))

def layout(onecs,others,events):
 need_onec=max(3,int(DIGEST_MAX*PERC_ONEC))
 news=(onecs[:need_onec]+others)[:DIGEST_MAX]
 evnts=events[:3]
 return news,evnts

# ─── prompt ───
def build_prompt(news,evnts):
 today=dt.datetime.now(TZ).strftime("%d %b %Y")
 return textwrap.dedent(f"""
  Ты — редактор B2B‑дайджеста для интеграторов 1С. Используй ТОЛЬКО данные JSON, ничего не выдумывай.
  Секции:
  🌍 <b>GLOBAL IT</b> (допускается строка “без глобальных тем”, если список пуст)
  🇷🇺 <b>RU TECH</b>
  🟡 <b>1С ЭКОСИСТЕМА</b>
  🎪 <b>Мероприятия & курсы</b>
  Требования: 8‑12 новостей (≥40 % про 1С) + до 3 мероприятий. Формат строки: «- <b>Заголовок</b> — 1–2 предложения. <a href=\"url\">Источник</a> (DD.MM.YYYY)».
  В конце блок "💡 <b>Insight</b>:" — 2 предложения.
  JSON_NEWS: ```{json.dumps(news,ensure_ascii=False)}```
  JSON_EVENTS: ```{json.dumps(evnts,ensure_ascii=False)}```
 """).strip()

# ─── sanitize/send ───

def sanitize(txt:str):
 txt=re.sub(r'href="([^"]+)"',lambda m:f'href="{m.group(1).replace("&","&amp;")}"',txt)
 parts=re.split(r'(<[^>]+>)',txt)
 return ''.join(p if p.startswith('<') else _html.escape(p) for p in parts)

def send(html:str):
 api=f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
 for i in range(0,len(html),3800):
  chunk=sanitize(html[i:i+3800])
  requests.post(api,json={"chat_id":CHAT_ID,"text":chunk,"parse_mode":"HTML"}).raise_for_status()

# ─── MAIN ───

def main():
 onec,other,events=collect_raw()
 onec=title_filter(onec)
 other=title_filter(other)
 events=title_filter(events)
 merged=onec+other
 filtered=asyncio.run(body_filter(merged))
 ranked=gpt_rank(filtered)
 news,evnts=layout([a for a in ranked if a in onec],[a for a in ranked if a in other],events)
 prompt=build_prompt(news,evnts)
 resp=client.chat.completions.create(model=MODEL,messages=[{"role":"user","content":prompt}],temperature=0.3,max_tokens=1200)
 send(resp.choices[0].message.content.strip())

if __name__=="__main__":
 main()
