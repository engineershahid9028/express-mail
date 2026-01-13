import requests

def detect_country(ip):
    try:
        res = requests.get(f"https://ipapi.co/{ip}/json/").json()
        return res.get("country_code", "US")
    except:
        return "US"
