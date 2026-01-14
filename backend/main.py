from fastapi import FastAPI
from backend.bot import bot_router
from backend.payments import payment_router

app = FastAPI(title="Express Mail Platform")

app.include_router(bot_router, prefix="/bot")
app.include_router(payment_router, prefix="/payments")

@app.get("/")
def home():
    return {"status": "Express Mail running"}
