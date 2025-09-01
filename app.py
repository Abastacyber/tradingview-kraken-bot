import os
import json
import math
import logging
from functools import lru_cache
from typing import Any, Dict, Tuple

from flask import Flask, request, jsonify
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

# ========= Lecture ENV =========
LOG_LEVEL              = env_str("LOG_LEVEL", "INFO").upper()
EXCHANGE_NAME          = env_str("EXCHANGE", "phemex").lower()       # "phemex"
BASE_SYMBOL            = env_str("BASE_SYMBOL", "BTC").upper()       # "BTC"
QUOTE_SYMBOL           = env_str("QUOTE_SYMBOL", "USDT").upper()     # "USDT"
SYMBOL_DEFAULT         = f"{BASE_SYMBOL}/{QUOTE_SYMBOL}"             # format ccxt

ORDER_TYPE             = env_str("ORDER_TYPE", "market").lower()     # uniquement market ici
SIZE_MODE              = env_str("SIZE_MODE", "fixed_quote")
FIXED_QUOTE_PER_TRADE  = env_float("FIXED_QUOTE_PER_TRADE", 15.0)    # en QUOTE
FEE_BUFFER_PCT         = env_float("FEE_BUFFER_PCT", 0.002)          # 0.2%
MIN_QUOTE_PER_TRADE    = env_float("MIN_QUOTE_PER_TRADE", 10.0)      # garde-fou
BASE_RESERVE           = env_float("BTC_RESERVE", 0.00005)           # réserve en BASE

WEBHOOK_TOKEN          = env_str("WEBHOOK_TOKEN", "")                # optionnel (X-Webhook-Token)
DRY_RUN                = env_str("DRY_RUN", "false").lower() in ("1", "true", "yes")

API_KEY                = env_str("PHEMEX_API_KEY")
API_SECRET             = env_str("PHEMEX_API_SECRET")

# ========= Logs =========
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO))
log = logging.getLogger("tv-phemex")

# ========= Flask =========
app = Flask(__name__)

# ========= Exchange (ccxt) =========
def _assert_env():
    if EXCHANGE_NAME != "phemex":
        raise RuntimeError(f"Exchange non supporté ici: {EXCHANGE_NAME}")
    if not API_KEY or not API_SECRET:
        raise RuntimeError("PHEMEX_API_KEY/SECRET manquants")

def _normalize_to_ccxt_symbol(s: str) -> str:
    """
    Normalise 'BTCUSDT', 'BTC-USD', 'BTC/USD' -> 'BTC/USDT'
    """
    if not s:
        return SYMBOL_DEFAULT
    s = s.replace("-", "/").upper()
    if "/" in s:
        base, quote = s.split("/", 1)
        return f"{base}/{quote}"
    for q in ("USDT", "USD", "USDC", "EUR", "BTC", "ETH"):
        if s.endswith(q):
            base = s[:-len(q)]
            return f"{base}/{q}"
    return SYMBOL_DEFAULT

def _make_exchange():
    _assert_env()
    ex = ccxt.phemex({
        "apiKey": API_KEY,
        "secret": API_SECRET,
        "options": {"defaultType": "spot"},
        "enableRateLimit": True,
    })
    return ex

@lru_cache(maxsize=1)
def _load_markets(ex):
    return ex.load_markets()

def _round_to_step(value: float, step: float) -> float:
    if not step:
        return value
    return math.floor(value / step) * step

def _amount_step_from_market(market: Dict[str, Any]) -> float:
    precision = (market.get("precision") or {}).get("amount")
    if precision is not None:
        try:
            return 10 ** (-int(precision))
        except Exception:
            pass
    info = market.get("info") or {}
    for k in ("lotSz", "lotSize", "qtyStep"):
        if k in info:
            try:
                return float(info[k])
            except Exception:
                continue
    return None

def _compute_base_qty_for_quote(ex, symbol: str, quote_amt: float) -> Tuple[float, float, Dict[str, Any]]:
    markets = _load_markets(ex)
    if symbol not in markets:
        raise RuntimeError(f"Symbole inconnu côté exchange: {symbol}")
    market = markets[symbol]

    ticker = ex.fetch_ticker(symbol)
    price = float(ticker.get("last") or ticker.get("close") or ticker.get("ask") or ticker.get("bid") or 0.0)
    if price <= 0:
        raise RuntimeError("Prix invalide")

    base_qty = (quote_amt / price) * (1.0 - FEE_BUFFER_PCT)

    limits = market.get("limits") or {}
    min_amount = float((limits.get("amount") or {}).get("min") or 0.0)
    min_cost   = float((limits.get("cost")   or {}).get("min") or 0.0)

    if min_cost and (base_qty * price) < min_cost:
        base_qty = min_cost / price
    if min_amount and base_qty < min_amount:
        base_qty = min_amount

    step = _amount_step_from_market(market)
    if step:
        base_qty = _round_to_step(base_qty, step)

    return base_qty, price, market

# ========= Routes =========
@app.get("/")
def index():
    return jsonify({"service": "tv-phemex-bot", "status": "ok"}), 200

@app.get("/health")
def health():
    return jsonify({"status": "ok"}), 200

@app.post("/webhook")
def webhook():
    try:
        # --- Auth simple par token (optionnel) ---
        if WEBHOOK_TOKEN:
            given = (
                request.headers.get("X-Webhook-Token")
                or request.args.get("token")
                or (request.get_json(silent=True) or {}).get("token")
            )
            if given != WEBHOOK_TOKEN:
                return jsonify({"error": "unauthorized"}), 401

        payload = request.get_json(silent=True) or {}
        log.info("Webhook payload: %s", json.dumps(payload, ensure_ascii=False))

        signal = (payload.get("signal") or "").upper()
        if signal not in {"BUY", "SELL"}:
            return jsonify({"error": "signal invalide (BUY/SELL)"}), 400

        symbol = _normalize_to_ccxt_symbol(payload.get("symbol") or SYMBOL_DEFAULT)

        quote_to_use = float(payload.get("quote") or FIXED_QUOTE_PER_TRADE)
        if quote_to_use < MIN_QUOTE_PER_TRADE:
            log.warning("Montant QUOTE %s < MIN_QUOTE_PER_TRADE %s", quote_to_use, MIN_QUOTE_PER_TRADE)

        if ORDER_TYPE != "market":
            return jsonify({"error": "Cette version ne gère que les ordres market"}), 400

        ex = _make_exchange()

        # === BUY ===
        if signal == "BUY":
            base_qty, price, market = _compute_base_qty_for_quote(ex, symbol, quote_to_use)
            if base_qty <= 0:
                return jsonify({"error": "qty base <= 0 après calcul"}), 400

            log.info("BUY %s ~%.8f %s | qty=%s", symbol, price, QUOTE_SYMBOL, base_qty)

            if DRY_RUN:
                return jsonify({"ok": True, "dry_run": True, "action": "BUY", "symbol": symbol, "qty": base_qty, "price": price}), 200

            order = ex.create_market_buy_order(symbol, base_qty)
            log.info("BUY filled: %s", json.dumps(order, default=str))
            return jsonify({"ok": True, "order": order}), 200

        # === SELL ===
        qty_override = payload.get("qty_base")
        if qty_override is not None:
            try:
                base_qty = float(qty_override)
            except Exception:
                return jsonify({"error": "qty_base invalide"}), 400
        else:
            base_qty, price, _ = _compute_base_qty_for_quote(ex, symbol, quote_to_use)

        balances = ex.fetch_free_balance()
        base_code = symbol.split("/")[0]
        avail_base = float(balances.get(base_code, 0.0))
        sellable = max(0.0, avail_base - BASE_RESERVE)
        base_qty = min(base_qty, sellable)

        if base_qty <= 0:
            return jsonify({"error": "Pas de quantité base vendable (réserve incluse)"}), 400

        log.info("SELL %s qty=%s (reserve=%s, avail=%s)", symbol, base_qty, BASE_RESERVE, avail_base)

        if DRY_RUN:
            return jsonify({"ok": True, "dry_run": True, "action": "SELL", "symbol": symbol, "qty": base_qty}), 200

        order = ex.create_market_sell_order(symbol, base_qty)
        log.info("SELL filled: %s", json.dumps(order, default=str))
        return jsonify({"ok": True, "order": order}), 200

    except ccxt.InsufficientFunds as e:
        log.warning("Fonds insuffisants: %s", str(e))
        return jsonify({"error": "InsufficientFunds", "detail": str(e)}), 400
    except ccxt.NetworkError as e:
        log.exception("Erreur réseau exchange/ccxt")
        return jsonify({"error": "NetworkError", "detail": str(e)}), 503
    except ccxt.BaseError as e:
        log.exception("Erreur exchange/ccxt")
        return jsonify({"error": "ExchangeError", "detail": str(e)}), 502
    except Exception as e:
        log.exception("Erreur serveur")
        return jsonify({"error": str(e)}), 500

if __name__ == "__m
