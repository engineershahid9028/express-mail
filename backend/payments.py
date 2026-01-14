from fastapi import APIRouter, Request, Header, HTTPException
import os, hmac, hashlib, json
from redis_client import r
from config import BINANCE_SECRET

payment_router = APIRouter()

def make_premium(user_id):
    r.set(f"premium_user:{user_id}", "1")

def verify_signature(payload, signature):
    computed = hmac.new(
        BINANCE_SECRET.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()
    return computed == signature

@payment_router.post("/binance/webhook")
async def binance_webhook(request: Request, x_signature: str = Header(None)):
    payload = await request.body()
    payload_str = payload.decode()

    if not verify_signature(payload_str, x_signature):
        raise HTTPException(401, "Invalid signature")

    data = json.loads(payload_str)

    if data.get("status") == "PAID":
        user_id = data.get("merchantOrderId")
        make_premium(user_id)

    return {"status": "ok"}
