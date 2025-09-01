import os
import json
import math
import time
import threading
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

# ========= ENV =========
LOG_LEVEL              = env_str("LOG_LEVEL", "INFO").upper()
EXCHANGE_NAME          = env_str("EXCHANGE", "phemex").lower()
BASE_SYMBOL            = env_str("BASE_SYMBOL", "BTC").upper()
QUOTE_SYMBOL           = env_str("QUOTE_SYMBOL", "USDT").upper()
SYMBOL_DEFAULT         = f"{BASE_SYMBOL}/{QUOTE_SYMBOL}"

ORDER_TYPE             = env_str("ORDER_TYPE", "market").lower()
FIXED_QUOTE_PER_TRADE  = env_float("FIXED_QUOTE_PER_TRADE", 30.0)
FEE_BUFFER_PCT         = env_float("FEE_BUFFER_PCT", 0.002)     # 0.2%
MIN_QUOTE_PER_TRADE    = env_float("MIN_QUOTE_PER_TRADE", 10.0) # <<— nom correct

BASE_RESERVE           = env_float("BASE_RESERVE", 0.00005)     # réserve BTC
QUOTE_RESERVE          = env_float("QUOTE_RESERVE", 10.0)       # réserve USDT

WEBHOOK_TOKEN          = env_str("WEBHOOK_TOKEN", "")
DRY_RUN                = env_str("DRY_RUN", "false").lower() in ("1","true","yes")

# Trailing (côté bot)
TRAILING_ENABLED           = env_str("TRAILING_ENABLED", "false").lower() in ("1","true","yes")
TRAIL_ACTIVATE_PCT_CONF2   = env_float("TRAIL_ACTIVATE_PCT_CONF2", 0.003)   # +0.30%
TRAIL_GAP_CONF2            = env_float("TRAIL_GAP_CONF2",         0.0025)   # 0.25%
TRAIL_ACTIVATE_PCT_CONF3   = env_float("TRAIL_ACTIVATE_PCT_CONF3", 0.005)   # +0.50%
TRAIL_GAP_CONF3            = env_float("TRAIL_GAP_CONF3",         0.0035)   # 0.35%

API_KEY                = env_str("PHEMEX_API_KEY")
API_SECRET             = env_str("PHEMEX_API_SECRET")

# ========= Logs =========
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO))
log = logging.getLogger("tv-phemex")

# ========= Flask =========
app = Flask(__name__)

# ========= Exchange =========
def _assert_env():
    if EXCHANGE_NAME != "phemex":
        raise RuntimeError(f"Exchange non supporté: {EXCHANGE_NAME}")
    if not API_KEY or not API_SECRET:
        raise RuntimeError("PHEMEX_API_KEY/SECRET manquants")

def _normalize_to_ccxt_symbol(s: str) -> str:
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
    return ccxt.phemex({
        "apiKey": API_KEY,
        "secret": API_SECRET,
        "options": {"defaultType": "spot"},
        "enableRateLimit": True,
    })

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

    base_qty_raw = (quote_amt / price) * (1.0 - FEE_BUFFER_PCT)

    limits = market.get("limits") or {}
    min_amount = float((limits.get("amount") or {}).get("min") or 0.0)
    min_cost   = float((limits.get("cost")   or {}).get("min") or 0.0)
    step       = _amount_step_from_market(market)

    base_qty = base_qty_raw
    if min_cost and (base_qty * price) < min_cost:
        base_qty = min_cost / price
    if min_amount and base_qty < min_amount:
        base_qty = min_amount
    if step:
        base_qty = _round_to_step(base_qty, step)

    minimal_lot = max(step or 0.0, min_amount or 0.0)
    if minimal_lot and base_qty < minimal_lot:
        required_quote = (minimal_lot * price) * (1.0 + FEE_BUFFER_PCT)
        raise RuntimeError(
            f"Montant trop faible pour le lot minimal: lot_min={minimal_lot} {symbol.split('/')[0]} "
            f"(≈ {required_quote:.2f} {symbol.split('/')[1]} requis)"
        )
    return base_qty, price, market

def _tp_sl_from_confidence(conf: int) -> Tuple[float, float]:
    # (tp_pct, sl_pct)
    if conf >= 3:
        return (0.008, 0.005)   # +0.8% / -0.5%
    return (0.003, 0.002)       # +0.3% / -0.2%

def _trail_params(conf:int)->Tuple[float,float]:
    # (activate_pct, gap)
    if conf >= 3:
        return (TRAIL_ACTIVATE_PCT_CONF3, TRAIL_GAP_CONF3)
    return (TRAIL_ACTIVATE_PCT_CONF2, TRAIL_GAP_CONF2)

# ========= Trailing monitor (côté bot) =========
def _monitor_trailing(ex, symbol: str, side: str, qty: float, entry_price: float,
                      conf: int, base_sl_pct: float):
    """Thread de suivi : active un trailing après un certain gain et vend au marché si le stop est touché."""
    if not TRAILING_ENABLED or side != "BUY" or qty <= 0:
        return
    activate_pct, gap = _trail_params(conf)
    max_price = entry_price
    trail_stop = entry_price * (1.0 - base_sl_pct)
    activated = False

    log.info("[TRAIL] start %s qty=%.8f entry=%.2f conf=%s baseSL=%.4f",
             symbol, qty, entry_price, conf, base_sl_pct)

    while True:
        try:
            t = ex.fetch_ticker(symbol)
            last = float(t.get("last") or t.get("close") or 0)
            if last <= 0:
                time.sleep(3); continue

            if not activated and last >= entry_price * (1.0 + activate_pct):
                activated = True
                log.info("[TRAIL] activated at %.2f (>= %.2f)", last, entry_price*(1+activate_pct))

            if activated:
                if last > max_price:
                    max_price = last
                    trail_stop = max(trail_stop, max_price * (1.0 - gap))

            if last <= trail_stop:
                log.info("[TRAIL] stop hit %.2f <= %.2f -> SELL market", last, trail_stop)
                if not DRY_RUN:
                    try:
                        ex.create_market_sell_order(symbol, qty)
                    except Exception as e:
                        log.warning("[TRAIL] SELL failed: %s", e)
                break

            time.sleep(3)
        except Exception as e:
            log.warning("[TRAIL] error: %s", e)
            time.sleep(3)

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
        if WEBHOOK_TOKEN:
            given = (request.headers.get("X-Webhook-Token")
                     or request.args.get("token")
                     or (request.get_json(silent=True) or {}).get("token"))
            if given != WEBHOOK_TOKEN:
                return jsonify({"error": "unauthorized"}), 401

        payload = request.get_json(silent=True) or {}
        log.info("Webhook payload: %s", json.dumps(payload, ensure_ascii=False))

        signal = (payload.get("signal") or "").upper()
        if signal not in {"BUY", "SELL"}:
            return jsonify({"error": "signal invalide (BUY/SELL)"}), 400

        symbol = _normalize_to_ccxt_symbol(payload.get("symbol") or SYMBOL_DEFAULT)
        conf   = int(payload.get("confidence") or payload.get("indicators_count") or 2)
        tp_pct, sl_pct = _tp_sl_from_confidence(conf)

        ex = _make_exchange()

        if signal == "BUY":
            requested_quote = float(payload.get("quote") or FIXED_QUOTE_PER_TRADE)

            # --- Blocage si quote < MIN_QUOTE_PER_TRADE
            if requested_quote < MIN_QUOTE_PER_TRADE:
                return jsonify({
                    "error": "sizing_error",
                    "detail": f"Montant trop faible: min {MIN_QUOTE_PER_TRADE} {QUOTE_SYMBOL}, tu as {requested_quote}"
                }), 400

            # réserve USDT
            balances = ex.fetch_free_balance()
            avail_quote = float(balances.get(QUOTE_SYMBOL, 0.0))
            usable_quote = max(0.0, avail_quote - QUOTE_RESERVE)
            quote_to_use = min(requested_quote, usable_quote)

            if quote_to_use <= 0:
                return jsonify({"error":"Pas assez de QUOTE disponible (réserve incluse)",
                                "available": avail_quote, "quote_reserve": QUOTE_RESERVE}), 400

            # sizing
            try:
                base_qty, price, _ = _compute_base_qty_for_quote(ex, symbol, quote_to_use)
            except Exception as e:
                log.warning("Sizing error: %s", e)
                return jsonify({"error": "sizing_error", "detail": str(e),
                                "suggestion": "Augmente le montant quote ou diminue la réserve QUOTE"}), 400

            if ORDER_TYPE != "market":
                return jsonify({"error": "Cette version ne gère que les ordres market"}), 400

            log.info("BUY %s quote=%.4f -> qty=%.8f (price~%.2f) | reserves QUOTE=%.2f, BASE=%.8f",
                     symbol, quote_to_use, base_qty, price, QUOTE_RESERVE, BASE_RESERVE)

            if DRY_RUN:
                return jsonify({"ok": True, "dry_run": True, "action": "BUY",
                                "symbol": symbol, "qty": base_qty, "price": price,
                                "tp_pct": tp_pct, "sl_pct": sl_pct,
                                "confidence": conf, "trailing_enabled": TRAILING_ENABLED}), 200

            order = ex.create_market_buy_order(symbol, base_qty)

            # prix moyen rempli (fallback ticker)
            try:
                fill_price = float(order.get("average") or order.get("price") or 0.0)
            except Exception:
                fill_price = 0.0
            if not fill_price:
                t = ex.fetch_ticker(symbol)
                fill_price = float(t.get("last") or t.get("close") or 0.0)

            # trailing (thread)
            if TRAILING_ENABLED:
                threading.Thread(
                    target=_monitor_trailing,
                    args=(ex, symbol, "BUY", base_qty, fill_price, conf, sl_pct),
                    daemon=True
                ).start()

            return jsonify({"ok": True, "order": order,
                            "tp_pct": tp_pct, "sl_pct": sl_pct,
                            "confidence": conf, "trailing_enabled": TRAILING_ENABLED}), 200

        # -------- SELL --------
        qty_override = payload.get("qty_base")
        if qty_override is not None:
            try:
                base_qty = float(qty_override)
            except Exception:
                return jsonify({"error": "qty_base invalide"}), 400
        else:
            requested_quote = float(payload.get("quote") or FIXED_QUOTE_PER_TRADE)
            try:
                base_qty, price, _ = _compute_base_qty_for_quote(ex, symbol, requested_quote)
            except Exception as e:
                log.warning("Sizing error SELL: %s", e)
                return jsonify({"error": "sizing_error", "detail": str(e)}), 400

        balances = ex.fetch_free_balance()
        base_code = symbol.split("/")[0]
        avail_base = float(balances.get(base_code, 0.0))
        sellable = max(0.0, avail_base - BASE_RESERVE)
        base_qty = min(base_qty, sellable)

        if base_qty <= 0:
            return jsonify({"error": "Pas de quantité base vendable (réserve incluse)",
                            "available_base": avail_base, "base_reserve": BASE_RESERVE}), 400

        if ORDER_TYPE != "market":
            return jsonify({"error": "Cette version ne gère que les ordres market"}), 400

        log.info("SELL %s qty=%.8f (avail=%.8f, reserve=%.8f)", symbol, base_qty, avail_base, BASE_RESERVE)

        if DRY_RUN:
            return jsonify({"ok": True, "dry_run": True, "action": "SELL",
                            "symbol": symbol, "qty": base_qty,
                            "tp_pct": tp_pct, "sl_pct": sl_pct, "confidence": conf}), 200

        order = ex.create_market_sell_order(symbol, base_qty)
        return jsonify({"ok": True, "order": order,
                        "tp_pct": tp_pct, "sl_pct": sl_pct, "confidence": conf}), 200

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

if __name__ == "__main__":
    port = int(os.getenv("PORT", "3000"))
    app.run(host="0.0.0.0", port=port)
