import os
import json
from flask import Flask, request
import krakenex

# ====== Config via variables d'environnement Render ======
BASE_SYMBOL = os.getenv("BASE_SYMBOL", "BTC")
QUOTE_SYMBOL = os.getenv("QUOTE_SYMBOL", "EUR")
RISK_EUR_PER_TRADE = float(os.getenv("RISK_EUR_PER_TRADE", "25"))
PAPER_MODE = os.getenv("PAPER_MODE", "1") == "1"

KRAKEN_API_KEY = os.getenv("KRAKEN_API_KEY")
KRAKEN_API_SECRET = os.getenv("KRAKEN_API_SECRET")

# ====== Kraken client ======
kraken = krakenex.API()
kraken.key = KRAKEN_API_KEY
kraken.secret = KRAKEN_API_SECRET

app = Flask(__name__)

@app.get("/")
def home():
    return "Bot TradingView-Kraken actif"

@app.post("/webhook")
def webhook():
    try:
        data = request.get_json(force=True, silent=True) or {}
        signal = (data.get("signal") or "").upper()
        if signal not in ("BUY", "SELL"):
            return {"error": "invalid signal"}, 400

        pair = f"{BASE_SYMBOL}{QUOTE_SYMBOL}"

        if PAPER_MODE:
            print(f"[PAPER] {signal} {pair}")
            return {"status": "ok", "mode": "paper", "signal": signal, "pair": pair}, 200

        # Récupère le prix marché et calcule un petit volume
        t = kraken.query_public("Ticker", {"pair": pair})
        key = next(iter(t["result"]))
        price = float(t["result"][key]["c"][0])
        volume = max(round(RISK_EUR_PER_TRADE / price, 6), 0.000025)

        order = kraken.query_private("AddOrder", {
            "pair": pair,
            "type": "buy" if signal == "BUY" else "sell",
            "ordertype": "market",
            "volume": str(volume),
        })
        print(f"[LIVE] {signal} {pair} -> {order}")
        return {"status": "ok", "order": order}, 200

    except Exception as e:
        print(f"[ERROR] {e}")
        return {"error": str(e)}, 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
