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
    # Normalize
    if isinstance(text, list):
        text = "\n".join(text)

    if isinstance(html, list):
        html = "\n".join(html)

    # Prefer plain text if available
    if text and len(text.strip()) > 20:
        body = text
    else:
        soup = BeautifulSoup(html, "html.parser")
        body = soup.get_text(separator="\n")

    # Cleanup lines
    lines = []
    for line in body.splitlines():
        line = line.strip()
        if line and not line.lower().startswith("http"):
            lines.append(line)

    return "\n".join(lines[:40])


def send_bot_message_to(chat_id, text):
    if not BOT_TOKEN:
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, data={
        "chat_id": chat_id,
        "text": text
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

                send_bot_message_to(chat_id, message)

                # Destroy inbox after delivery
                r.delete(f"mailtoken:{email}")
                send_bot_message_to(chat_id, "🗑 Inbox destroyed for privacy.")
                return

        except Exception as e:
            print("Watcher error:", e)

        time.sleep(3)

    # Timeout
    r.delete(f"mailtoken:{email}")
    send_bot_message_to(chat_id, "⌛ No email received (5 minutes). Inbox destroyed.")


# ========================
# AUTO COUNTRY PRICING
# ========================

@app.get("/pricing")
def pricing(request: Request, country_override: str = None):
    ip = request.client.host
    country = country_override or detect_country(ip)

    pricing = get_country_pricing(country)
    if not pricing:
        pricing = get_country_pricing("US")

    return {
        "country": country,
        "currency": pricing.get("currency", "USD"),
        "plans": {
            "week": pricing.get("week"),
            "month": pricing.get("month"),
            "3month": pricing.get("3month"),
            "12month": pricing.get("12month")
        }
    }


# ========================
# ADMIN: SET PRICING
# ========================

@app.post("/admin/pricing/{country}")
def admin_set_pricing(
    country: str,
    data: dict,
    admin_key: str = Header(...)
):
    verify_admin(admin_key)
    r.hset(f"pricing:{country}", mapping=data)
    return {"status": "updated", "country": country}


# ========================
# TELEGRAM BOT ALERTS
# ========================

@app.post("/bot/alert")
def bot_alert(msg: str):
    if CHAT_ID:
        send_bot_message_to(CHAT_ID, msg)
    return {"sent": True}


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
            reply = "✅ Express Mail backend is running."

        elif text == "/newemail":
            domains = get_domains()["domains"]
            if not domains:
                reply = "❌ No email domains available."
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
                    reply = "❌ Failed to create email."
                else:
                    token_res = requests.post(f"{MAILTM_BASE}/token", json={
                        "address": email,
                        "password": password
                    }).json()

                    token = token_res.get("token")
                    if not token:
                        reply = "❌ Login failed."
                    else:
                        r.setex(f"mailtoken:{email}", 600, token)

                        reply = (
                            f"📧 Your temporary email:\n\n{email}\n\n"
                            f"⏳ Waiting for email (5 minutes)..."
                        )

                        threading.Thread(
                            target=watch_for_email,
                            args=(email, chat_id),
                            daemon=True
                        ).start()

        elif text == "/help":
            reply = (
                "📌 Express Mail Bot Commands:\n\n"
                "/newemail - Create temp email & wait for email\n"
                "/status - Server status\n"
                "/help - Show commands"
            )

        else:
            reply = "❓ Unknown command. Type /help"

        send_bot_message_to(chat_id, reply)
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

    res = requests.get(url).json()
    return res
