import threading
import time
from fastapi import FastAPI, Request, Header, HTTPException
import requests, os, re
from redis_client import r
from geo import detect_country
from pricing import get_country_pricing
from admin import verify_admin
from bs4 import BeautifulSoup

app = FastAPI(title="Express Mail API")

MAILTM_BASE = "https://api.mail.tm"

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")


# ========================
# UTILITIES
# ========================

def extract_otp(text):
    patterns = [
        r"\b\d{4,8}\b",
        r"\b[A-Z0-9]{4,8}\b",
        r"\b[A-Z]{3}-[A-Z]{3}\b"
    ]

    for p in patterns:
        match = re.search(p, text)
        if match:
            return match.group(0)

    return None


def clean_email_body(text, html):
    if isinstance(text, list):
        text = "\n".join(text)

    if isinstance(html, list):
        html = "\n".join(html)

    if text and len(text.strip()) > 20:
        body = text
    else:
        soup = BeautifulSoup(html, "html.parser")
        body = soup.get_text(separator="\n")

    lines = []
    for line in body.splitlines():
        line = line.strip()
        if line and not line.lower().startswith("http"):
            lines.append(line)

    return "\n".join(lines[:40])


# ========================
# TELEGRAM HELPERS
# ========================

def send_bot_message_to(chat_id, text):
    if not BOT_TOKEN:
        return None

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    res = requests.post(url, data={
        "chat_id": chat_id,
        "text": text
    }).json()

    return res.get("result", {}).get("message_id")


def delete_bot_message(chat_id, message_id, delay=60):
    time.sleep(delay)
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteMessage"
    requests.post(url, data={
        "chat_id": chat_id,
        "message_id": message_id
    })


# ========================
# HEALTH CHECK
# ========================

@app.get("/health")
def health():
    return {"status": "ok", "service": "Express Mail"}


# ========================
# EMAIL DOMAIN LIST
# ========================

@app.get("/domains")
def get_domains():
    res = requests.get(f"{MAILTM_BASE}/domains").json()
    domains = [d["domain"] for d in res.get("hydra:member", [])]
    return {"domains": domains}


# ========================
# CREATE TEMP EMAIL (API)
# ========================

@app.post("/create-email")
def create_email():
    domains = get_domains()["domains"]
    if not domains:
        raise HTTPException(500, "No domains available")

    import random, string
    username = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
    email = f"{username}@{domains[0]}"
    password = "TempPass123!"

    acc = requests.post(f"{MAILTM_BASE}/accounts", json={
        "address": email,
        "password": password
    })

    if acc.status_code not in (200, 201):
        raise HTTPException(500, "Failed to create email")

    token_res = requests.post(f"{MAILTM_BASE}/token", json={
        "address": email,
        "password": password
    }).json()

    token = token_res.get("token")
    if not token:
        raise HTTPException(500, "Failed to login email")

    r.setex(f"mailtoken:{email}", 600, token)

    return {
        "email": email,
        "password": password
    }


# ========================
# GET INBOX (API)
# ========================

@app.get("/inbox/{email}")
def inbox(email: str):
    token = r.get(f"mailtoken:{email}")
    if not token:
        raise HTTPException(404, "Session expired")

    headers = {"Authorization": f"Bearer {token}"}
    res = requests.get(f"{MAILTM_BASE}/messages", headers=headers).json()

    messages = []
    for msg in res.get("hydra:member", []):
        msg_id = msg["id"]
        full = requests.get(f"{MAILTM_BASE}/messages/{msg_id}", headers=headers).json()

        text_part = full.get("text", "")
        html_part = full.get("html", "")

        body = clean_email_body(text_part, html_part)
        otp = extract_otp(body)

        messages.append({
            "from": msg["from"]["address"],
            "subject": msg["subject"],
            "body": body,
            "otp": otp,
            "time": msg["createdAt"]
        })

    return {"email": email, "messages": messages}


# ========================
# EMAIL WATCHER (BOT)
# ========================

def watch_for_email(email, chat_id, timeout=300):
    token = r.get(f"mailtoken:{email}")
    if not token:
        return

    headers = {"Authorization": f"Bearer {token}"}
    start_time = time.time()

    while time.time() - start_time < timeout:
        try:
            res = requests.get(f"{MAILTM_BASE}/messages", headers=headers).json()
            msgs = res.get("hydra:member", [])

            if msgs:
                msg_id = msgs[0]["id"]
                full = requests.get(f"{MAILTM_BASE}/messages/{msg_id}", headers=headers).json()

                subject = full.get("subject", "No subject")
                sender = full.get("from", {}).get("address", "Unknown sender")

                text_part = full.get("text", "")
                html_part = full.get("html", "")

                body = clean_email_body(text_part, html_part)
                otp = extract_otp(body)

                message = (
                    "📩 New Email Received\n\n"
                    f"From: {sender}\n"
                    f"Subject: {subject}\n\n"
                    f"Message:\n{body}\n\n"
                )

                if otp:
                    message += f"🔐 OTP: {otp}"

                msg_id = send_bot_message_to(chat_id, message)

                # Auto delete message after 60 seconds
                if msg_id:
                    threading.Thread(
                        target=delete_bot_message,
                        args=(chat_id, msg_id, 300),
                        daemon=True
                    ).start()

                # Destroy inbox
                r.delete(f"mailtoken:{email}")
                send_bot_message_to(chat_id, "🗑 Inbox destroyed for privacy.")
                return

        except Exception as e:
            print("Watcher error:", e)

        time.sleep(3)

    r.delete(f"mailtoken:{email}")
    send_bot_message_to(chat_id, "⌛ No email received (5 minutes). Inbox destroyed.")


# ========================
# TELEGRAM BOT COMMANDS
# ========================

@app.post("/telegram/webhook")
def telegram_webhook(update: dict):
    try:
        message = update.get("message", {})
        text = message.get("text", "").strip()
        chat_id = message.get("chat", {}).get("id")

        if not text or not chat_id:
            return {"ok": True}

        if text == "/status":
            msg_id = send_bot_message_to(chat_id, "✅ Express Mail backend is running.")
            if msg_id:
                threading.Thread(target=delete_bot_message, args=(chat_id, msg_id, 20), daemon=True).start()

        elif text == "/newemail":
            domains = get_domains()["domains"]
            if not domains:
                send_bot_message_to(chat_id, "❌ No email domains available.")
            else:
                import random, string
                username = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
                email = f"{username}@{domains[0]}"
                password = "TempPass123!"

                acc = requests.post(f"{MAILTM_BASE}/accounts", json={
                    "address": email,
                    "password": password
                })

                if acc.status_code not in (200, 201):
                    send_bot_message_to(chat_id, "❌ Failed to create email.")
                else:
                    token_res = requests.post(f"{MAILTM_BASE}/token", json={
                        "address": email,
                        "password": password
                    }).json()

                    token = token_res.get("token")
                    if not token:
                        send_bot_message_to(chat_id, "❌ Login failed.")
                    else:
                        r.setex(f"mailtoken:{email}", 600, token)

                        reply = (
                            f"📧 Your temporary email:\n\n{email}\n\n"
                            f"⏳ Waiting for email (5 minutes)..."
                        )

                        msg_id = send_bot_message_to(chat_id, reply)
                        if msg_id:
                            threading.Thread(target=delete_bot_message, args=(chat_id, msg_id, 30), daemon=True).start()

                        threading.Thread(
                            target=watch_for_email,
                            args=(email, chat_id),
                            daemon=True
                        ).start()

        elif text == "/help":
            msg_id = send_bot_message_to(
                chat_id,
                "📌 Express Mail Commands:\n\n/newemail - Create email & wait\n/status - Server status\n/help - Commands"
            )
            if msg_id:
                threading.Thread(target=delete_bot_message, args=(chat_id, msg_id, 30), daemon=True).start()

        else:
            send_bot_message_to(chat_id, "❓ Unknown command. Type /help")

        return {"ok": True}

    except Exception as e:
        print("Telegram webhook error:", e)
        return {"ok": False}


# ========================
# TELEGRAM WEBHOOK SETUP
# ========================

@app.get("/setup-telegram-webhook")
def setup_telegram_webhook():
    webhook_url = "https://web-production-5e56.up.railway.app/telegram/webhook"
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={webhook_url}"
    return requests.get(url).json()
