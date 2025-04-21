#!/usr/bin/env python3
import os, textwrap, datetime, requests
from openai import OpenAI          # новенький клиент
from dotenv import load_dotenv

load_dotenv()                      # подхватываем .env
TZ = datetime.timezone(datetime.timedelta(hours=3))   # Moscow
MODEL = "o3"                       # o3 или gpt‑4o‑mini

def make_prompt() -> str:
    """
    Возвращает промпт‑шаблон для модели.
    Гарантирует:
    • Только материалы ≤ 48 ч   • Явная дата у каждой новости   • Читаемый Markdown
    """
    today = datetime.datetime.now(TZ).strftime("%d %b %Y")

    return f"""
Ты — IT‑аналитик. Сформируй дайджест в строгом формате Markdown ниже.
⚠️ Используй ТОЛЬКО источники, опубликованные не старше 48 часов от {today}.
Если источник старше — замени его. Дату бери из самой статьи или метаданных.
⚠️ Укажи дату публикации (ДД.ММ) после ссылки, чтобы я мог проверить свежесть.

Формат выдачи (сохрани пустые строки и отступы):

🗞️ **IT‑Digest • {today}**

🌍 **ГЛОБАЛЬНЫЙ IT**
- **Короткий жирный заголовок** — 1‑2 предложения сути. [Источник](URL) (DD.MM)
- …

🇷🇺 **РОССИЙСКИЙ TECH**
- **Жирный заголовок** — 1‑2 предложения. [Источник](URL) (DD.MM)
- …

🟡 **ЭКОСИСТЕМА 1С**
- **Жирный заголовок** — 1‑2 предложения. [Источник](URL) (DD.MM)

💡 **Insight:** 2‑3 предложения, почему эти события важны PM‑ам.

Требования по стилю:
• Каждая строка новости на отдельной строке; между секциями пустая строка.  
• Не добавляй лишние смайлы; разрешены только 🌍 🇷🇺 🟡 в названиях секций.  
• Лимит ≤ 30 слов на новость.  
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
    url      = f"https://api.telegram.org/bot{os.getenv('TG_TOKEN')}/sendMessage"
    char_max = 4000                # немного меньше лимита 4096

    for i in range(0, len(text), char_max):
        chunk = text[i:i+char_max]   # срез сохраняет все \n
        requests.post(url, json={
            "chat_id": os.getenv("CHAT_ID"),
            "text": chunk,
            "parse_mode": "Markdown",
            "disable_web_page_preview": False
        })

if __name__ == "__main__":
    send_to_telegram(fetch_digest())
