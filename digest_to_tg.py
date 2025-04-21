#!/usr/bin/env python3
"""IT‑Digest Telegram bot — v7.1 (2025‑04‑21)

Fix: OpenAI 400 «unsupported value ‘temperature’»
───────────────────────────────────────────────
Модели семейства **o3** не принимают параметр `temperature` в chat/response
энд‑поинтах, поэтому вынесли его полностью.

Изменения v7.1
‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾
* Удалён `temperature` из `client.responses.create` (Collector) и
  `client.chat.completions.create` (Scorer).
* Переменная `TEMP_GEN` оставлена «про запас», но не передаётся.
* Остальная логика (скoring ≥2, HEAD‑смягчение, Insight, dedup) без изменений.
"""
import os, re, sqlite3, datetime as dt, time, hashlib, requests
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode, urlparse
from dotenv import load_dotenv
from openai import OpenAI

# ───────── CONFIG ─────────
load_dotenv()
TZ               = dt.timezone(dt.timedelta(hours=3))
MODEL            = os.getenv("MODEL", "o3")
MAX_AGE_HOURS    = int(os.getenv("MAX_AGE_HOURS", 168))
MIN_NEWS         = int(os.getenv("MIN_NEWS", 6))
MIN_NEWS_SOFT    = int(os.getenv("MIN_NEWS_SOFT", 3))
MAX_ITER         = int(os.getenv("MAX_ITER", 6))
TG_TOKEN         = os.environ["TG_TOKEN"]
CHAT_ID          = os.environ["CHAT_ID"]
ALLOWED_DOMAINS  = [d.strip().lower() for d in os.getenv("ALLOWED_DOMAINS", "").split(',') if d.strip()]

client = OpenAI()

NEWS_RE     = re.compile(r"^\s*[-*]\s*(?:\*\*)?.+?\]\((https?://[^)\s]+)\)\s*\((\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?\)")
SECTION_RE  = re.compile(r"^\s*(🌍|🇷🇺|🟡)")
SECT_ORDER  = {"🌍": 0, "🇷🇺": 1, "🟡": 2}

# ───── HELPERS ─────

def strip_utm(u:str)->str:
    p=list(urlsplit(u))
    p[3]=urlencode([(k,v) for k,v in parse_qsl(p[3]) if not k.startswith('utm_')])
    return urlunsplit(p)

def allowed_domain(u:str)->bool:
    if not ALLOWED_DOMAINS:
        return True
    host=(urlparse(u).hostname or "").lower()
    return any(host==d or host.endswith('.'+d) for d in ALLOWED_DOMAINS)

def fresh(d:int,m:int,y:int|None)->bool:
    y=y or dt.datetime.now(TZ).year
    pub=dt.datetime(y,m,d,tzinfo=TZ)
    return (dt.datetime.now(TZ)-pub).total_seconds()<=MAX_AGE_HOURS*3600

def head_ok(u:str)->bool:
    try:
        return requests.head(u,allow_redirects=True,timeout=5).status_code<400
    except requests.RequestException:
        return False

def md5u(u:str)->str:
    return hashlib.md5(u.encode()).hexdigest()

# ───── PROMPT ─────

def make_prompt(today:str)->str:
    days=MAX_AGE_HOURS//24
    wl = f"домены: {', '.join(ALLOWED_DOMAINS)}" if ALLOWED_DOMAINS else "любой проверенный сайт"
    return (
        f"Ты — IT‑аналитик. Сформируй черновик дайджеста. Правила:\n"
        f"• Новости ≤ {days} дней, {wl}.\n"
        "• Формат: - **Заголовок** — суть. [Источник](URL) (DD.MM.YYYY)\n"
        "• Секции: 🌍, 🇷🇺, 🟡 с пустой строкой между ними.\n"
        f"• Минимум {MIN_NEWS} строк. Insight в конце."
    )

# ───── SCORER ─────

def relevance_score(line:str)->int:
    q=f"Оцени полезность новости для IT‑специалиста (0‑5). Ответ одно число.\nНовость: {line}"
    r=client.chat.completions.create(model=MODEL,messages=[{"role":"user","content":q}])
    try:
        return int(r.choices[0].message.content.strip()[:1])
    except:
        return 0

# ───── COLLECTOR ─────

def collect_once()->str:
    today=dt.datetime.now(TZ).strftime('%d %b %Y')
    r=client.responses.create(model=MODEL,tools=[{"type":"web_search"}],input=[{"role":"user","content":make_prompt(today)}],store=False)
    return r.output_text

# ───── VALIDATOR ─────

def validate(raw:str, dedup:set):
    buckets={0:[],1:[],2:[]}; insight=""
    for ln in raw.splitlines():
        if ln.startswith('💡') or ln.lower().startswith('insight'):
            insight=ln.strip(); continue
        m=NEWS_RE.search(ln)
        sect=SECT_ORDER.get((SECTION_RE.match(ln) or ['🌍'])[0]) if m else None
        if not (m and sect is not None):
            continue
        url,day,mon,year=m.group(1),int(m.group(2)),int(m.group(3)),m.group(4)
        year=int(year) if year else None
        url=strip_utm(url); h=md5u(url)
        if h in dedup or not fresh(day,mon,year):
            continue
        score=relevance_score(ln)
        if not head_ok(url): score=max(0,score-1)
        if score<2: continue
        buckets[sect].append((score, ln.replace(m.group(1), url)))
        dedup.add(h)
    out=[f"🗞️ **IT‑Digest • {dt.datetime.now(TZ).strftime('%d %b %Y')}**",""]
    for s in range(3):
        out.append(list(SECT_ORDER.keys())[list(SECT_ORDER.values()).index(s)]+' ')
        out.extend([ln for _,ln in sorted(buckets[s],key=lambda t:t[0],reverse=True)])
        out.append('')
    if insight:
        out.append(insight)
    return [ln for ln in out if ln.strip() or ln=='']

# ───── PIPELINE ─────

def produce_digest():
    db=sqlite3.connect('sent_hashes.db'); db.execute('CREATE TABLE IF NOT EXISTS sent(hash TEXT PRIMARY KEY)')
    seen={h for (h,) in db.execute('SELECT hash FROM sent')}
    for i in range(1,MAX_ITER+1):
        lines=validate(collect_once(), seen); news_cnt=sum(1 for l in lines if l.startswith('-'))
        print(f"iter {i}: news={news_cnt}")
        if news_cnt>=MIN_NEWS or (i==MAX_ITER and news_cnt>=MIN_NEWS_SOFT):
            db.executemany('INSERT OR IGNORE INTO sent VALUES(?)',[(md5u(l),) for l in lines if l.startswith('-')]); db.commit()
            return "\n".join(lines)
        time.sleep(2)
    raise RuntimeError('Не удалось собрать дайджест')

# ───── SEND ─────

def send(msg:str):
    url=f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    for i in range(0,len(msg),3900):
        requests.post(url,json={"chat_id":CHAT_ID,"text":msg[i:i+3900],"parse_mode":"Markdown","disable_web_page_preview":False})

if __name__=='__main__':
    send(produce_digest())
