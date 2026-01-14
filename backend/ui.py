import json, requests
from backend.config import BOT_TOKEN

def send_ui(chat_id, text, buttons):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "reply_markup": json.dumps({"inline_keyboard": buttons})
    }
    requests.post(url, data=payload)

def show_main_menu(chat_id):
    send_ui(
        chat_id,
        "📬 *Express Mail*\n\nTemporary Email & OTP Platform",
        [
            [{"text": "📧 Create Email", "callback_data": "newemail"}],
            [{"text": "⭐ Buy Premium", "callback_data": "buy"}],
            [{"text": "📊 Dashboard", "callback_data": "dashboard"}],
            [{"text": "🎁 Referral", "callback_data": "referral"}],
        ]
    )
