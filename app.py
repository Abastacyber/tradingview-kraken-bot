import os, time, json, math
from flask import Flask, request, jsonify
import ccxt

app = Flask(_name_)

# ----- ENV -----
API_KEY = os.getenv("PHEMEX_API_KEY", "")
API_SECRET = os.getenv("PHEMEX_API_SECRET", "")
BASE = os.getenv("BASE_SYMBOL", "BTC")
QUOTE = os.getenv("QUOTE_SYMBOL", "USDT")
SYMBOL = f"{BASE}/{QUOTE}"      # ccxt format
EX_SYMBOL = f"{BASE}{QUOTE}"    # ex: BTCUSDT
FIXED_QUOTE = float(os.getenv("FIXED_QUOTE_PER_TRADE", "25"))  # budget en USDT
MIN_QUOTE = float(os.getenv("MIN_QUOTE_PER_TRADE", "10"))      # sécurité

# ----- Phemex via ccxt -----
exchange = ccxt.phemex({
    "apiKey": API_KEY,
    "secret": API_SECRET,
    "enableRateLimit": True,
})
exchange.options["defaultType"] = "spot"

def _price():
    t = exchange.fetch_ticker(SYMBOL)
    return float(t["last"])

def _lot_size(amount):
    # récupère le pas de taille si dispo (sinon arrondi à 6 déc.)
    try:
        exm = exchange.load_markets()
        step = exm[SYMBOL]["limits"]["amount"]["min"] or 0.000001
        # arrondi au pas
        digits = max(0, -int(math.floor(math.log10(step))))
        return float(f"{amount:.{min(digits,8)}f}")
    except Exception:
        return float(f"{amount:.6f}")

@app.route("/health", methods=["GET","HEAD"])
def health():
    return jsonify({"status":"ok","time":int(time.time())})

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True, silent=False)
    except Exception:
        return jsonify({"status":"error","message":"JSON invalide"}), 400

    # lecture flexible du champ action
    action = (data.get("action") or data.get("signal") or "").upper().strip()
    symbol = (data.get("symbol") or EX_SYMBOL).upper().strip()
    qty = data.get("quantity")  # optionnel (en BASE)

    if symbol != EX_SYMBOL:
        return jsonify({"status":"error","message":f"Symbole inattendu: {symbol}"}), 400
    if action not in ("BUY","SELL"):
        return jsonify({"status":"error","message":"Champ 'action'/'signal' requis (BUY/SELL)"}), 400

    # calcule quantité si absente
    if qty is None:
        px = _price()
        quote_to_use = max(MIN_QUOTE, FIXED_QUOTE)
        qty = _lot_size(quote_to_use / px)

    # safety
    if qty <= 0:
        return jsonify({"status":"error","message":"Quantité <= 0"}), 400

    app.logger.info(f"Ordre {action} {qty} {BASE} sur {EX_SYMBOL}")

    # place ordre au marché
    try:
        if action == "BUY":
            order = exchange.create_market_buy_order(SYMBOL, qty)
        else:
            order = exchange.create_market_sell_order(SYMBOL, qty)
    except ccxt.BaseError as e:
        app.logger.exception("Erreur ccxt")
        return jsonify({"status":"error","message":str(e)}), 502

    return jsonify({"status":"ok","order":order})
