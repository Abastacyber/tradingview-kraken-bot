# app.py
import os
import json
import time
import base64
import hmac
import hashlib
import urllib.parse
import logging

import requests
from flask import Flask, request, jsonify

# === App / Logs ===
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# === ENV ===
API_KEY   = os.getenv("KRAKEN_API_KEY", "")
API_SECRET= os.getenv("KRAKEN_API_SECRET", "")
BASE      = os.getenv("BASE_SYMBOL", "BTC").upper()
QUOTE     = os.getenv("QUOTE_SYMBOL", "EUR").upper()
PAPER     = os.getenv("PAPER_MODE", "1") == "1"          # 1 = paper (pas d'ordre réel)
RISK_EUR  = float(os.getenv("RISK_EUR_PER_TRADE", "25")) # budget EUR par trade
VALIDATE_ENV = os.getenv("KRAKEN_VALIDATE", "1")         # 1 = dry-run côté Kraken

# Mapping TradingView -> Kraken (principaux)
MAP = {"BTC": "XBT", "ETH": "XETH", "LTC": "XLTC"}

# ========= Routes ping (UptimeRobot) =========
@app.route("/")
def root_ok():
    return {"status": "ok"}, 200

@app.get("/health")
def health():
    return {"status": "ok"}, 200

# ========= Webhook TradingView =========
@app.post("/webhook")
def webhook():
    data = request.get_json(force=True, silent=False)
    app.logger.info(f"Webhook payload: {json.dumps(data, ensure_ascii=False)}")

    # Champs attendus
    signal    = (data.get("signal") or "").upper().strip()
    symbol_tv = (data.get("symbol") or f"{BASE}/{QUOTE}").upper().strip()
    timeframe = (data.get("timeframe") or data.get("time frame") or "").strip()

    if signal not in {"BUY", "SELL"}:
        return jsonify({"error": "invalid signal"}), 400

    # "BTC/EUR" -> "XBTEUR"
    try:
        b, q = [s.strip().upper() for s in symbol_tv.split("/")]
        pair = f"{MAP.get(b, b)}{q}"
    except Exception:
        pair = symbol_tv.replace(":", "").replace("/", "").upper()

    try:
        # Récup prix et calc qty
        price = fetch_price(pair)     # dernier prix Kraken
        qty   = calc_qty(price)       # volume en coin, à partir de RISK_EUR

        if PAPER:
            app.logger.info(f"PAPER {signal} {pair} qty={qty} price={price} tf={timeframe}")
            return jsonify({"paper": True, "signal": signal, "pair": pair, "qty": qty, "price": price}), 200
        else:
            # Ordre réel (avec dry-run côté Kraken si VALIDATE_ENV=1)
            res = place_order(signal, pair, qty, validate=(VALIDATE_ENV == "1"))
            app.logger.info(f"REAL {signal} {pair} qty={qty} validate={VALIDATE_ENV} RESULT={res}")
            return jsonify({"paper": False, "validate": VALIDATE_ENV == "1", "result": res}), 200

    except Exception as e:
        app.logger.exception("Webhook error")
        return jsonify({"error": str(e)}), 500

# ========= Helpers publics Kraken =========
def fetch_price(pair: str) -> float:
    """
    Retourne le dernier prix traité (c[0]) via /0/public/Ticker
    pair ex: XBTEUR
    """
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
    """
    Calcul volume coin = budget_EUR / prix coin
    Arrondi simple à 6 décimales (adapter si besoin selon step size Kraken).
    """
    raw = RISK_EUR / max(price, 1e-9)
    return float(f"{raw:.6f}")

# ========= Helpers privés Kraken (HMAC) =========
KRAKEN_API_URL = "https://api.kraken.com"

def _kraken_sign(uri_path: str, data: dict, secret: str) -> str:
    postdata = urllib.parse.urlencode(data)
    encoded = (str(data["nonce"]) + postdata).encode()
    message = uri_path.encode() + hashlib.sha256(encoded).digest()
    mac = hmac.new(base64.b64decode(secret), message, hashlib.sha512)
    return base64.b64encode(mac.digest()).decode()

def kraken_private(endpoint: str, data: dict) -> dict:
    if "nonce" not in data:
        data["nonce"] = int(time.time() * 1000)

    uri_path = f"/0/private/{endpoint}"
    headers = {
        "API-Key": API_KEY,
        "API-Sign": _kraken_sign(uri_path, data, API_SECRET),
        "Content-Type": "application/x-www-form-urlencoded",
    }

    r = requests.post(
        KRAKEN_API_URL + uri_path,
        headers=headers,
        data=urllib.parse.urlencode(data),
        timeout=15,
    )
    r.raise_for_status()
    resp = r.json()
    if resp.get("error"):
        # Kraken renvoie une liste d’erreurs; si non vide -> exception
        if isinstance(resp["error"], list) and resp["error"]:
            raise RuntimeError(f"Kraken error: {resp['error']}")
        elif resp["error"]:
            raise RuntimeError(f"Kraken error: {resp['error']}")
    return resp.get("result", {})

def place_order(signal: str, pair: str, qty: float, validate: bool = True) -> dict:
    """
    Envoie un ordre MARKET (buy/sell) sur Kraken.
    validate=True => dry-run (validation sans exécution).
    """
    side = "buy" if signal == "BUY" else "sell"
    data = {
        "pair": pair,               # ex: XBTEUR
        "type": side,               # buy / sell
        "ordertype": "market",
        "volume": f"{qty:.8f}",     # 8 décimales max
        "validate": "true" if validate else "false",
    }
    return kraken_private("AddOrder", data)

# ========= Run local (sur Render, gunicorn est utilisé) =========
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
