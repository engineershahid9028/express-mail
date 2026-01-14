from datetime import datetime
from backend.redis_client import r
from backend.config import FREE_DAILY_LIMIT

def today():
    return datetime.utcnow().strftime("%Y-%m-%d")

def is_premium(chat_id):
    return r.exists(f"premium_user:{chat_id}")

def can_create_email(chat_id):
    if is_premium(chat_id):
        return True, None

    key = f"free_count:{chat_id}:{today()}"
    count = int(r.get(key) or 0)

    if count >= FREE_DAILY_LIMIT:
        return False, "❌ Daily free limit reached. Upgrade to Premium."

    return True, None

def increment_free_count(chat_id):
    key = f"free_count:{chat_id}:{today()}"
    r.incr(key)
    r.expire(key, 86400)
