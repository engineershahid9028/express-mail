from fastapi import APIRouter
from ui import show_main_menu
from limits import can_create_email, increment_free_count, is_premium
from redis_client import r

bot_router = APIRouter()

@bot_router.post("/webhook")
async def telegram_webhook(update: dict):
    if "message" in update:
        chat_id = update["message"]["chat"]["id"]
        text = update["message"].get("text", "")

        if text == "/start":
            show_main_menu(chat_id)

    return {"ok": True}
