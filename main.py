from fastapi import FastAPI, Request, Header, HTTPException
import requests, os, re
from redis_client import r
from geo import detect_country
from pricing import get_country_pricing
from admin import verify_admin

app = FastAPI(title="Express Mail API")

MAILTM_BASE = "https://api.mail.tm"
OTP_REGEX = r"\b\d{4,8}\b"


# ========================
# UTILITIES
# ========================

def extract_otp(text):
    match = re.search(OTP_REGEX, text)
    return match.group(0) if match else None


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
# CREATE TEMP EMAIL
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

    # Create account
    acc = requests.post(f"{MAILTM_BASE}/accounts", json={
        "address": email,
        "password": password
    })

    if acc.status_code not in (200, 201):
        raise HTTPException(500, "Failed to create email")

    # Login
    token_res = requests.post(f"{MAILTM_BASE}/token", json={
        "address": email,
        "password": password
    }).json()

    token = token_res.get("token")
    if not token:
        raise HTTPException(500, "Failed to login email")

    # Save token temporarily
    r.setex(f"mailtoken:{email}", 3600, token)

    return {
        "email": email,
        "password": password
    }


# ========================
# GET INBOX
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
        body = full.get("text", "") + full.get("html", "")
        otp = extract_otp(body)

        messages.append({
            "from": msg["from"]["address"],
            "subject": msg["subject"],
            "otp": otp,
            "time": msg["createdAt"]
        })

    return {"email": email, "messages": messages}


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

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")


def send_bot_message(text):
    if not BOT_TOKEN or not CHAT_ID:
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, data={
        "chat_id": CHAT_ID,
        "text": text
    })


@app.post("/bot/alert")
def bot_alert(msg: str):
    send_bot_message(msg)
    return {"sent": True}
# ========================
# TELEGRAM BOT COMMANDS
# ========================

@app.post("/telegram/webhook")
def telegram_webhook(update: dict):
    message = update.get("message", {})
    text = message.get("text", "")
    chat_id = message.get("chat", {}).get("id")

    if not text or not chat_id:
        return {"ok": True}

    if text == "/status":
        reply = "✅ Express Mail backend is running."

    elif text == "/health":
        reply = "🟢 System status: OK"

    elif text == "/pricing":
        pricing = get_country_pricing("PK") or {}
        reply = f"💰 PK Pricing:\nWeek: {pricing.get('week')}\nMonth: {pricing.get('month')}"

    elif text == "/help":
        reply = (
            "📌 Express Mail Bot Commands:\n"
            "/status - Check server status\n"
            "/pricing - Show PK pricing\n"
            "/help - Show commands"
        )

    else:
        reply = "❓ Unknown command. Type /help"

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, data={
        "chat_id": chat_id,
        "text": reply
    })

    return {"ok": True}

