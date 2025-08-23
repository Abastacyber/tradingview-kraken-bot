import os
import json
import math
import time
from datetime import datetime, timezone

from flask import Flask, request, jsonify
import requests
import krakenex

# =========================
#  Config via ENV
# =========================
KRAKEN_API_KEY    = os.getenv("KRAKEN_API_KEY", "")
KRAKEN_API_SECRET = os.getenv("KRAKEN_API_SECRET", "")

BASE  = os.getenv("BASE", "BTC").upper()     # ex: BTC
QUOTE = os.getenv("QUOTE", "EUR").upper()    # ex: EUR
PAIR  = f"{BASE}{QUOTE}"                     # ex: BTCEUR

SIZE_MODE = os.getenv("SIZE_MODE", "fixed_eur").lower()  # "fixed_eur" uniquement ici
FIXED_EUR_PER_TRADE = float(os.getenv("FIXED_EUR_PER_TRADE", "35"))  # € par trade

FEE_BUFFER_PCT   = float(os.getenv("FEE_BUFFER_PCT", "0.002"))  # 0.002 = 0.2%
ALERT_PRICE_TOL  = float(os.getenv("ALERT_PRICE_TOL", "0.5"))   # tolérance % vs prix alert
MAX_OPEN_POS     = int(os.getenv("MAX_OPEN_POS", "1"))          # non utilisé ici (spot simple)

# Réessais réseau
HTTP_RETRIES     = int(os.getenv("HTTP_RETRIES", "3"))
HTTP_BACKOFF_S   = float(os.getenv("HTTP_BACKOFF_S", "0.7"))

# Réserve “poussière” pour éviter le plein solde
DUST_RESERVE_BASE = float(os.getenv("DUST_RESERVE_BASE", "0.00001"))  # BTC à laisser de côté
DUST_RESERVE_QUOTE = float(os.getenv("DUST_RESERVE_QUOTE", "1.0"))    # EUR à laisser de côté

# =========================
#  Kraken client
# =========================
api = krakenex.API(key=KRAKEN_API_KEY, secret=KRAKEN_API_SECRET)

app = Flask(__name__)

# =========================
#  Utils
# =========================
class KrakenNetworkError(Exception):
    pass

def kraken_query_public(method, data=None, retries=HTTP_RETRIES):
    for i in range(retries):
        try:
            resp = api.query_public(method, data or {})
            return resp
        except requests.exceptions.RequestException as e:
            if i == retries - 1:
                raise KrakenNetworkError(str(e))
            time.sleep(HTTP_BACKOFF_S * (i + 1))

def kraken_query_private(method, data=None, retries=HTTP_RETRIES):
    for i in range(retries):
        try:
            resp = api.query_private(method, data or {})
            return resp
        except requests.exceptions.RequestException as e:
            if i == retries - 1:
                raise KrakenNetworkError(str(e))
            time.sleep(HTTP_BACKOFF_S * (i + 1))

def get_pair_info(pair):
    """Récupère lot_decimals et ordermin (min size) depuis Kraken."""
    resp = kraken_query_public("AssetPairs", {"pair": pair})
    if "error" in resp and resp["error"]:
        raise Exception(f"Kraken error AssetPairs: {resp['error']}")
    # clé interne (ex: XXBTZEUR)
    k = list(resp["result"].keys())[0]
    info = resp["result"][k]
    lot_decimals = info.get("lot_decimals", 8)
    ordermin = float(info.get("ordermin", "0"))
    return lot_decimals, ordermin

def round_qty(qty, lot_decimals):
    factor = 10 ** lot_decimals
    return math.floor(qty * factor) / factor

def get_price(pair):
    """Dernier prix (mid entre ask/bid si possible)."""
    resp = kraken_query_public("Ticker", {"pair": pair})
    if "error" in resp and resp["error"]:
        raise Exception(f"Kraken error Ticker: {resp['error']}")
    k = list(resp["result"].keys())[0]
    # c = last trade [price, volume], a = ask [price,...], b = bid [price,...]
    last = float(resp["result"][k]["c"][0])
    ask  = float(resp["result"][k]["a"][0])
    bid  = float(resp["result"][k]["b"][0])
    mid  = (ask + bid) / 2.0 if ask and bid else last
    return mid

def get_balances():
    resp = kraken_query_private("Balance")
    if "error" in resp and resp["error"]:
        raise Exception(f"Kraken error Balance: {resp['error']}")
    bal = resp["result"]
    base_key  = f"X{BASE}" if f"X{BASE}" in bal else BASE
    quote_key = f"Z{QUOTE}" if f"Z{QUOTE}" in bal else QUOTE
    base_bal  = float(bal.get(base_key, "0"))
    quote_bal = float(bal.get(quote_key, "0"))
    return base_bal, quote_bal

def safe_notional_eur():
    """Renvoie le notional EUR utilisable (après réserve quote + buffer frais)."""
    _, quote_bal = get_balances()
    max_spend = max(0.0, quote_bal - DUST_RESERVE_QUOTE)
    target = FIXED_EUR_PER_TRADE
    raw = min(target, max_spend)
    # marge de frais
    return raw * (1.0 - FEE_BUFFER_PCT)

def safe_qty_from_eur(price, lot_decimals, ordermin):
    """Convertit un montant EUR en quantité base (BTC), arrondie et >= ordermin si possible."""
    if price <= 0:
        return 0.0
    qty = safe_notional_eur() / price
    qty = round_qty(qty, lot_decimals)
    if qty < ordermin:
        return 0.0
    return qty

def safe_qty_for_sell(lot_decimals, ordermin):
    """Quantité base dispo pour SELL (en tenant compte de la dust réserve)."""
    base_bal, _ = get_balances()
    sellable = max(0.0, base_bal - DUST_RESERVE_BASE)
    sellable = round_qty(sellable, lot_decimals)
    if sellable < ordermin:
        return 0.0
    # On ne veut pas forcément TOUT vendre : limite à la quantité équivalente à FIXED_EUR_PER_TRADE si possible
    price = get_price(PAIR)
    max_qty_vs_eur = (FIXED_EUR_PER_TRADE * (1.0 - FEE_BUFFER_PCT)) / price
    max_qty_vs_eur = round_qty(max_qty_vs_eur, lot_decimals)
    if max_qty_vs_eur >= ordermin:
        return max(ordermin, min(sellable, max_qty_vs_eur))
    # Sinon, si le max EUR est trop petit, on vend ce qui est vendable si > ordermin
    return sellable

def place_market_order(side, pair, qty):
    """Place un ordre marché (volume en base)."""
    if qty <= 0:
        return {"error": ["EOrder:Invalid volume"], "result": {}}

    data = {
        "pair": pair,
        "type": side,                 # "buy" | "sell"
        "ordertype": "market",
        "volume": f"{qty:.10f}",
        "oflags": "viqc"              # value in quote currency (ok pour market)
    }
    resp = kraken_query_private("AddOrder", data)
    return resp

def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts} [INFO] {msg}", flush=True)

# =========================
#  Webhook
# =========================
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        payload = request.get_json(force=True, silent=False)
    except Exception:
        # si TradingView a envoyé du texte brut
        try:
            payload = json.loads(request.data.decode("utf-8"))
        except Exception:
            return jsonify({"ok": False, "msg": "invalid json"}), 400

    log(f"Raw alert: {json.dumps(payload)}")

    signal = str(payload.get("signal", "")).upper()
    symbol = str(payload.get("symbol", "")).upper().replace(":", "").replace("/", "")
    # on ignore symbol/timeframe venant de TV si tu veux forcer le PAIR via env
    pair = PAIR if PAIR else symbol

    # tolérance prix (facultative, juste informative)
    alert_price = None
    try:
        alert_price = float(payload.get("price")) if payload.get("price") is not None else None
    except Exception:
        alert_price = None

    # récup infos pair
    try:
        lot_decimals, ordermin = get_pair_info(pair)
    except Exception as e:
        log(f"ERROR AssetPairs: {e}")
        return jsonify({"ok": False, "msg": "assetpairs error"}), 200  # on répond 200 pour éviter retry TV

    # prix spot
    try:
        price = get_price(pair)
    except Exception as e:
        log(f"ERROR Ticker: {e}")
        return jsonify({"ok": False, "msg": "ticker error"}), 200

    # log prix & déviation
    if alert_price:
        dev = abs(price - alert_price) / alert_price * 100.0
        log(f"Alert price={alert_price:.2f} vs spot={price:.2f} (dev={dev:.2f}%)")

    # ------------- ROUTAGE -------------
    if signal == "BUY":
        # Dimensionnement en EUR fixes -> qty base
        qty = safe_qty_from_eur(price, lot_decimals, ordermin)
        if qty <= 0:
            log(f"BUY skipped: not enough EUR after reserves/fees or below ordermin. "
                f"ordermin={ordermin}, lot_decimals={lot_decimals}")
            return jsonify({"ok": True, "skipped": "not_enough_eur"}), 200

        log(f"==> ORDER BUY {qty:.10f} {pair} ~{qty*price:.2f} {QUOTE} @ ~{price:.2f}")
        resp = place_market_order("buy", pair, qty)

        if resp.get("error"):
            log(f"Kraken ERROR (BUY): {resp['error']}")
            return jsonify({"ok": False, "kraken_error": resp["error"]}), 200

        log(f"BUY ok: {resp['result']}")
        return jsonify({"ok": True, "result": resp["result"]}), 200

    elif signal == "SELL":
        # Vérifie le stock et adapte
        qty = safe_qty_for_sell(lot_decimals, ordermin)
        if qty <= 0:
            log(f"SELL skipped: not enough {BASE} available after dust reserve "
                f"or below ordermin. ordermin={ordermin}, lot_decimals={lot_decimals}")
            return jsonify({"ok": True, "skipped": "not_enough_base"}), 200

        log(f"==> ORDER SELL {qty:.10f} {pair} ~{qty*price:.2f} {QUOTE} @ ~{price:.2f}")
        resp = place_market_order("sell", pair, qty)

        if resp.get("error"):
            # Si jamais “Insufficient funds” malgré le check, on log et on sort proprement
            log(f"Kraken ERROR (SELL): {resp['error']}")
            return jsonify({"ok": False, "kraken_error": resp["error"]}), 200

        log(f"SELL ok: {resp['result']}")
        return jsonify({"ok": True, "result": resp["result"]}), 200

    else:
        log(f"Signal inconnu: {signal}")
        return jsonify({"ok": True, "skipped": "unknown_signal"}), 200


@app.route("/health", methods=["GET", "HEAD"])
def health():
    return "ok", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
