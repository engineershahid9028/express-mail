import re
from bs4 import BeautifulSoup

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
