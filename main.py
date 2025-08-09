import os
from flask import Flask, request, jsonify
import krakenex

app = Flask(__name__)

# --- Config via variables d'environnement Render ---
API_KEY = os.getenv("KRAKEN_API_KEY", "")
API_SECRET = os.getenv("KRAKEN_API_SECRET", "")
PAPER_MODE = os.getenv("PAPER_MODE", "1") == "1"  # 1 = paper, 0 = réel

BASE = os.getenv("BASE_SYMBOL", "BTC")
QUOTE = os.getenv("QUOTE_SYMBOL", "EUR")
RISK_EUR_PER_TRADE = float(os.getenv("RISK_EUR_PER_TRADE", "25"))

# Client Kraken (même en paper on l'instancie, pas grave)
api = krakenex.API(API_KEY, API_SECRET)

# Mapping simple des paires Kraken (tu pourras en ajouter au besoin)
def kraken_pair_code(base: str, quote: str) -> str:
    mapping = {
        ("BTC", "EUR"): "XXBTZEUR",
        ("ETH", "EUR"): "XETHZEUR",
        ("BTC", "USD"): "XXBTZUSD",
        ("ETH", "USD"): "XETHZUSD",
    }
    return mapping.get((base.upper(), quote.upper()), f"{base.upper()}{quote.upper()}")

PAIR = kraken_pair_code(BASE, QUOTE)

@app.get("/")
def health():
    return "ok", 200

@app.post("/webhook")
def webhook():
    data = request.get_json(force=True, silent=True) or {}
    print(">>> Webhook reçu:", data)

    signal = (data.get("signal") or "").upper()
    price_raw = data.get("price")

    # Prix peut arriver avec virgule (locale FR) -> on normalise
    price = None
    if isinstance(price_raw, (int, float)):
        price = float(price_raw)
    elif isinstance(price_raw, str):
        price = float(price_raw.replace(",", "."))
    # Sinon, on laissera Kraken remplir au prix marché.

    # On ne traite que les signaux attendus
    if signal not in {"BUY", "SELL", "BUY_CONFIRM", "SELL_CONFIRM"}:
        return jsonify({"status": "ignored", "reason": "unknown signal"}), 200

    # Taille de position approximative : RISK_EUR_PER_TRADE / price
    # (si pas de price, volume par défaut très petit)
    if price and price > 0:
        volume = round(RISK_EUR_PER_TRADE / price, 6)
    else:
        volume = 0.00025  # micro-volume par défaut

    side = "buy" if signal.startswith("BUY") else "sell"

    if PAPER_MODE:
        print(f"[PAPER] {side.upper()} {PAIR} volume={volume} price={price}")
        return jsonify({"status": "paper_ok", "pair": PAIR, "side": side, "volume": volume}), 200

    # En réel : ordre au marché
    try:
        payload = {
            "pair": PAIR,
            "type": side,
            "ordertype": "market",
            "volume": str(volume),
        }
        resp = api.query_private("AddOrder", payload)
        print("Kraken AddOrder resp:", resp)
        return jsonify({"status": "ok", "kraken": resp}), 200
    except Exception as e:
        print("Kraken error:", e)
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    # Render fournit PORT en env ; sinon 5000 en local
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
