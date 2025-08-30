import os, json, time
from flask import Flask, request, jsonify
import ccxt

# --- Flask app que Gunicorn doit voir ---
app = Flask(_name_)   # <-- IMPORTANT : la variable s'appelle bien "app"

# --- ENV / Defaults ---
BASE = os.getenv("BASE_SYMBOL", "BTC").upper()
QUOTE = os.getenv("QUOTE_SYMBOL", "USDT").upper()
SYMBOL = f"{BASE}/{QUOTE}"

# sizing simple: montant fixe par trade en QUOTE (USDT)
FIXED_QUOTE_PER_TRADE = float(os.getenv("FIXED_QUOTE_PER_TRADE", "25"))
MIN_QUOTE_PER_TRADE   = float(os.getenv("MIN_QUOTE_PER_TRADE", "10"))

# clés Phemex
PHEMEX_KEY    = os.getenv("PHEMEX_API_KEY", "")
PHEMEX_SECRET = os.getenv("PHEMEX_API_SECRET", "")

# ccxt client Phemex Spot
def make_client():
    if not PHEMEX_KEY or not PHEMEX_SECRET:
        raise RuntimeError("Phemex API key/secret manquants")
    exchange = ccxt.phemex({
        "apiKey": PHEMEX_KEY,
        "secret": PHEMEX_SECRET,
        "enableRateLimit": True,
        "options": {
            "defaultType": "spot",  # spot et non perp/futures
        }
    })
    return exchange

@app.route("/")
def index():
    return "OK - TV → Phemex Spot webhook en ligne"

@app.route("/health")
def health():
    return jsonify({"status": "ok", "time": int(time.time())})

@app.route("/webhook", methods=["POST"])
def webhook():
    """
    Payload attendu (exemples) :
    - TradingView (message custom) => {"signal":"BUY","symbol":"BTCUSDT","timeframe":"5"}
    - Test curl minimal           => {"signal":"BUY","quantity":0.001}
    Champs utiles : signal (BUY/SELL), symbol (BTCUSDT facultatif), quantity (facultative en BASE),
                    price (facultatif), tp_pct/sl_pct/trailing... ignorés côté exécution directe marché.
    """
    try:
        data = request.get_json(force=True, silent=False)
    except Exception as e:
        return jsonify({"status": "error", "message": f"JSON invalide: {e}"}), 400

    # sécurité : on logge "propre"
    app.logger.info(f"Payload reçu: {json.dumps(data, ensure_ascii=False)}")

    signal = str(data.get("signal", "")).upper()
    if signal not in ("BUY", "SELL"):
        return jsonify({"status": "error", "message": "Champ 'signal' manquant ou invalide (BUY/SELL)"}), 400

    # symbole : accepte "BTCUSDT" ou on retombe sur env SYMBOL
    symbol_raw = str(data.get("symbol", "")).upper().replace("-", "").replace("/", "")
    symbol = SYMBOL if not symbol_raw else f"{symbol_raw[:-4]}/{symbol_raw[-4:]}" if symbol_raw.endswith("USDT") else SYMBOL

    # quantité : soit fournie en BASE, soit on calcule depuis FIXED_QUOTE_PER_TRADE
    qty_base = data.get("quantity")
    try:
        client = make_client()
        market = client.market(symbol)  # charge métadonnées (précisions, min)
        client.load_markets()

        if qty_base is None:
            # calcul depuis quote fixe (USDT)
            ticker = client.fetch_ticker(symbol)
            last = float(ticker["last"])
            quote_amt = max(FIXED_QUOTE_PER_TRADE, MIN_QUOTE_PER_TRADE)
            qty_base = quote_amt / last

        qty_base = float(qty_base)
        if qty_base <= 0:
            return jsonify({"status": "error", "message": "Quantité <= 0"}), 400

        # normalise quantité selon stepSize
        amount = client.amount_to_precision(symbol, qty_base)

        side = "buy" if signal == "BUY" else "sell"
        order = client.create_order(symbol=symbol, type="market", side=side, amount=amount)

        app.logger.info(f"Ordre Phemex OK: {order}")
        return jsonify({
            "status": "success",
            "exchange": "phemex-spot",
            "symbol": symbol,
            "side": side.upper(),
            "amount": amount,
            "order_id": order.get("id")
        })

    except ccxt.InsufficientFunds as e:
        return jsonify({"status": "error", "message": f"Fonds insuffisants: {e}"}), 400
    except ccxt.InvalidOrder as e:
        return jsonify({"status": "error", "message": f"Ordre invalide: {e}"}), 400
    except ccxt.ExchangeError as e:
        return jsonify({"status": "error", "message": f"Erreur bourse: {e}"}), 502
    except Exception as e:
        app.logger.exception("Erreur serveur")
        return jsonify({"status": "error", "message": f"Erreur serveur: {e}"}), 500

if _name_ == "_main_":
    # pour exécution locale éventuelle
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
