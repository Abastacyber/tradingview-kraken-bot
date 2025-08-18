# app.py — Bot TradingView -> Kraken (LIMIT + gestion du risque)
# --------------------------------------------------------------
# - Reçoit un webhook JSON de TradingView
# - Calcule la quantité en € risqués (fixe ou % du solde EUR)
# - Passe un ordre LIMIT sur Kraken (offset pour baisser les frais/prix)
# - Modes:
#   PAPER_MODE=1  -> ne passe pas d'ordre, log seulement
#   KRAKEN_VALIDATE=1 -> "test" côté Kraken (AddOrder validate=1)

import os
import json
import time
import hmac
import base64
import hashlib
import logging
import urllib.parse
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# === ENV ===
API_KEY     = os.getenv("KRAKEN_API_KEY", "")
API_SECRET  = os.getenv("KRAKEN_API_SECRET", "")
BASE        = os.getenv("BASE_SYMBOL", "BTC").upper()      # ex: BTC
QUOTE       = os.getenv("QUOTE_SYMBOL", "EUR").upper()     # ex: EUR
PAPER       = os.getenv("PAPER_MODE", "1") == "1"          # 1 = simulation côté bot
VALIDATE    = os.getenv("KRAKEN_VALIDATE", "1") == "1"     # 1 = dry-run côté Kraken
RISK_EUR    = float(os.getenv("RISK_EUR_PER_TRADE", "5"))  # € risqués si pas de % (fallback)
RISK_PCT    = float(os.getenv("RISK_PCT", "0"))            # % du solde EUR (0 = désactivé)
LIMIT_BPS   = int(os.getenv("LIMIT_OFFSET_BPS", "10"))     # offset LIMIT en basis points (10 = 0,10 %)
MIN_QTY     = float(os.getenv("MIN_QTY", "0.00002"))       # min volume BTC sur Kraken (sécurité)
QTY_STEP    = float(os.getenv("QTY_STEP", "0.00001"))      # pas volume
PRICE_STEP  = float(os.getenv("PRICE_STEP", "0.5"))        # pas prix en EUR (sécurité)

# Mapping TV -> Kraken base (public/privé n’acceptent pas toujours les mêmes alias)
MAP = {"BTC": "XBT", "ETH": "XETH", "LTC": "XLTC"}

# === Routes simples (uptime/health) ===
@app.route("/")
def root_ok():
    return {"status": "ok"}, 200

@app.get("/health")
def health():
    return {"status": "ok"}, 200

# === Webhook TradingView ===
@app.post("/webhook")
def webhook():
    data = request.get_json(force=True, silent=False)
    app.logger.info(f"Webhook payload: {json.dumps(data, ensure_ascii=False)}")

    # lecture champs de base
    signal   = (data.get("signal") or "").upper().strip()
    symbol   = (data.get("symbol") or f"{BASE}/{QUOTE}").upper().strip()
    tf       = (data.get("timeframe") or data.get("time frame") or "").strip()

    if signal not in {"BUY", "SELL"}:
        return jsonify({"error": "invalid signal"}), 400

    # EX: "BTC/EUR" -> "XBT/EUR" (privé), "XBTEUR" (public)
    try:
        b, q = [s.strip().upper() for s in symbol.split("/")]
        base_k = MAP.get(b, b)   # XBT
        pair_priv = f"{base_k}/{q}"        # ex: XBT/EUR  (AddOrder)
        pair_pub  = f"{base_k}{q}".replace("XETH", "XETH").replace("XLTC", "XLTC")  # ex: XBTEUR (Ticker)
    except Exception:
        # quand TV envoie "BTCEUR" sans "/"
        base_k = MAP.get(symbol.replace("/", ""), symbol.replace("/", ""))
        pair_priv = f"{base_k}/{QUOTE}"
        pair_pub  = f"{base_k}{QUOTE}"

    try:
        # 1) prix spot
        last = fetch_price(pair_pub)

        # 2) quantité selon risque
        qty = calc_qty(last)  # en base (BTC)
        if qty < MIN_QTY:
            qty = MIN_QTY

        # 3) prix LIMIT avec léger offset
        # BUY: on place un peu sous le marché ; SELL: au-dessus
        off = LIMIT_BPS / 10000.0
        if signal == "BUY":
            limit_price = last * (1 - off)
        else:
            limit_price = last * (1 + off)

        # arrondis sécurité (pas d'échec pour pas)
        limit_price = round_to_step(limit_price, PRICE_STEP)
        qty         = round_to_step(qty, QTY_STEP)

        if PAPER:
            app.logger.info(
                f"PAPER {signal} {pair_priv} qty={qty} limit={limit_price} tf={tf}"
            )
            return jsonify({
                "paper": True, "signal": signal, "pair": pair_priv,
                "qty": float(qty), "limit": float(limit_price), "tf": tf
            }), 200

        # 4) ordre réel (ou validé côté Kraken si VALIDATE=1)
        res = place_order(signal, pair_priv, qty, limit_price, validate=VALIDATE)
        app.logger.info(f"REAL {signal} {pair_priv} qty={qty} limit={limit_price} validate={VALIDATE} RESULT={res}")
        return jsonify({
            "paper": False, "validate": VALIDATE, "result": res
        }), 200

    except Exception as e:
        app.logger.exception("Webhook error")
        return jsonify({"error": str(e)}), 500


# === Helpers publics Kraken ===
def fetch_price(pair_code: str) -> float:
    """
    Récupère le dernier prix (last trade) via /0/public/Ticker
    pair_code ex: 'XBTEUR' ou proche — on lit la 1ère clé retournée.
    """
    url = f"https://api.kraken.com/0/public/Ticker?pair={pair_code}"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    res = r.json()
    if res.get("error"):
        raise RuntimeError(res["error"])
    data = res["result"]
    k = next(iter(data.keys()))
    return float(data[k]["c"][0])


def round_to_step(value: float, step: float) -> float:
    # arrondi "vers le bas" au pas
    return float(int(value / step) * step)


def get_eur_balance() -> float:
    """Solde EUR (ZEUR) via /0/private/Balance ; 0 en cas d'erreur."""
    try:
        resp = kraken_private("Balance", {})
        # Kraken renvoie "ZEUR" / "EUR" selon comptes/legacy ; on couvre les 2
        for key in ("ZEUR", "EUR"):
            if key in resp:
                return float(resp[key])
        return 0.0
    except Exception as _:
        return 0.0


def calc_qty(price_eur: float) -> float:
    """
    € risqués -> quantité en base (BTC).
    Si RISK_PCT > 0: on prend min(RISK_EUR, RISK_PCT * solde EUR),
    sinon on utilise RISK_EUR.
    """
    risk_eur = RISK_EUR
    if RISK_PCT > 0:
        bal = get_eur_balance()
        pct_amt = bal * RISK_PCT
        if pct_amt > 0:
            risk_eur = min(RISK_EUR, pct_amt)
    raw_qty = risk_eur / max(price_eur, 1e-9)
    return float(f"{raw_qty:.8f}")  # 8 décimales


# === Helpers privés Kraken (HMAC + AddOrder) ===
KRAKEN_API_URL = "https://api.kraken.com"

def _kraken_sign(uri_path: str, data: dict, secret: str) -> str:
    postdata = urllib.parse.urlencode(data)
    encoded  = (str(data['nonce']) + postdata).encode()
    message  = uri_path.encode() + hashlib.sha256(encoded).digest()
    mac      = hmac.new(base64.b64decode(secret), message, hashlib.sha512)
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
        raise RuntimeError(f"Kraken error: {resp['error']}")
    return resp["result"]

def place_order(signal: str, pair_priv: str, qty: float, limit_price: float, validate: bool = True) -> dict:
    """
    Envoi AddOrder LIMIT.
    pair_priv ex: 'XBT/EUR'
    """
    side = "buy" if signal == "BUY" else "sell"
    data = {
        "pair": pair_priv,
        "type": side,
        "ordertype": "limit",
        "price": f"{limit_price:.2f}",
        "volume": f"{qty:.8f}",
        "validate": validate,
    }
    return kraken_private("AddOrder", data)


# === Run local (Render utilise gunicorn) ===
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
