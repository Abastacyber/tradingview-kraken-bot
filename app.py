import os
import json
import math
import logging
from typing import Any, Dict, Tuple

from flask import Flask, request, jsonify

# ccxt pour l'exchange
import ccxt

# ========= Helpers ENV =========
def env_str(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return v if v not in (None, "") else default

def env_float(name: str, default: float = 0.0) -> float:
    try:
        return float(env_str(name, str(default)))
    except Exception:
        return float(default)

def env_int(name: str, default: int = 0) -> int:
    try:
        return int(float(env_str(name, str(default))))
    except Exception:
        return int(default)

# ========= Lecture ENV (défauts sûrs) =========
LOG_LEVEL              = env_str("LOG_LEVEL", "INFO").upper()
EXCHANGE_NAME          = env_str("EXCHANGE", "phemex").lower()   # "phemex"
BASE_SYMBOL            = env_str("BASE_SYMBOL", "BTC")           # "BTC"
QUOTE_SYMBOL           = env_str("QUOTE_SYMBOL", "USDT")         # "USDT"
SYMBOL                 = env_str("SYMBOL", "BTCUSDT")            # "BTCUSDT"

ORDER_TYPE             = env_str("ORDER_TYPE", "market")         # "market" (géré ici)
SIZE_MODE              = env_str("SIZE_MODE", "fixed_eur")       # informatif
FIXED_QUOTE_PER_TRADE  = env_float("FIXED_QUOTE_PER_TRADE", 15)  # Montant en QUOTE (ex: 15 USDT)
FEE_BUFFER_PCT         = env_float("FEE_BUFFER_PCT", 0.002)      # 0,2 % par défaut
MIN_EUR_PER_TRADE      = env_float("MIN_EUR_PER_TRADE", 10)      # garde‑fou non bloquant
BTC_RESERVE            = env_float("BTC_RESERVE", 0.00005)       # réserve base pour éviter le sold‑out

PHEMEX_API_KEY         = env_str("PHEMEX_API_KEY")
PHEMEX_API_SECRET      = env_str("PHEMEX_API_SECRET")

# ========= Logs =========
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO))
log = logging.getLogger("tv-phemex")

# ========= Flask =========
app = Flask(__name__)

# ========= Exchange (ccxt) =========
def _make_exchange():
    # Phemex en spot
    if EXCHANGE_NAME != "phemex":
        raise RuntimeError(f"Exchange non supporté ici: {EXCHANGE_NAME}")
    if not PHEMEX_API_KEY or not PHEMEX_API_SECRET:
        raise RuntimeError("PHEMEX_API_KEY/SECRET manquants")

    ex = ccxt.phemex({
        "apiKey": PHEMEX_API_KEY,
        "secret": PHEMEX_API_SECRET,
        "options": {
            "defaultType": "spot",
        },
        "enableRateLimit": True,
    })
    return ex

def _round_to_step(value: float, step: float) -> float:
    if step is None or step == 0:
        return value
    return math.floor(value / step) * step

def _compute_base_qty_for_quote(ex, symbol: str, quote_amt: float) -> Tuple[float, float, Dict[str, Any]]:
    """
    Retourne (base_qty_arrondie, price, market) pour convertir un montant QUOTE -> BASE.
    Applique FEE_BUFFER_PCT sur la base (sécurité).
    Respecte minCost/minAmount/steps.
    """
    markets = ex.load_markets()
    if symbol not in markets:
        raise RuntimeError(f"Symbole inconnu côté exchange: {symbol}")
    market = markets[symbol]

    # prix moyen
    ticker = ex.fetch_ticker(symbol)
    price = float(ticker["last"] or ticker["close"] or ticker["ask"] or ticker["bid"])

    # conversion QUOTE -> BASE
    base_qty = quote_amt / price
    base_qty *= (1.0 - FEE_BUFFER_PCT)

    # contraintes exchange
    min_amount = float((market.get("limits", {}).get("amount", {}) or {}).get("min") or 0.0)
    min_cost   = float((market.get("limits", {}).get("cost", {}) or {}).get("min") or 0.0)

    # steps (quantize)
    amount_step = None
    if "precision" in market and market["precision"].get("amount"):
        # ccxt: precision = nb décimales -> on arrondit ensuite via step approximatif
        decimals = int(market["precision"]["amount"])
        amount_step = 10 ** (-decimals)
    elif "info" in market and "lotSz" in market["info"]:
        # parfois Phemex expose lot size
        try:
            amount_step = float(market["info"]["lotSz"])
        except Exception:
            amount_step = None

    if min_cost and (base_qty * price) < min_cost:
        # augmente pour respecter min cost
        base_qty = min_cost / price

    if min_amount and base_qty < min_amount:
        base_qty = min_amount

    if amount_step:
        base_qty = _round_to_step(base_qty, amount_step)

    return base_qty, price, market

# ========= Routes =========
@app.get("/health")
def health():
    return jsonify({"status": "ok"}), 200

@app.post("/webhook")
def webhook():
    """
    Payload TradingView attendu (exemples simples) :
      - {"signal":"BUY"}  -> achète pour FIXED_QUOTE_PER_TRADE (en QUOTE, ex : 15 USDT)
      - {"signal":"SELL"} -> vend l’équivalent FIXED_QUOTE_PER_TRADE (ou au max dispo moins la réserve)
      Options :
      - {"signal":"BUY","quote":25}     # override du montant QUOTE à utiliser
      - {"signal":"SELL","qty_base":0.001}  # override de la quantité base à vendre
    """
    try:
        payload = request.get_json(silent=True) or {}
        log.info("Webhook payload: %s", json.dumps(payload, ensure_ascii=False))

        signal = (payload.get("signal") or "").upper()
        if signal not in {"BUY", "SELL"}:
            return jsonify({"error": "signal invalide (BUY/SELL)"}), 400

        quote_to_use = float(payload.get("quote") or FIXED_QUOTE_PER_TRADE)
        if quote_to_use < MIN_EUR_PER_TRADE:
            log.warning("Montant quote %s < MIN_EUR_PER_TRADE %s", quote_to_use, MIN_EUR_PER_TRADE)

        ex = _make_exchange()

        # --- BUY ---
        if signal == "BUY":
            base_qty, price, market = _compute_base_qty_for_quote(ex, SYMBOL, quote_to_use)

            # sécurité : si qty très petite
            if base_qty <= 0:
                return jsonify({"error": "qty base <= 0 après calcul"}), 400

            log.info("BUY %s @~%.8f %s (base_qty=%s)", SYMBOL, price, QUOTE_SYMBOL, base_qty)

            if ORDER_TYPE != "market":
                return jsonify({"error": "Cette version ne gère que market"}), 400

            order = ex.create_market_buy_order(SYMBOL, base_qty)
            log.info("BUY filled: %s", json.dumps(order, default=str))
            return jsonify({"ok": True, "order": order}), 200

        # --- SELL ---
        else:
            # quantité souhaitée override ?
            qty_override = payload.get("qty_base")
            if qty_override is not None:
                try:
                    base_qty = float(qty_override)
                except Exception:
                    return jsonify({"error": "qty_base invalide"}), 400
            else:
                # calcule base à partir d'un montant en QUOTE (même logique que BUY)
                base_qty, price, _ = _compute_base_qty_for_quote(ex, SYMBOL, quote_to_use)

            # ne pas dépasser le disponible (et laisser une réserve)
            balances = ex.fetch_free_balance()
            avail_base = float(balances.get(BASE_SYMBOL, 0.0))
            sellable = max(0.0, avail_base - BTC_RESERVE)
            base_qty = min(base_qty, sellable)

            if base_qty <= 0:
                return jsonify({"error": "Pas de quantité base vendable (réserve incluse)"}), 400

            log.info("SELL %s qty=%s (reserve=%s, avail=%s)", SYMBOL, base_qty, BTC_RESERVE, avail_base)

            if ORDER_TYPE != "market":
                return jsonify({"error": "Cette version ne gère que market"}), 400

            order = ex.create_market_sell_order(SYMBOL, base_qty)
            log.info("SELL filled: %s", json.dumps(order, default=str))
            return jsonify({"ok": True, "order": order}), 200

    except ccxt.InsufficientFunds as e:
        log.warning("Fonds insuffisants: %s", str(e))
        return jsonify({"error": "InsufficientFunds", "detail": str(e)}), 400
    except ccxt.BaseError as e:
        log.exception("Erreur exchange/ccxt")
        return jsonify({"error": "ExchangeError", "detail": str(e)}), 502
    except Exception as e:
        log.exception("Erreur serveur")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.getenv("PORT", "3000"))
    app.run(host="0.0.0.0", port=port)
