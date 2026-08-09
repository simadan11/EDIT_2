import urllib.request
import json

def run_skill(args, player=None):
    coin = args.get("coin", "bitcoin").lower().strip()
    try:
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin}&vs_currencies=usd"
        req = urllib.request.Request(url, headers={"User-Agent": "EDIT-AI/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            if coin in data and "usd" in data[coin]:
                price = data[coin]["usd"]
                return f"The current price of {coin.capitalize()} is ${price:,.2f} USD."
    except Exception:
        pass
    return f"Checked price for {coin}. Please specify exact coin name if price is unavailable."
