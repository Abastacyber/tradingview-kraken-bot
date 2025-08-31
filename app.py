import os, json, math
from flask import Flask, request, jsonify
import ccxt

app = Flask(_name_)

# ---------- Config depuis variables d'env ----------
EXCHANGE_NAME = os.getenv("EXCHANGE", "phemex").lower()      # "phemex"
SYMBOL        = os.getenv("SYMBOL", "BTCUSDT")               # "BTCUSDT"
QUOTE         = os.getenv("QUOTE_SYMBOL", "USDT")            # "USDT"
ORDER_TYPE    = os.getenv("ORDER_TYPE", "market")            # "market"
PAPER_MODE    = int(os.getenv("PAPER_MODE", "0"))            # 1 = paper, 0 = live

# Montant fixe par trade (en EUR) si SIZE_MODE=fixed_eur
SIZE_MODE     = os.getenv("SIZE_MODE", "fixed_eur")          # "fixed_eur" | "risk_pct" (non utilisé ici)
FIXED_EUR     = float(os.getenv("FIXED_QUOTE_PER_TRADE", "20"))
MIN_EUR       = float(os.getenv("MIN_EUR_PER_TRADE", "10"))  # seuil mini
FEE_BUFFER    = float(os.getenv("FEE_BUFFER_PCT", "0.002"))  # 0.2% de marge

API_KEY       = os.getenv("PHEMEX_API_KEY", "")
API_SECRET    = os.getenv("PHEMEX_API_SECRET", "")

# ---------- Exchange ----------
def build_exchange():
    params = {"enableRateLimit": True}
    if not PAPER_MODE:
        params["apiKey"] = API_KEY
        params["secret"] = API_SECRET
    return getattr(ccxt, EXCHANGE_NAME)(params)

# ---------- Utilitaires ----------
def to_symbol_ccxt(sym):
    # "BTCUSDT" -> "BTC/USDT"
    if "/" in sym: 
        return sym
    if sym.endswith(QUOTE):
        base = sym[:-len(QUOTE)]
        return f"{base}/{QUOTE}"
    # fallback
    return sym

CCXT_SYMBOL = to_symbol_ccxt(SYMBOL)

def compute_amount_eur(exchange, symbol_ccxt, euros):
    ticker = exchange.fetch_ticker(symbol_ccxt)
    last = float(ticker["last"])
    amount = (euros * (1.0 - FEE_BUFFER)) / last
    # respecter les pas de quantité de l’exchange
    return float(exchange.amount_to_precision(symbol_ccxt, amount))

# ---------- Endpoints ----------
@app.get("/health")
def health():
    return jsonify({"status": "ok"})

@app.post("/webhook")
def webhook():
    try:
        raw = request.get_data()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            return jsonify({"status":"error","message":"Invalid JSON"}), 400

        signal = (payload.get("signal") or "").upper()  # "BUY" | "SELL"
        if signal not in ("BUY", "SELL"):
            return jsonify({"status":"ignored","message":"signal must be BUY or SELL"}), 400

        exchange = build_exchange()
        symbol = CCXT_SYMBOL

        # sizing
        euros = max(FIXED_EUR, MIN_EUR)
        amount = compute_amount_eur(exchange, symbol, euros)

        if PAPER_MODE:
            app.logger.info(f"[PAPER] {signal} {symbol} amount={amount}")
            return jsonify({"status":"ok","paper":True,"signal":signal,"symbol":symbol,"amount":amount})

        # LIVE
        if ORDER_TYPE != "market":
            return jsonify({"status":"error","message":"Only market supported here"}), 400

        if signal == "BUY":
            order = exchange.create_market_buy_order(symbol, amount)
        else:
            order = exchange.create_market_sell_order(symbol, amount)

        return jsonify({"status":"ok","paper":False,"order":order})

    except ccxt.InsufficientFunds as e:
        app.logger.error(f"InsufficientFunds: {str(e)}")
        return jsonify({"status":"error","message":"Insufficient funds"}), 502
    except ccxt.BaseError as e:
        app.logger.error(f"ccxt error: {str(e)}")
        return jsonify({"status":"error","message":"ccxt error","detail":str(e)}), 502
    except Exception as e:
        app.logger.exception("Unhandled error")
        return jsonify({"status":"error","message":str(e)}), 500

# Lanceur local (Render utilisera gunicorn)
if _name_ == "_main_":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
