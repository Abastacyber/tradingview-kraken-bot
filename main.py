import os, json, logging, time
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
MAP = {'BTC':'XBT', 'ETH':'XETH', 'LTC':'XLTC'}

def to_kraken_pair(tv_symbol: str) -> str:
    # "BTC/EUR" -> "XBTEUR"
    base, quote = [s.strip().upper() for s in tv_symbol.split('/')]
    return f"{MAP.get(base, base)}{quote}"

@app.get("/health")
def health():
    return jsonify({"status": "ok"}), 200

@app.post("/webhook")
def webhook():
    t0 = time.time()
    data = request.get_json(force=True, silent=False)
    app.logger.info(f"Webhook payload: {json.dumps(data, ensure_ascii=False)}")

    signal = (data.get("signal") or "").upper().strip()
    symbol = (data.get("symbol") or f"{BASE}/{QUOTE}").upper().strip()
    timeframe = (data.get("timeframe") or "").strip()

    if signal not in {"BUY","SELL"}:
        return jsonify({"error": "invalid signal"}), 400

    pair = to_kraken_pair(symbol)
    price = fetch_price(pair)
    qty = calc_qty(price)

    if PAPER:
        app.logger.info(f"PAPER {signal} {pair} qty={qty} price≈{price} tf={timeframe} dt={time.time()-t0:.3f}s")
        return jsonify({"paper": True, "signal": signal, "pair": pair, "qty": qty, "price": price}), 200

    # TODO: implémenter AddOrder privé Kraken ici (signature HMAC-SHA512)
    app.logger.info(f"REAL (TODO) {signal} {pair} qty={qty} price≈{price}")
    return jsonify({"paper": False, "todo": "place real order"}), 200

def fetch_price(pair: str) -> float:
    # Kraken public ticker
    url = f"https://api.kraken.com/0/public/Ticker?pair={pair}"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    res = r.json()
    if res.get("error"):
        raise RuntimeError(res["error"])
    data = res["result"]
    k = next(iter(data.keys()))
    return float(data[k]["c"][0])

def calc_qty(price: float) -> float:
    raw = RISK_EUR / max(price, 1e-9)
    return float(f"{raw:.6f}")  # arrondi simple; adapter au step size Kraken

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
