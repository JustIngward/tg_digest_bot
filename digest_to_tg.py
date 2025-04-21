#!/usr/bin/env python3
"""IT‑Digest Telegram bot — v8.0 (2025‑04‑22)

✦ Пересборка «с нуля» — один вызов модели формирует готовый дайджест
───────────────────────────────────────────────────────────────────
* Используем только **один** chat‑completion с web_search: модель сразу отдаёт
  финальный Markdown.  
* Python‑скрипт делает минимум валидации (регекс + кол‑во строк) и шлёт в TG.  
* Нет пост‑скоринга и HEAD‑проверок → меньше токенов, меньше падений.  
* Температура задаётся переменной `TEMPERATURE` (по‑умолчанию 0.8) — подходит
  для o3.
"""
import os, re, sqlite3, datetime as dt, time, requests
from dotenv import load_dotenv
from openai import OpenAI

# ───── CONFIG ─────
load_dotenv()
TZ            = dt.timezone(dt.timedelta(hours=3))
MODEL         = os.getenv("MODEL", "gpt-4o")
TEMP          = float(os.getenv("TEMPERATURE", 0.8))
MAX_AGE_DAYS  = int(os.getenv("MAX_AGE_DAYS", 7))
MIN_LINES     = int(os.getenv("MIN_LINES", 6))
MAX_ITER      = int(os.getenv("MAX_ITER", 4))
TG_TOKEN      = os.environ["TG_TOKEN"]
CHAT_ID       = os.environ["CHAT_ID"]

client = OpenAI()

NEWS_RE = re.compile(r"^\s*[-*]\s*(?:\*\*)?.+?\]\(https?://[^)\s]+\)\s*\(\d{2}\.\d{2}\.(?:\d{4})?\)")

# ───── PROMPT ─────

def build_prompt() -> str:
    today = dt.datetime.now(TZ).strftime('%d %b %Y')
    days  = MAX_AGE_DAYS
    return f"""
Ты — IT‑аналитик.
Сделай готовый дайджест (Markdown) для IT‑департамента.
Правила:
1. Бери ТОЛЬКО реальные статьи, опубликованные ≤ {days} дней.
2. Формат одной новости: `- **Заголовок** — суть. [Источник](URL) (DD.MM.YYYY)`.
3. Секции строго в порядке и с пустой строкой между:
   🌍 **ГЛОБАЛЬНЫЙ IT**

   🇷🇺 **РОССИЙСКИЙ TECH**

   🟡 **ЭКОСИСТЕМА 1С**
4. Минимум {MIN_LINES} строк суммарно.
5. В конце добавь `💡 **Insight:**` — 2‑3 предложения, зачем это важно.
6. Заголовок всего дайджеста: `🗞️ **IT‑Digest • {today}**`.
7. Пиши по‑русски, без лишних эмодзи и UTM‑меток.
"""

# ───── COLLECTOR ─────

def collect() -> str:
    # responses.create поддерживает web_search без function schema
    resp = client.responses.create(
        model=MODEL,
        tools=[{"type": "web_search"}],
        input=[{"role": "user", "content": build_prompt()}],
        temperature=TEMP,
        store=False,
    )
    return resp.output_text.strip()

# ───── VALIDATE ─────

def validate(md:str) -> bool:
    lines=[l for l in md.splitlines() if NEWS_RE.match(l)]
    return len(lines)>=MIN_LINES

# ───── MAIN ─────

def run():
    for i in range(1,MAX_ITER+1):
        draft = collect()
        if validate(draft):
            return draft
        print(f"iter {i}: not enough lines, retry…")
        time.sleep(2)
    raise RuntimeError("Не удалось сформировать дайджест — модель не собрала достаточное число новостей")

# ───── SEND ─────

def send(msg:str):
    url=f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    for i in range(0,len(msg),3900):
        requests.post(url,json={"chat_id":CHAT_ID,"text":msg[i:i+3900],"parse_mode":"Markdown","disable_web_page_preview":False})

if __name__=='__main__':
    digest = run()
    send(digest)
