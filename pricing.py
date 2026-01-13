from redis_client import r

def get_country_pricing(country):
    return r.hgetall(f"pricing:{country}")
