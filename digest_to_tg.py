#!/usr/bin/env python3
import os, textwrap, datetime, requests
from openai import OpenAI          # новенький клиент
from dotenv import load_dotenv

load_dotenv()                      # подхватываем .env
TZ = datetime.timezone(datetime.timedelta(hours=3))   # Moscow
MODEL = "o3"                       # o3 или gpt‑4o‑mini

#def make_prompt() -> str:
 #   today = datetime.datetime.now(TZ).strftime("%d %b %Y")
  #  return (f"Сделай IT‑дайджест за {today}: 5 новостей за 48 ч, "
   #         "обзор мирового IT‑рынка, российского рынка и 1С; добавь ссылки после каждой новости.")
def make_prompt() -> str:
    today = datetime.datetime.now(TZ).strftime("%d %b %Y")

    # многострочный текст без лишних отступов
    return f"""
Ты — опытный IT‑обозреватель. Составь дайджест **строго в следующем формате**:

🗞️ IT‑Digest • {today}

🌍 **ГЛОБАЛЬНЫЙ IT**
- *Жирный заголовок первой новости* — 1‑2 предложения сути (≤ 30 слов). [Источник]
- *Вторая новость* — 1‑2 предложения. [Источник]
- *Третья новость (если есть)* — 1‑2 предложения. [Источник]

🇷🇺 **РОССИЙСКИЙ TECH**
- *Первая новость* — 1‑2 предложения. [Источник]
- *Вторая новость* — 1‑2 предложения. [Источник]
- *Третья новость (если есть)* — 1‑2 предложения. [Источник]

🟡 **ЭКОСИСТЕМА 1С**
- *Первая новость* — 1‑2 предложения. [Источник]
- *Вторая новость (если есть)* — 1‑2 предложения. [Источник]

💡 **Insight:** В 2‑3 предложениях объясни, почему эти события важны для IT‑PM‑ов.

Требования и фильтры:
• Используй только материалы, опубликованные за последние **48 часов**.  
• Игнорируй шутки 1 апреля, рекламные или paywall‑источники.  
• Не дублируй новости из предыдущих двух суток.  
• Ссылку ставь в квадратных скобках Markdown‑ом сразу после каждой новости.  
• Лаконичный, полу‑деловой тон; допускаются эмодзи разделов, но без лишних смайлов в тексте.
"""


def fetch_digest() -> str:
    client = OpenAI()  # ключ читается из OPENAI_API_KEY
    resp = client.responses.create(
        model = MODEL,
        tools = [{"type": "web_search"}],    # ← даёт модели интернет
        input = [{"role": "user", "content": make_prompt()}],
        store = False                        # историю нам хранить не нужно
    )
    # у Responses API ответ в resp.output[0].content
    return resp.output_text.strip()

def send_to_telegram(text: str):
    url = f"https://api.telegram.org/bot{os.getenv('TG_TOKEN')}/sendMessage"
    for chunk in textwrap.wrap(text, 4096):   # Telegram лимит 4096 символов
        requests.post(url, json={
            "chat_id": os.getenv("CHAT_ID"),
            "text": chunk,
            "parse_mode": "Markdown",
            "disable_web_page_preview": False
        })

if __name__ == "__main__":
    send_to_telegram(fetch_digest())
