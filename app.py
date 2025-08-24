# app.py
import os
import logging
from decimal import Decimal, ROUND_DOWN
from datetime import datetime, timezone
from flask import Flask, request, jsonify

import krakenex

# ========= Logging propre =========
logger = logging.getLogger("tv-kraken")
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(h)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
logger.propagate = False

app = Flask(__name__)

# ========= Config depuis l'env =========
BASE = os.getenv("BASE", "BTC").upper()       # ex: BTC
QUOTE = os.getenv("QUOTE", "EUR").upper()     # ex: EUR
PAIR = ("XBT" if BASE == "BTC" else BASE) + QUOTE  # ex: XBTEUR

SIZE_MODE = os.getenv("SIZE_MODE", "fixed_eur").lower()  # fixed_eur ou auto_size
FIXED_EUR_PER_TRADE = Decimal(os.getenv("FIXED_EUR_PER_TRADE", "50"))
MIN_EUR_PER_TRADE = Decimal(os.getenv("MIN_EUR_PER_TRADE", "10"))
BTC_RESERVE = Decimal(os.getenv("BTC_RESERVE", "0.00005"))
FEE_BUFFER_PCT = Decimal(os.getenv("FEE_BUFFER_PCT", "0.002"))  # 0.2%

COOLDOWN_SEC = int(os.getenv("COOLDOWN_SEC", "0"))
_last_fire_ts = 0.0

# ========= Kraken client =========
KRAKEN_API_KEY = os.getenv("KRAKEN_API_KEY", "")
KRAKEN_API_SECRET = os.getenv("KRAKEN_API_SECRET", "")
k = krakenex.API(key=KRAKEN_API_KEY, secret=KRAKEN_API_SECRET)

def iso_now():
    return datetime.now(timezone.utc).isoformat()

def log_tag_for_request():
    ua = (request.headers.get("User-Agent") or "").lower()
    path = request.path
    method = request.method
    tag = None
    if "uptimerobot" in ua:
        tag = "PING Uptime"
    elif "google-apps-script" in ua or "script.google.com" in ua or "beanserver" in ua:
        tag = "PING Google Script"
    elif path == "/health":
        tag = "HEALTH"
    elif path == "/webhook" and method == "POST":
        tag = "ALERTE TradingView"
    if tag:
        logger.info(tag)

# ========= utilitaires Kraken =========
def get_balances():
    resp = k.query_private("Balance")
    if resp.get("error"):
        raise RuntimeError(f"Kraken Balance error: {resp['error']}")
    return {k_: Decimal(v) for k_, v in resp["result"].items()}

def place_market_order(side: str, volume_btc: Decimal):
    """
    side: 'buy' ou 'sell'
    volume_btc: quantité en BTC (Kraken attend un volume base)
    """
    # conformer au lot min BTC sur Kraken (5 décimales est généralement OK)
    vol_str = str(volume_btc.quantize(Decimal("0.00001"), rounding=ROUND_DOWN))

    data = {
        "pair": PAIR,                # ex: XBTEUR
        "type": side,                # buy / sell
        "ordertype": "market",
        "volume": vol_str,
        # tu peux ajouter "oflags": "fciq" pour "post-only" sur limit, pas utile ici
        # "validate": True   # pour tester sans exécuter
    }
    logger.info(f"ORDER {side.upper()} {PAIR} vol={vol_str}")
    resp = k.query_private("AddOrder", data)
    if resp.get("error"):
        raise RuntimeError(f"Kraken AddOrder error: {resp['error']}")
    logger.info(f"KRAKEN OK | {resp.get('result')}")
    return resp["result"]

@app.get("/health")
def health():
    log_tag_for_request()
    return jsonify({"status": "ok", "time": iso_now()}), 200

@app.post("/webhook")
def webhook():
    global _last_fire_ts
    try:
        log_tag_for_request()

        data = request.get_json(force=True, silent=True) or {}
        signal = str(data.get("signal", "")).upper()
        symbol = str(data.get("symbol", ""))
        timeframe = str(data.get("timeframe", ""))
        price = data.get("price")  # peut être string ou nombre

        logger.info(f"ALERT {signal} | {symbol} {timeframe} | price={price}")

        # --- Cooldown simple ---
        if COOLDOWN_SEC > 0:
            import time
            now = time.time()
            if now - _last_fire_ts < COOLDOWN_SEC:
                left = int(COOLDOWN_SEC - (now - _last_fire_ts))
                logger.info(f"Cooldown actif -> alerte ignorée ({left}s restants)")
                return jsonify({"ok": True, "skipped": "cooldown"}), 200

        # --- Balances ---
        balances = get_balances()
        bal_eur = balances.get("ZEUR", Decimal("0"))
        bal_btc = balances.get("XXBT", Decimal("0")) or balances.get("XBT", Decimal("0"))

        # --- BUY (on dépense des EUR) ---
        if signal == "BUY":
            # sécurité montant min
            eur_to_spend = FIXED_EUR_PER_TRADE if SIZE_MODE == "fixed_eur" else bal_eur
            eur_to_spend = Decimal(eur_to_spend)

            if eur_to_spend < MIN_EUR_PER_TRADE:
                return jsonify({"ok": False, "reason": "MIN_EUR_PER_TRADE"}), 400
            if bal_eur <= 0:
                return jsonify({"ok": False, "reason": "NO_EUR_BALANCE"}), 400

            # on applique un petit buffer frais
            eur_net = eur_to_spend * (Decimal("1") - FEE_BUFFER_PCT)

            # si TradingView fournit un price, on s'en sert pour approx volume
            if price is None:
                return jsonify({"ok": False, "reason": "MISSING_PRICE_FOR_SIZING"}), 400
            px = Decimal(str(price))
            vol_btc = (eur_net / px).quantize(Decimal("0.00001"), rounding=ROUND_DOWN)

            # check qu'on a assez d'EUR
            if eur_to_spend > bal_eur:
                eur_to_spend = bal_eur
                vol_btc = (eur_to_spend * (Decimal("1") - FEE_BUFFER_PCT) / px).quantize(
                    Decimal("0.00001"), rounding=ROUND_DOWN
                )

            result = place_market_order("buy", vol_btc)
            _last_fire_ts = __import__("time").time()
            return jsonify({"ok": True, "kraken": result}), 200

        # --- SELL (on vend du BTC) ---
        elif signal == "SELL":
            btc_sellable = (bal_btc - BTC_RESERVE)
            if btc_sellable <= Decimal("0"):
                return jsonify({"ok": False, "reason": "NO_BTC_TO_SELL"}), 400

            # si tu veux vendre un pourcentage fixe, adapte ici
            # ici, on vend tout ce qui est au-delà de la réserve
            vol_btc = btc_sellable.quantize(Decimal("0.00001"), rounding=ROUND_DOWN)
            result = place_market_order("sell", vol_btc)
            _last_fire_ts = __import__("time").time()
            return jsonify({"ok": True, "kraken": result}), 200

        else:
            return jsonify({"ok": False, "reason": "UNKNOWN_SIGNAL"}), 400

    except Exception as e:
        logger.error(f"ERROR webhook: {type(e).__name__}: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
