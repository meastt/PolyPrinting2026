import requests
import os
from dotenv import load_dotenv

load_dotenv()

KALSHI_API_KEY_ID = os.getenv("KALSHI_API_KEY_ID")
KALSHI_BASE_URL = "https://api.elections.kalshi.com"

print(f"Testing global scan on {KALSHI_BASE_URL}...")

def scan_all():
    # Try fetching without series filter
    url = f"{KALSHI_BASE_URL}/trade-api/v2/markets"
    params = {
        "status": "open",
        "limit": 100  # Try limit
    }
    
    try:
        res = requests.get(url, params=params)
        print(f"Status Code: {res.status_code}")
        if res.status_code != 200:
            print(res.text)
            return

        data = res.json()
        markets = data.get("markets", [])
        cursor = data.get("cursor")
        
        print(f"Fetched {len(markets)} markets.")
        print(f"Cursor: {cursor}")
        
        if len(markets) > 0:
            print("Sample Market:")
            print(markets[0]["ticker"], markets[0]["title"])
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    scan_all()
