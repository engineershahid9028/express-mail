import threading
import time
import requests, os, re, json, hmac, hashlib
from datetime import datetime
from fastapi import FastAPI, Request, Header, HTTPException
from redis_client import r
from bs4 import BeautifulSoup

app = FastAPI(title="Express Mail API")

MAILTM_BASE = "https://api.mail.tm"

BOT_TOKEN = os.getenv("BOT_TOKEN")
BINANCE_SECRET = os.getenv("BINANCE_SECRET")

ADMIN_IDS = [
    7575476523  # <-- your Telegram ID
]

# ========================
# HELPERS
# ========================

def today():
    return datetime.utcnow().strftime("%Y-%m-%d")

def is_admin(chat_id):
    return int(chat_id) in ADMIN_IDS

def is_premium(chat_id):
    return r.exists(f"premium_user:{chat_id}")

def make_premium(user_id):
    r.set(f"premium_user:{user_id}", "1")

def remove_premium(user_id):
    r.delete(f"premium_user:{user_id}")

def can_create_email(chat_id):
    if is_premium(chat_id):
        return True, None

    key = f"free_count:{chat_id}:{today()}"
    count = int(r.get(key) or 0)

    if count >= 5:
        return False, "❌ Daily free limit reached (5 emails). Upgrade to Premium."

    return True, None

def increment_free_count(chat_id):
    key = f"free_count:{chat_id}:{today()}"
    r.incr(key)
    r.expire(key, 86400)


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
# HEALTH
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
# EMAIL WATCHER
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

                body = clean_email_body(full.get("text", ""), full.get("html", ""))
                otp = extract_otp(body)

                message = (
                    "📩 New Email Received\n\n"
                    f"From: {full.get('from', {}).get('address')}\n"
                    f"Subject: {full.get('subject')}\n\n"
                    f"{body}\n\n"
                )

                if otp:
                    message += f"🔐 OTP: {otp}"

                msg_id = send_bot_message_to(chat_id, message)

                if msg_id:
                    threading.Thread(
                        target=delete_bot_message,
                        args=(chat_id, msg_id, 300),
                        daemon=True
                    ).start()

                # Burn inbox (free users)
                if not is_premium(chat_id):
                    r.delete(f"mailtoken:{email}")
                    r.delete(f"user_session:{chat_id}")
                    send_bot_message_to(chat_id, "🗑 Inbox destroyed for privacy.")

                return

        except Exception as e:
            print("Watcher error:", e)

        time.sleep(3)


# ========================
# TELEGRAM BOT
# ========================

@app.post("/telegram/webhook")
def telegram_webhook(update: dict):
    message = update.get("message", {})
    text = message.get("text", "").strip()
    chat_id = message.get("chat", {}).get("id")

    if not text or not chat_id:
        return {"ok": True}

    # ====================
    # USER COMMANDS
    # ====================

    if text == "/status":
        send_bot_message_to(chat_id, "✅ Express Mail backend running.")

    elif text == "/quota":
        if is_premium(chat_id):
            send_bot_message_to(chat_id, "👑 Premium user — Unlimited emails.")
        else:
            key = f"free_count:{chat_id}:{today()}"
            used = int(r.get(key) or 0)
            remaining = 5 - used
            send_bot_message_to(chat_id, f"📊 Free quota: {remaining}/5 emails remaining today.")

    elif text == "/buy":
        send_bot_message_to(chat_id,
            "💳 Premium Upgrade\n\n"
            "Pay via Binance Pay (USDT)\n\n"
            f"Use your Telegram ID as Order ID:\n{chat_id}\n\n"
            "Premium unlocks automatically after payment."
        )

    elif text.startswith("/newemail"):
        allowed, error = can_create_email(chat_id)
        if not allowed:
            send_bot_message_to(chat_id, error)
            return {"ok": True}

        domains = get_domains()["domains"]
        if not domains:
            send_bot_message_to(chat_id, "❌ No email domains available.")
            return {"ok": True}

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
            return {"ok": True}

        token_res = requests.post(f"{MAILTM_BASE}/token", json={
            "address": email,
            "password": password
        }).json()

        token = token_res.get("token")
        if not token:
            send_bot_message_to(chat_id, "❌ Login failed.")
            return {"ok": True}

        r.setex(f"mailtoken:{email}", 600, token)
        r.setex(f"user_session:{chat_id}", 600, email)

        if not is_premium(chat_id):
            increment_free_count(chat_id)

        send_bot_message_to(chat_id,
            f"📧 Temp Email:\n\n{email}\n\n⏳ Waiting for email..."
        )

        threading.Thread(target=watch_for_email, args=(email, chat_id), daemon=True).start()

    # ====================
    # PREMIUM COMMANDS
    # ====================

    elif text == "/extend":
        if not is_premium(chat_id):
            send_bot_message_to(chat_id, "⭐ Premium only feature.")
            return {"ok": True}

        email = r.get(f"user_session:{chat_id}")
        if not email:
            send_bot_message_to(chat_id, "❌ No active inbox.")
        else:
            r.expire(f"mailtoken:{email}", 600)
            r.expire(f"user_session:{chat_id}", 600)
            send_bot_message_to(chat_id, "⏳ Inbox extended 5 more minutes.")

    elif text == "/burn":
        if not is_premium(chat_id):
            send_bot_message_to(chat_id, "⭐ Premium only feature.")
            return {"ok": True}

        email = r.get(f"user_session:{chat_id}")
        if email:
            r.delete(f"mailtoken:{email}")
            r.delete(f"user_session:{chat_id}")
            send_bot_message_to(chat_id, "🔥 Inbox destroyed.")


    # ====================
    # ADMIN COMMANDS
    # ====================

    elif text.startswith("/makepremium"):
        if not is_admin(chat_id):
            send_bot_message_to(chat_id, "❌ Admin only.")
            return {"ok": True}

        user_id = text.split()[1]
        make_premium(user_id)
        send_bot_message_to(chat_id, f"⭐ User {user_id} is now Premium.")

    elif text.startswith("/removepremium"):
        if not is_admin(chat_id):
            send_bot_message_to(chat_id, "❌ Admin only.")
            return {"ok": True}

        user_id = text.split()[1]
        remove_premium(user_id)
        send_bot_message_to(chat_id, f"❌ Premium removed from {user_id}.")

    elif text == "/premiumlist":
        if not is_admin(chat_id):
            send_bot_message_to(chat_id, "❌ Admin only.")
            return {"ok": True}

        users = [k.split(":")[1] for k in r.keys("premium_user:*")]
        send_bot_message_to(chat_id, "⭐ Premium Users:\n" + "\n".join(users))

    return {"ok": True}


# ========================
# BINANCE PAY WEBHOOK
# ========================

def verify_binance_signature(payload, signature):
    computed = hmac.new(
        BINANCE_SECRET.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()

    return computed == signature


@app.post("/payment/binance/webhook")
async def binance_webhook(request: Request, x_signature: str = Header(None)):
    payload = await request.body()
    payload_str = payload.decode()

    if not verify_binance_signature(payload_str, x_signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    data = json.loads(payload_str)

    if data.get("status") == "PAID":
        user_id = data.get("merchantOrderId")
        make_premium(user_id)
        send_bot_message_to(user_id, "⭐ Payment received! You are now Premium.")

    return {"status": "ok"}
