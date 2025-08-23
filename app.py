import os
import time
import json
import logging
from flask import Flask, request, jsonify
import krakenex

# === Logging ===
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# === Flask app ===
app = Flask(__name__)

# === Kraken API ===
api = krakenex.API()
api.key = os.getenv("KRAKEN_API_KEY")
api.secret = os.getenv("KRAKEN_API_SECRET")

# === Params depuis variables Render ===
BASE = os.getenv("BASE", "BTC")               # ex: BTC
QUOTE = os.getenv("QUOTE", "EUR")             # ex: EUR
ORDER_TYPE = os.getenv("ORDER_TYPE", "market")

FIXED_EUR_PER_TRADE = float(os.getenv("FIXED_EUR_PER_TRADE", "20"))
FEE_BUFFER = float(os.getenv("FEE_BUFFER_PCT", "0.002"))
FALLBACK_TP = float(os.getenv("FALLBACK_TP_PCT", "0.6")) / 100
FALLBACK_SL = float(os.getenv("FALLBACK_SL_PCT", "1.0")) / 100
TRAIL_START = float(os.getenv("TRAIL_START_PCT", "0.6")) / 100
TRAIL_STEP = float(os.getenv("TRAIL_STEP_PCT", "0.3")) / 100

MAX_OPEN_POS = int(os.getenv("MAX_OPEN_POS", "1"))
MAX_DAILY_LOSS = float(os.getenv("MAX_DAILY_LOSS_PCT", "20"))

# === Helper: obtenir ticker ===
def get_price(pair):
    ticker = api.query_public("Ticker", {"pair": pair})
    return float(ticker["result"][list(ticker["result"].keys())[0]]["c"][0])

# === Helper: ouvrir une position ===
def open_order(signal, pair):
    price = get_price(pair)
    volume = FIXED_EUR_PER_TRADE / price
    volume = round(volume, 6)  # adapter à Kraken

    side = "buy" if signal == "BUY" else "sell"

    logging.info(f"==> ORDER {side.upper()} {volume} {pair} ~{FIXED_EUR_PER_TRADE} EUR @ {price}")

    try:
        order = api.query_private("AddOrder", {
            "pair": pair,
            "type": side,
            "ordertype": ORDER_TYPE,
            "volume": volume,
        })
        if order.get("error"):
            logging.error(f"Kraken ERROR: {order['error']}")
        else:
            logging.info(f"Order placed: {order}")
        return order
    except Exception as e:
        logging.error(f"Exception Kraken: {e}")
        return None

# === Webhook endpoint ===
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_data(as_text=True)
    logging.info(f"Raw alert: {data}")

    try:
        alert = json.loads(data)
    except Exception as e:
        logging.error(f"Invalid JSON: {e}")
        return jsonify({"status": "error", "msg": "invalid json"}), 400

    signal = alert.get("signal")
    if signal not in ["BUY", "SELL"]:
        return jsonify({"status": "ignored", "msg": "no trade signal"}), 200

    pair = f"{BASE}{QUOTE}"
    order = open_order(signal, pair)

    return jsonify({"status": "ok", "signal": signal, "pair": pair, "order": order}), 200

# === Healthcheck ===
@app.route("/", methods=["GET"])
def home():
    return "✅ TradingView-Kraken Bot is running."

# === Run app ===
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
