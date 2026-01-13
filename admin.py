import os
from fastapi import HTTPException

ADMIN_KEY = os.getenv("ADMIN_KEY")

def verify_admin(key):
    if key != ADMIN_KEY:
        raise HTTPException(403, "Unauthorized")
