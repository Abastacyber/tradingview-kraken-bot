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
MIN_QUOTE_PER_TRADE    = env_float("MIN_QUOTE_PER_TRADE", 10.0)

BASE_RESERVE           = env_float("BASE_RESERVE", 0.00005)
QUOTE_RESERVE          = env_float("QUOTE_RESERVE", 10.0)

# Gestion du risque
RISK_PCT               = env_float("RISK_PCT", 0.01)   # 1% du montant de l'ordre (borne SL)
MAX_SL_PCT             = env_float("MAX_SL_PCT", 0.05) # 5% max (filet ultime)

WEBHOOK_TOKEN          = env_str("WEBHOOK_TOKEN", "")
DRY_RUN                = env_str("DRY_RUN", "false").lower() in ("1","true","yes")

# Trailing (côté bot)
TRAILING_ENABLED           = env_str("TRAILING_ENABLED", "false").lower() in ("1","true","yes")
TRAIL_ACTIVATE_PCT_CONF2   = env_float("TRAIL_ACTIVATE_PCT_CONF2", 0.003)   # +0.30%
TRAIL_GAP_CONF2            = env_float("TRAIL_GAP_CONF2",           0.0025) # 0.25%
TRAIL_ACTIVATE_PCT_CONF3   = env_float("TRAIL_ACTIVATE_PCT_CONF3", 0.005)   # +0.50%
TRAIL_GAP_CONF3            = env_float("TRAIL_GAP_CONF3",           0.0035) # 0.35%

# Cooldown BUY
BUY_COOLDOWN_SEC       = env_int("BUY_COOLDOWN_SEC", 0)

# Persistance d'état simple (fichier)
STATE_FILE             = env_str("STATE_FILE", "/tmp/bot_state.json")
RESTORE_ON_START       = env_str("RESTORE_ON_START", "true").lower() in ("1","true","yes")

API_KEY                = env_str("PHEMEX_API_KEY")
API_SECRET             = env_str("PHEMEX_API_SECRET")

# ========= État mémoire (simple, 1 paire) =========
_position_lock = threading.Lock()
_state_lock = threading.Lock()
_has_position = False
_last_buy_ts = 0

def _read_state() -> dict:
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def _write_state(data: dict):
    try:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, STATE_FILE)  # atomic on POSIX
    except Exception as e:
        logging.getLogger("tv-phemex").warning("State write failed: %s", e)

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

def _amount_step_from_market(market: Dict[str, Any]) -> float | None:
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
    """
    Convertit un montant QUOTE -> quantité BASE, en respectant minCost/minAmount/step.
    Ajoute un garde-fou pour ignorer des min_amount irréalistes (ex: 1 BTC).
    """
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

    # PATCH: ignorer min_amount irréaliste (ex: 1 BTC quand step ~ 1e-6)
    if step and min_amount and (min_amount >= step * 1000):
        log.debug("Ignorer min_amount irréaliste: min_amount=%s, step=%s", min_amount, step)
        min_amount = 0.0

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
        return (0.008, 0.005)    # +0.8% / -0.5%
    return (0.003, 0.002)        # +0.3% / -0.2%

def _trail_params(conf:int)->Tuple[float,float]:
    # (activate_pct, gap)
    if conf >= 3:
        return (TRAIL_ACTIVATE_PCT_CONF3, TRAIL_GAP_CONF3)
    return (TRAIL_ACTIVATE_PCT_CONF2, TRAIL_GAP_CONF2)

# ========= Trailing monitor (côté bot) =========
def _monitor_trailing(ex, symbol: str, side: str, qty: float, entry_price: float,
                      conf: int, base_sl_pct: float):
    """Thread de suivi : trailing stop côté bot, avec stop initial borné."""
    global _has_position

    if not TRAILING_ENABLED or side != "BUY" or qty <= 0:
        return

    # recrée un client ccxt dans le thread
    try:
        ex = _make_exchange()
    except Exception as e:
        log.warning("[TRAIL] exchange init failed: %s", e)
        return

    activate_pct, gap = _trail_params(conf)
    max_price = entry_price
    risk_sl_pct = min(base_sl_pct, MAX_SL_PCT)
    initial_stop_price = entry_price * (1.0 - risk_sl_pct)
    trail_stop = initial_stop_price
    activated = False

    log.info("[TRAIL] start %s qty=%.8f entry=%.2f conf=%s baseSL=%.4f",
             symbol, qty, entry_price, conf, base_sl_pct)

    while True:
        try:
            t = ex.fetch_ticker(symbol)
            last = float(t.get("last") or t.get("close") or 0)
            if last <= 0:
                time.sleep(3); continue

            # SL initial
            if last <= initial_stop_price:
                log.warning("[TRAIL] initial SL hit (%.2f <= %.2f) -> SELL market", last, initial_stop_price)
                if not DRY_RUN:
                    try:
                        ex.create_market_sell_order(symbol, qty)
                        _has_position = False
                        with _state_lock:
                            _write_state({"open": False, "symbol": symbol, "ts": time.time()})
                        log.info("[TRAIL] Position fermée (SL initial).")
                    except Exception as e:
                        log.warning("[TRAIL] SELL initial SL failed: %s", e)
                break

            # Activation du trailing
            if not activated and last >= entry_price * (1.0 + activate_pct):
                activated = True
                log.info("[TRAIL] activated at %.2f (>= %.2f)", last, entry_price*(1+activate_pct))

            if activated:
                if last > max_price:
                    max_price = last
                    trail_stop = max(initial_stop_price, max_price * (1.0 - gap))

            if last <= trail_stop:
                log.info("[TRAIL] stop hit %.2f <= %.2f -> SELL market", last, trail_stop)
                if not DRY_RUN:
                    try:
                        ex.create_market_sell_order(symbol, qty)
                        _has_position = False
                        with _state_lock:
                            _write_state({"open": False, "symbol": symbol, "ts": time.time()})
                        log.info("[TRAIL] Position fermée (trailing stop).")
                    except Exception as e:
                        log.warning("[TRAIL] SELL failed: %s", e)
                break

            log.debug("[TRAIL] last=%.2f max=%.2f stop=%.2f activated=%s", last, max_price, trail_stop, activated)
            time.sleep(3)
        except Exception as e:
            log.warning("[TRAIL] error: %s", e)
            time.sleep(3)

    log.info("[TRAIL] thread finished.")
    _has_position = False  # garde-fou

# ========= Restauration au démarrage =========
def _restore_open_position():
    if not RESTORE_ON_START:
        return
    st = _read_state()
    if not st or not st.get("open"):
        return
    try:
        symbol = _normalize_to_ccxt_symbol(st.get("symbol") or SYMBOL_DEFAULT)
        qty = float(st.get("qty") or 0.0)
        entry_price = float(st.get("entry_price") or 0.0)
        conf = int(st.get("conf") or 2)
        sl_pct = float(st.get("sl_pct") or 0.002)
        if qty > 0 and entry_price > 0:
            log.warning("[RESTORE] Position détectée -> relance trailing (qty=%.8f, entry=%.2f, conf=%s)",
                        qty, entry_price, conf)
            global _has_position
            _has_position = True
            ex = _make_exchange()
            threading.Thread(
                target=_monitor_trailing,
                args=(ex, symbol, "BUY", qty, entry_price, conf, sl_pct),
                daemon=True
            ).start()
        else:
            log.warning("[RESTORE] État ouvert mais incomplet, on l'ignore: %s", st)
    except Exception as e:
        log.warning("[RESTORE] échec: %s", e)

# lancer la restauration une fois à l'import (utiliser 1 worker)
try:
    _restore_open_position()
except Exception as _e:
    log.warning("Restore on start failed: %s", _e)

# ========= Routes =========
@app.get("/")
def index():
    return jsonify({"service": "tv-phemex-bot", "status": "ok"}), 200

@app.get("/health")
def health():
    return jsonify({"status": "ok"}), 200

@app.post("/webhook")
def webhook():
    global _has_position, _last_buy_ts

    with _position_lock:
        try:
            # Auth optionnelle
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

            # -------- BUY --------
            if signal == "BUY":
                now = time.time()
                if BUY_COOLDOWN_SEC > 0 and (now - _last_buy_ts) < BUY_COOLDOWN_SEC:
                    return jsonify({"ok": False, "skipped": "buy_cooldown",
                                    "seconds_since_last": round(now - _last_buy_ts),
                                    "cooldown_sec": BUY_COOLDOWN_SEC}), 200

                if _has_position:
                    log.info("BUY ignoré: position déjà ouverte.")
                    return jsonify({"ok": False, "reason": "position_already_open"}), 200

                requested_quote = float(payload.get("quote") or FIXED_QUOTE_PER_TRADE)

                # borne simple du SL par RISK_PCT
                if requested_quote * sl_pct > requested_quote * RISK_PCT:
                    log.warning("Risque de perte (%.2f%%) > max (%.2f%%). SL borné.",
                                sl_pct*100, RISK_PCT*100)
                    sl_pct = RISK_PCT

                if requested_quote < MIN_QUOTE_PER_TRADE:
                    return jsonify({
                        "error": "sizing_error",
                        "detail": f"Montant trop faible: min {MIN_QUOTE_PER_TRADE} {QUOTE_SYMBOL}, tu as {requested_quote}"
                    }), 400

                balances = ex.fetch_free_balance()
                avail_quote = float(balances.get(QUOTE_SYMBOL, 0.0))
                usable_quote = max(0.0, avail_quote - QUOTE_RESERVE)
                quote_to_use = min(requested_quote, usable_quote)

                if quote_to_use <= 0:
                    return jsonify({"error":"Pas assez de QUOTE disponible (réserve incluse)",
                                    "available": avail_quote, "quote_reserve": QUOTE_RESERVE}), 400

                try:
                    base_qty, price, _ = _compute_base_qty_for_quote(ex, symbol, quote_to_use)
                except Exception as e:
                    log.warning("Sizing error BUY: %s", e)
                    return jsonify({"error": "sizing_error", "detail": str(e),
                                    "suggestion": "Augmente le montant quote ou diminue la réserve QUOTE"}), 400

                if ORDER_TYPE != "market":
                    return jsonify({"error": "Cette version ne gère que les ordres market"}), 400

                log.info("BUY %s quote=%.4f -> qty=%.8f (price~%.2f) | reserves QUOTE=%.2f, BASE=%.8f",
                         symbol, quote_to_use, base_qty, price, QUOTE_RESERVE, BASE_RESERVE)

                if DRY_RUN:
                    _has_position = True
                    _last_buy_ts = now
                    with _state_lock:
                        _write_state({
                            "open": True, "symbol": symbol, "qty": base_qty,
                            "entry_price": price, "conf": conf, "sl_pct": sl_pct, "ts": time.time()
                        })
                    return jsonify({"ok": True, "dry_run": True, "action": "BUY",
                                    "symbol": symbol, "qty": base_qty, "price": price,
                                    "tp_pct": tp_pct, "sl_pct": sl_pct,
                                    "confidence": conf, "trailing_enabled": TRAILING_ENABLED}), 200

                order = ex.create_market_buy_order(symbol, base_qty)

                try:
                    fill_price = float(order.get("average") or order.get("price") or 0.0)
                except Exception:
                    fill_price = 0.0
                if not fill_price:
                    t = ex.fetch_ticker(symbol)
                    fill_price = float(t.get("last") or t.get("close") or 0.0)

                filled_qty = float(order.get("filled") or base_qty)

                _has_position = True
                _last_buy_ts = now

                # persister l'état
                with _state_lock:
                    _write_state({
                        "open": True, "symbol": symbol, "qty": filled_qty,
                        "entry_price": fill_price, "conf": conf, "sl_pct": sl_pct, "ts": time.time()
                    })

                if TRAILING_ENABLED:
                    threading.Thread(
                        target=_monitor_trailing,
                        args=(ex, symbol, "BUY", filled_qty, fill_price, conf, sl_pct),
                        daemon=True
                    ).start()

                return jsonify({"ok": True, "order": order,
                                "tp_pct": tp_pct, "sl_pct": sl_pct,
                                "confidence": conf, "trailing_enabled": TRAILING_ENABLED}), 200

            # -------- SELL --------
            # Alias tolérant: "quantity" accepté si "qty_base" absent
            qty_override = payload.get("qty_base")
            if qty_override is None and "quantity" in payload:
                qty_override = payload.get("quantity")

            balances = ex.fetch_free_balance()
            base_code = symbol.split("/")[0]
            avail_base = float(balances.get(base_code, 0.0))
            sellable = max(0.0, avail_base - BASE_RESERVE)

            if qty_override is not None:
                try:
                    base_qty = float(qty_override)
                except Exception:
                    return jsonify({"error": "qty_base invalide"}), 400
                base_qty_to_sell = min(base_qty, sellable)
            else:
                requested_quote = float(payload.get("quote") or FIXED_QUOTE_PER_TRADE)

                # Close-all si quote énorme (ex: 999999)
                if requested_quote >= 1e5:
                    base_qty_to_sell = sellable
                else:
                    try:
                        base_qty, price, _ = _compute_base_qty_for_quote(ex, symbol, requested_quote)
                    except Exception as e:
                        log.warning("Sizing error SELL: %s", e)
                        return jsonify({"error": "sizing_error", "detail": str(e)}), 400
                    base_qty_to_sell = min(base_qty, sellable)

            if base_qty_to_sell <= 0:
                return jsonify({"error": "Pas de quantité base vendable (réserve incluse)",
                                "available_base": avail_base, "base_reserve": BASE_RESERVE}), 400

            if ORDER_TYPE != "market":
                return jsonify({"error": "Cette version ne gère que les ordres market"}), 400

            log.info("SELL %s qty=%.8f (avail=%.8f, reserve=%.8f)",
                     symbol, base_qty_to_sell, avail_base, BASE_RESERVE)

            if DRY_RUN:
                _has_position = False
                with _state_lock:
                    _write_state({"open": False, "symbol": symbol, "ts": time.time()})
                return jsonify({"ok": True, "dry_run": True, "action": "SELL",
                                "symbol": symbol, "qty": base_qty_to_sell,
                                "tp_pct": tp_pct, "sl_pct": sl_pct, "confidence": conf}), 200

            order = ex.create_market_sell_order(symbol, base_qty_to_sell)
            _has_position = False
            with _state_lock:
                _write_state({"open": False, "symbol": symbol, "ts": time.time()})

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
