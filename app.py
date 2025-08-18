import os
import json
import logging
import time
import base64
import hashlib
import hmac
import urllib.parse

import requests
from flask import Flask, request, jsonify

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# === ENV ===
API_KEY     = os.getenv("KRAKEN_API_KEY", "")
API_SECRET  = os.getenv("KRAKEN_API_SECRET", "")
BASE        = os.getenv("BASE_SYMBOL", "BTC").upper()
QUOTE       = os.getenv("QUOTE_SYMBOL", "EUR").upper()
PAPER       = os.getenv("PAPER_MODE", "1") == "1"           # 1 = mode papier (dry)
VALIDATE    = os.getenv("KRAKEN_VALIDATE", "1") == "1"      # 1 = validate only côté Kraken
RISK_EUR    = float(os.getenv("RISK_EUR_PER_TRADE", "5"))   # investissement fixe en EUR

# Mapping TradingView -> Kraken (base seulement)
MAP_BASE = {
    "BTC": "XBT",
    "XBT": "XBT",
    "ETH": "XETH",
    "XETH": "XETH",
    "LTC": "XLTC",
    "XLTC": "XLTC",
}

# --- Helpers de parsing de symbole ---
def normalize_pair(symbol_tv: str | None, default_base: str, default_quote: str) -> str:
    """
    Accepte 'BTCEUR', 'BTC/EUR', 'XBT/EUR', 'XBTEUR', 'BTC:EUR', etc.
    Retourne toujours un pair Kraken 'XBTEUR', 'XETHEUR', ...
    """
    if not symbol_tv or not str(symbol_tv).strip():
        symbol_tv = f"{default_base}{default_quote}"

    s = str(symbol_tv).upper().replace(":", "").replace("/", "").strip()

    # Déduire le QUOTE en fin de chaîne (EUR par défaut)
    if s.endswith(default_quote):
        base = s[:-len(default_quote)]
        quote = default_quote
    else:
        # tentative simple si on ne reconnait pas : retombe sur env
        base, quote = default_base, default_quote

    base = base.strip()
    if base in MAP_BASE:
        base_kraken = MAP_BASE[base]
    else:
        # si TradingView envoie déjà la “forme Kraken” alternative (ex: XBTEUR),
        # on essaie de la reconnaître (ex: XBT + EUR)
        if base.startswith("X") and len(base) >= 3:
            base_kraken = base
        else:
            base_kraken = base  # dernier recours

    return f"{base_kraken}{quote}"

# --- Endpoints “ping/health” (Render & UptimeRobot) ---
@app.get("/")
def root_ok():
    return jsonify({"status": "ok"}), 200

@app.get("/health")
def health():
    return jsonify({"status": "ok"}), 200

# === Webhook TradingView ===
@app.post("/webhook")
def webhook():
    try:
        data = request.get_json(force=True, silent=False)
    except Exception:
        data = {}

    app.logger.info(f"app:Webhook payload: {json.dumps(data, ensure_ascii=False)}")

    signal    = (data.get("signal") or "").upper().strip()
    symbol_tv = (data.get("symbol") or f"{BASE}{QUOTE}").upper().strip()
    timeframe = (data.get("timeframe") or data.get("time frame") or "").strip()

    if signal not in {"BUY", "SELL"}:
        return jsonify({"error": "invalid signal"}), 400

    # Normaliser la paire pour Kraken
    pair_pub = normalize_pair(symbol_tv, BASE, QUOTE)     # ex: XBTEUR
    pair_priv = pair_pub                                  # AddOrder accepte aussi 'XBTEUR'

    try:
        price = fetch_price(pair_pub)                     # dernier prix du Ticker public
        qty   = calc_qty(price)                           # quantité en base-coins sur budget EUR

        if PAPER:
            app.logger.info(f"app:PAPER {signal} {pair_pub} qty={qty:.8f} tf={timeframe}")
            return jsonify({"paper": True, "signal": signal, "pair": pair_pub, "qty": qty, "price": price}), 200
        else:
            res = place_order(signal, pair_priv, qty, validate=VALIDATE)
            app.logger.info(f"app:REAL {signal} {pair_priv} qty={qty:.8f} validate={VALIDATE} RESULT={res}")
            return jsonify({"paper": False, "validate": VALIDATE, "result": res}), 200

    except Exception as e:
        app.logger.exception("app:Webhook error")
        return jsonify({"error": str(e)}), 500

# === Helpers Kraken publics ===
def fetch_price(pair: str) -> float:
    """
    Récupère le dernier prix via /0/public/Ticker.
    Accepte 'XBTEUR' (forme alternative). Si Kraken renvoie une erreur, on lève.
    """
    url = f"https://api.kraken.com/0/public/Ticker?pair={pair}"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    js = r.json()

    if js.get("error"):
        raise RuntimeError(f"{js['error']}")

    result = js["result"]
    k = next(iter(result.keys()))
    c = result[k]["c"][0]  # last trade price
    return float(c)

def calc_qty(price: float) -> float:
    # quantité en coin = budget_eur / prix
    raw = RISK_EUR / max(price, 1e-9)
    # arrondi 6 décimales (Kraken autorise 8 max sur XBT)
    return float(f"{raw:.6f}")

# === Helpers Kraken privés (HMAC) ===
KRAKEN_API_URL = "https://api.kraken.com"

def _kraken_sign(uri_path: str, data: dict, secret: str) -> str:
    postdata = urllib.parse.urlencode(data)
    encoded = (str(data["nonce"]) + postdata).encode()
    message = uri_path.encode() + hashlib.sha256(encoded).digest()
    mac = hmac.new(base64.b64decode(secret), message, hashlib.sha512)
    sig_digest = base64.b64encode(mac.digest())
    return sig_digest.decode()

def kraken_private(endpoint: str, data: dict) -> dict:
    if "nonce" not in data:
        data["nonce"] = int(time.time() * 1000)
    uri_path = f"/0/private/{endpoint}"

    headers = {
        "API-Key": API_KEY,
        "API-Sign": _kraken_sign(uri_path, data, API_SECRET),
        "Content-Type": "application/x-www-form-urlencoded",
    }

    r = requests.post(KRAKEN_API_URL + uri_path, headers=headers,
                      data=urllib.parse.urlencode(data), timeout=15)
    r.raise_for_status()
    resp = r.json()
    if resp.get("error"):
        raise RuntimeError(f"{resp['error']}")
    return resp["result"]

def place_order(signal: str, pair: str, qty: float, validate: bool = True) -> dict:
    side = "buy" if signal == "BUY" else "sell"
    data = {
        "pair": pair,            # ex: XBTEUR
        "type": side,            # buy/sell
        "ordertype": "market",
        "volume": f"{qty:.8f}",  # 8 décimales max
        "validate": validate,    # True = validation côté Kraken (pas d’exécution)
    }
    return kraken_private("AddOrder", data)

# === Run local (Render utilise gunicorn) ===
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
