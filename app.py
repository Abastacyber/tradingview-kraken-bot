rimport os, json, logging, time
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# === ENV ===
API_KEY = os.getenv('KRAKEN_API_KEY', '')
API_SECRET = os.getenv('KRAKEN_API_SECRET', '')
BASE = os.getenv('BASE_SYMBOL', 'BTC').upper()
QUOTE = os.getenv('QUOTE_SYMBOL', 'EUR').upper()
PAPER = os.getenv('PAPER_MODE', '1') == '1'
RISK_EUR = float(os.getenv('RISK_EUR_PER_TRADE', '25'))

# Map TV -> Kraken (principaux)
from flask import Flask, request, jsonify
import os
import requests
import time

app = Flask(__name__)

# ====== PAGE D'ACCUEIL POUR UPTIMEROBOT ======
@app.route('/')
def home():
    return {"status": "ok"}, 200

# ====== CONFIG ======
RISK_EUR = 2.0  # Montant du trade en EUR
API_KEY = os.getenv("KRAKEN_API_KEY", "demo")
API_SECRET = os.getenv("KRAKEN_API_SECRET", "demo")
PAPER_TRADING = True  # True = mode simulation

# ====== WEBHOOK TRADINGVIEW ======
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()
    app.logger.info(f"Webhook payload: {data}")

    signal = data.get("signal")
    symbol = data.get("symbol")
    timeframe = data.get("time frame", "N/A")

    if not signal or not symbol:
        return jsonify({"error": "Invalid payload"}), 400

    # Exemple : XBT/EUR sur Kraken
    pair = symbol.replace(":", "").upper()

    try:
        price = fetch_price(pair)
        qty = calc_qty(price)

        if PAPER_TRADING:
            app.logger.info(f"PAPER {signal} {pair} qty={qty} price={price} tf={timeframe}")
            return jsonify({"paper": True, "signal": signal, "pair": pair, "qty": qty, "price": price}), 200
        else:
            # ICI mettre la fonction pour exécuter l'ordre réel sur Kraken
            app.logger.info(f"REAL (TODO) {signal} {pair} qty={qty} price={price}")
            return jsonify({"paper": False, "todo": "place real order"}), 200

    except Exception as e:
        app.logger.error(f"Error: {str(e)}")
        return jsonify({"error": str(e)}), 500

# ====== RECUPÉRATION DU PRIX KRAKEN ======
def fetch_price(pair: str) -> float:
    url = f"https://api.kraken.com/0/public/Ticker?pair={pair}"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    res = r.json()
    if res.get("error"):
        raise RuntimeError(res["error"])
    data = res["result"]
    k = next(iter(data.keys()))
    return float(data[k]["c"][0])

# ====== CALCUL DE LA QUANTITÉ ======
def calc_qty(price: float) -> float:
    raw = RISK_EUR / max(price, 1e-9)
    return float(f"{raw:.6f}")  # arrondi à 6 décimales

# ====== LANCEMENT APP ======
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
