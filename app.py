# app.py
import os
import json
import logging
from decimal import Decimal, ROUND_DOWN

from flask import Flask, request, jsonify
import ccxt

# ========= Config & logs =========
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO))
log = logging.getLogger("tv-phemex")

app = Flask(__name__)

# ========= Env vars attendues =========
EXCHANGE_NAME           = os.getenv("EXCHANGE", "phemex").lower()     # "phemex"
PHEMEX_API_KEY          = os.getenv("PHEMEX_API_KEY", "")
PHEMEX_API_SECRET       = os.getenv("PHEMEX_API_SECRET", "")
BASE_SYMBOL             = os.getenv("BASE_SYMBOL", "BTC")              # "BTC"
QUOTE_SYMBOL            = os.getenv("QUOTE_SYMBOL", "USDT")            # "USDT"
SYMBOL_ENV              = os.getenv("SYMBOL", f"{BASE_SYMBOL}{QUOTE_SYMBOL}")  # "BTCUSDT"
ORDER_TYPE              = os.getenv("ORDER_TYPE", "market").lower()    # "market"
SIZE_MODE               = os.getenv("SIZE_MODE", "fixed_eur")          # "fixed_eur"
FIXED_QUOTE_PER_TRADE   = Decimal(os.getenv("FIXED_QUOTE_PER_TRADE", "15"))
MIN_EUR_PER_TRADE       = Decimal(os.getenv("MIN_EUR_PER_TRADE", "10"))
FEE_BUFFER_PCT          = Decimal(os.getenv("FEE_BUFFER_PCT", "0.002"))  # 0.2%
PAPER_MODE              = int(os.getenv("PAPER_MODE", "0"))            # 1 = pas d'envoi d'ordres

# ccxt veut "BTC/USDT"
def to_ccxt_symbol(sym: str) -> str:
    s = sym.replace(":", "").replace("-", "").upper()
    if "/" in s:
        return s
    # ex: BTCUSDT -> BTC/USDT
    if s.endswith(QUOTE_SYMBOL.upper()):
        return f"{BASE_SYMBOL.upper()}/{QUOTE_SYMBOL.upper()}"
    # fallback
    return f"{BASE_SYMBOL.upper()}/{QUOTE_SYMBOL.upper()}"

CCXT_SYMBOL = to_ccxt_symbol(SYMBOL_ENV)

# ========= Exchange =========
def make_exchange():
    if EXCHANGE_NAME != "phemex":
        raise RuntimeError("Pour l’instant seul Phemex est supporté (EXCHANGE=phemex).")
    conf = {"enableRateLimit": True}
    if not PAPER_MODE:
        conf.update({"apiKey": PHEMEX_API_KEY, "secret": PHEMEX_API_SECRET})
    return ccxt.phemex(conf)

exchange = make_exchange()

# ========= Utils =========
def quantize_amount(amount: Decimal, step: Decimal) -> Decimal:
    """
    Tronque amount au pas 'step' (ex: 0.0001) pour matcher le lot minimum.
    """
    if step <= 0:
        return amount
    # nombre de décimales du step
    q = Decimal(str(step)).normalize()
    decimals = abs(q.as_tuple().exponent)
    return amount.quantize(Decimal(10) ** -decimals, rounding=ROUND_DOWN)

def get_price(symbol: str) -> Decimal:
    ticker = exchange.fetch_ticker(symbol)
    # mid price: (bid+ask)/2 si possibles, sinon last
    bid = Decimal(str(ticker.get("bid") or 0))
    ask = Decimal(str(ticker.get("ask") or 0))
    last = Decimal(str(ticker.get("last") or 0))
    if bid > 0 and ask > 0:
        return (bid + ask) / Decimal("2")
    return last if last > 0 else Decimal("0")

def compute_amount_eur_fixed(symbol: str, eur_size: Decimal) -> Decimal:
    price = get_price(symbol)
    if price <= 0:
        raise RuntimeError("Impossible de récupérer le prix.")
    # sur une paire USDT, on assimile 1 USDT ≈ 1 EUR (tu finances en EUR mais trades en USDT côté exchange)
    # donc 'eur_size' agit comme taille en quote (USDT).
    amount = eur_size / price                           # quantité de base (ex: en BTC)
    amount *= (Decimal("1") - FEE_BUFFER_PCT)           # buffer frais
    # adapter au lot min
    market = exchange.market(symbol)
    step = Decimal(str(market.get("limits", {}).get("amount", {}).get("min", 0))) or Decimal("0")
    # si min non fourni, tente precision
    if step == 0:
        precision = market.get("precision", {}).get("amount")
        step = Decimal(str(10 ** -(precision))) if precision is not None else Decimal("0.00000001")
    return quantize_amount(amount, step)

def place_order(signal: str) -> dict:
    symbol = CCXT_SYMBOL

    # taille
    if SIZE_MODE == "fixed_eur":
        notional = FIXED_QUOTE_PER_TRADE
        if notional < MIN_EUR_PER_TRADE:
            raise RuntimeError(f"FIXED_QUOTE_PER_TRADE ({notional}€) < MIN_EUR_PER_TRADE ({MIN_EUR_PER_TRADE}€).")
        amount = compute_amount_eur_fixed(symbol, notional)
    else:
        raise RuntimeError(f"SIZE_MODE non supporté: {SIZE_MODE}")

    if amount <= 0:
        raise RuntimeError("Quantité calculée nulle.")

    log.info(f"Signal={signal} | Symbol={symbol} | Amount={amount}")

    if PAPER_MODE:
        return {
            "paper": True,
            "signal": signal,
            "symbol": symbol,
            "amount": float(amount),
        }

    # Exécution
    if ORDER_TYPE != "market":
        raise RuntimeError("Seul ORDER_TYPE=market est géré ici.")
    if signal == "BUY":
        return exchange.create_market_buy_order(symbol, float(amount))
    elif signal == "SELL":
        return exchange.create_market_sell_order(symbol, float(amount))
    else:
        raise RuntimeError("Signal inconnu (attendu: BUY ou SELL).")

# ========= Routes =========
@app.get("/health")
def health():
    return {"status": "ok", "exchange": EXCHANGE_NAME, "symbol": CCXT_SYMBOL}

@app.post("/webhook")
def webhook():
    """
    Payload TradingView attendu, par ex. :
    {
      "signal": "BUY",               # ou "SELL"
      "symbol": "BTCUSDT"            # optionnel, on utilisera sinon ENV SYMBOL
    }
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
        signal = str(data.get("signal", "")).upper().strip()
        if not signal:
            return jsonify({"error": "champ 'signal' requis (BUY|SELL)"}), 400

        # (optionnel) si TV envoie un symbole différent, on l’accepte:
        sym_in = data.get("symbol")
        if sym_in:
            global CCXT_SYMBOL
            CCXT_SYMBOL = to_ccxt_symbol(sym_in)

        order = place_order(signal)
        return jsonify(order), 200

    except ccxt.BaseError as e:
        # Erreurs ccxt lisibles (fonds insuffisants, etc.)
        log.exception("ccxt error")
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log.exception("webhook error")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    # Pour un run local éventuel (Render utilisera gunicorn)
    port = int(os.getenv("PORT", "3000"))
    app.run(host="0.0.0.0", port=port)
