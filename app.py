import os
import json
import math
import time
import threading
import logging
from typing import Any, Dict, Tuple, Optional

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
EXCHANGE_NAME          = env_str("EXCHANGE", "kraken").lower()

BASE_SYMBOL            = env_str("BASE_SYMBOL", "BTC").upper()
QUOTE_SYMBOL           = env_str("QUOTE_SYMBOL", "USDT").upper()
SYMBOL_DEFAULT         = f"{BASE_SYMBOL}/{QUOTE_SYMBOL}"

ORDER_TYPE             = env_str("ORDER_TYPE", "market").lower()

# sizing / coûts
FIXED_QUOTE_PER_TRADE  = env_float("FIXED_QUOTE_PER_TRADE", 10.0)
MIN_QUOTE_PER_TRADE    = env_float("MIN_QUOTE_PER_TRADE", 10.0)     # garde-fou local
FEE_BUFFER_PCT         = env_float("FEE_BUFFER_PCT", 0.002)         # 0.2 %

# réserves
BASE_RESERVE           = env_float("BASE_RESERVE", 0.0)
QUOTE_RESERVE          = env_float("QUOTE_RESERVE", 0.0)

# gestion risque / SL
RISK_PCT               = env_float("RISK_PCT", 0.02)     # max perte vs ticket
MAX_SL_PCT             = env_float("MAX_SL_PCT", 0.05)   # SL dur max

# cooldown (accepte 2 noms)
BUY_COOL_SEC           = env_int("BUY_COOL_SEC", None)
if BUY_COOL_SEC in (None, 0):
    BUY_COOL_SEC = env_int("BUY_COOLDOWN_SEC", 300)

# sécurité / bac à sable
DRY_RUN                = env_str("DRY_RUN", "false").lower() in ("1", "true", "yes")

# Webhook secret (TradingView)
WEBHOOK_SECRET         = env_str("WEBHOOK_SECRET", env_str("WEBHOOK_TOKEN", ""))

# Trailing côté bot
TRAILING_ENABLED         = env_str("TRAILING_ENABLED", "true").lower() in ("1", "true", "yes")
TRAIL_ACTIVATE_PCT_CONF2 = env_float("TRAIL_ACTIVATE_PCT_CONF2", 0.004)  # +0.40 %
TRAIL_GAP_CONF2          = env_float("TRAIL_GAP_CONF2",        0.002)    # 0.20 %
TRAIL_ACTIVATE_PCT_CONF3 = env_float("TRAIL_ACTIVATE_PCT_CONF3", 0.006)  # +0.60 %
TRAIL_GAP_CONF3          = env_float("TRAIL_GAP_CONF3",        0.003)    # 0.30 %

# Persistance d'état
STATE_FILE             = env_str("STATE_FILE", "/tmp/bot_state.json")
RESTORE_ON_START       = env_str("RESTORE_ON_START", "true").lower() in ("1", "true", "yes")

# Clés API
API_KEY                = env_str("KRAKEN_API_KEY", "")
API_SECRET             = env_str("KRAKEN_API_SECRET", "")

# Kraken options
KRAKEN_ENV             = env_str("KRAKEN_ENV", "mainnet").lower()         # "testnet" | "mainnet"
KRAKEN_DEFAULT_TYPE    = env_str("KRAKEN_DEFAULT_TYPE", "spot").lower()   # "spot" | "swap"

# Micro-chunking
BUY_SPLIT_CHUNKS       = max(1, env_int("BUY_SPLIT_CHUNKS", 1))
BUY_SPLIT_DELAY_MS     = max(0, env_int("BUY_SPLIT_DELAY_MS", 300))
SELL_SPLIT_CHUNKS      = max(1, env_int("SELL_SPLIT_CHUNKS", 1))

# ========= Logs =========
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO))
log = logging.getLogger("tv-kraken")

# ========= Flask =========
app = Flask(__name__)

# ========= Etat & locks =========
_state_lock = threading.Lock()
_position_lock = threading.Lock()

_state = {
    "has_position": False,
    "last_buy_ts": 0.0,
    "last_entry_price": 0.0,
    "last_qty": 0.0,
    "symbol": SYMBOL_DEFAULT,
}

def _now() -> float:
    return time.time()

def _save_state():
    try:
        with _state_lock:
            tmp = json.dumps(_state)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            f.write(tmp)
    except Exception as e:
        log.warning("STATE save error: %s", e)

def _load_state():
    if not RESTORE_ON_START:
        return
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            with _state_lock:
                _state.update(data)
            log.info("STATE restored: %s", json.dumps(_state))
    except Exception as e:
        log.warning("STATE load error: %s", e)

# ========= Exchange helpers =========
def _assert_env():
    if EXCHANGE_NAME != "kraken":
        raise RuntimeError(f"Exchange non supporté: {EXCHANGE_NAME}")
    if not API_KEY or not API_SECRET:
        raise RuntimeError("KRAKEN_API_KEY / KRAKEN_API_SECRET manquants")

def _normalize_to_ccxt_symbol(s: str) -> str:
    if not s:
        return SYMBOL_DEFAULT
    s = s.replace("-", "/").upper()
    if "/" not in s:
        # tenter d'inférer le quote
        for q in ("USDT", "USD", "USDC", "EUR", "BTC", "ETH"):
            if s.endswith(q):
                base = s[:-len(q)]
                s = f"{base}/{q}"
                break
        else:
            return SYMBOL_DEFAULT
    base, quote = s.split("/")
    if base == "XBT":  # alias kraken
        base = "BTC"
    return f"{base}/{quote}"

def _make_exchange():
    _assert_env()
    ex = ccxt.kraken({
        "apiKey": API_KEY,
        "secret": API_SECRET,
        "options": {
            "defaultType": KRAKEN_DEFAULT_TYPE,
            # évite d'exiger un prix pour les market buy sur les exchanges qui le demandent
            "createMarketBuyOrderRequiresPrice": False,
        },
        "enableRateLimit": True,
    })
    if KRAKEN_ENV in ("testnet", "sandbox", "demo", "paper", "true", "1", "yes"):
        try:
            ex.set_sandbox_mode(True)
        except Exception:
            pass
    return ex

def _load_markets(ex):
    return ex.load_markets()

def _amount_step_from_market(market: Dict[str, Any]) -> Optional[float]:
    precision = (market.get("precision") or {}).get("amount")
    if precision is not None:
        try:
            return 10 ** (-int(precision))
        except Exception:
            pass
    info = market.get("info") or {}
    for k in ("lotSz", "lotSize", "qtyStep", "minQty"):
        if k in info:
            try:
                val = float(info[k])
                if val > 0:
                    return val
            except Exception:
                continue
    return None

def _get_min_trade_info(ex, symbol: str, price: float) -> Tuple[float, float, Optional[float]]:
    markets = _load_markets(ex)
    if symbol not in markets:
        raise RuntimeError(f"Symbole inconnu côté exchange: {symbol}")
    m = markets[symbol]
    limits = m.get("limits") or {}
    min_amount = float((limits.get("amount") or {}).get("min") or 0.0)
    min_cost   = float((limits.get("cost")   or {}).get("min") or 0.0)
    step       = _amount_step_from_market(m)

    # Sanitize min_amount aberrant
    if min_amount and price and (min_amount * price) > 200:
        base = symbol.split("/")[0]
        log.warning("Ignoring absurd min_amount=%s %s (~%.2f %s) – using qtyStep/minCost instead",
                    min_amount, base, min_amount*price, symbol.split("/")[1])
        min_amount = 0.0
    return min_amount, min_cost, step

def _round_floor(value: float, step: float) -> float:
    if not step or step <= 0:
        return value
    return math.floor(value / step) * step

def _to_exchange_precision(ex, symbol: str, amount: float) -> float:
    try:
        return float(ex.amount_to_precision(symbol, amount))
    except Exception:
        return amount

def _compute_base_qty_for_quote(ex, symbol: str, quote_amt: float) -> Tuple[float, float]:
    t = ex.fetch_ticker(symbol)
    price = float(t.get("last") or t.get("close") or t.get("ask") or t.get("bid") or 0.0)
    if price <= 0:
        raise RuntimeError("Prix invalide (ticker)")

    min_amount, min_cost, step = _get_min_trade_info(ex, symbol, price)
    qty = (quote_amt / price) * (1.0 - FEE_BUFFER_PCT)

    if min_cost and (qty * price) < min_cost:
        qty = min_cost / price
    if min_amount and qty < min_amount:
        qty = min_amount

    if step:
        qty = _round_floor(qty, step)

    # contrôle final
    if qty <= 0:
        required_quote = max(min_cost, (min_amount or 0) * price) or (price * (step or 0))
        required_quote = required_quote * (1.0 + FEE_BUFFER_PCT)
        raise RuntimeError(
            f"Montant trop faible pour le lot minimal (TE_QTY_TOO_SMALL). "
            f"Essaie >= ~{required_quote:.2f} {symbol.split('/')[1]}."
        )
    if min_cost and (qty * price) < min_cost:
        need = min_cost * (1.0 + FEE_BUFFER_PCT)
        raise RuntimeError(
            f"Montant trop faible: minCost≈{min_cost:.2f} {symbol.split('/')[1]} "
            f"(essaie >= ~{need:.2f} {symbol.split('/')[1]})."
        )
    if min_amount and qty < min_amount:
        need = (min_amount * price) * (1.0 + FEE_BUFFER_PCT)
        raise RuntimeError(
            f"Quantité trop faible: minAmount≈{min_amount} {symbol.split('/')[0]} "
            f"(essaie >= ~{need:.2f} {symbol.split('/')[1]})."
        )
    return qty, price

def _tp_sl_from_confidence(conf: int) -> Tuple[float, float]:
    return (0.008, 0.005) if conf >= 3 else (0.003, 0.002)

def _trail_params(conf: int) -> Tuple[float, float]:
    return ((TRAIL_ACTIVATE_PCT_CONF3, TRAIL_GAP_CONF3)
            if conf >= 3 else
            (TRAIL_ACTIVATE_PCT_CONF2, TRAIL_GAP_CONF2))

# ========= Trailing monitor =========
def _monitor_trailing(symbol: str, qty: float, entry_price: float, conf: int, base_sl_pct: float):
    if not TRAILING_ENABLED or qty <= 0:
        return
    try:
        ex = _make_exchange()
    except Exception as e:
        log.warning("[TRAIL] cannot start exchange: %s", e)
        return

    activate_pct, gap = _trail_params(conf)
    max_price = entry_price
    base_sl_pct = min(base_sl_pct, MAX_SL_PCT)
    initial_stop = entry_price * (1.0 - base_sl_pct)
    activated = False

    log.info("[TRAIL] start %s qty=%.8f entry=%.2f conf=%s baseSL=%.4f",
             symbol, qty, entry_price, conf, base_sl_pct)

    while True:
        try:
            t = ex.fetch_ticker(symbol)
            last = float(t.get("last") or t.get("close") or 0.0)
            if last <= 0:
                time.sleep(3)
                continue

            if last <= initial_stop:
                log.warning("[TRAIL] initial SL hit (%.2f <= %.2f) -> SELL", last, initial_stop)
                try:
                    min_amount, _, step = _get_min_trade_info(ex, symbol, last)
                    qty_to_sell = _round_floor(qty, step) if step else qty
                    qty_to_sell = _to_exchange_precision(ex, symbol, qty_to_sell)
                    if qty_to_sell > 0:
                        ex.create_market_sell_order(symbol, qty_to_sell)
                except Exception as e:
                    log.warning("[TRAIL] SELL initial failed: %s", e)
                _with_state(lambda s: s.update({"has_position": False}))
                break

            if not activated and last >= entry_price * (1.0 + activate_pct):
                activated = True
                log.info("[TRAIL] activated at %.2f", last)

            if activated:
                if last > max_price:
                    max_price = last
                trail_stop = max(initial_stop, max_price * (1.0 - gap))
                if last <= trail_stop:
                    log.info("[TRAIL] stop hit %.2f <= %.2f -> SELL", last, trail_stop)
                    try:
                        min_amount, _, step = _get_min_trade_info(ex, symbol, last)
                        qty_to_sell = _round_floor(qty, step) if step else qty
                        qty_to_sell = _to_exchange_precision(ex, symbol, qty_to_sell)
                        if qty_to_sell > 0:
                            ex.create_market_sell_order(symbol, qty_to_sell)
                    except Exception as e:
                        log.warning("[TRAIL] SELL failed: %s", e)
                    _with_state(lambda s: s.update({"has_position": False}))
                    break

            time.sleep(3)
        except Exception as e:
            log.warning("[TRAIL] error: %s", e)
            time.sleep(3)

    log.info("[TRAIL] finished")

# ========= State helpers =========
def _with_state(mutator):
    with _state_lock:
        mutator(_state)
        snap = dict(_state)
    _save_state()
    return snap

# ========= Routes =========
@app.get("/")
def index():
    return jsonify({"service": "tv-kraken-bot", "status": "ok"}), 200

@app.get("/health")
def health():
    return jsonify({"status": "ok"}), 200

@app.get("/debug/state")
def debug_state():
    with _state_lock:
        return jsonify(dict(_state)), 200

@app.get("/debug/limits")
def debug_limits():
    try:
        symbol = _normalize_to_ccxt_symbol(request.args.get("symbol") or SYMBOL_DEFAULT)
        ex = _make_exchange()
        _load_markets(ex)
        m = ex.markets[symbol]
        limits = m.get("limits") or {}
        precision = m.get("precision") or {}
        step = _amount_step_from_market(m)
        price = float(ex.fetch_ticker(symbol).get("last") or 0.0)
        return jsonify({
            "symbol": symbol,
            "price": price,
            "limits": limits,
            "precision": precision,
            "amount_step": step,
            "info_keys": list((m.get("info") or {}).keys())
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.post("/webhook")
def webhook():
    # Un seul passage à la fois (surtout pour le SELL)
    with _position_lock:
        try:
            payload = request.get_json(silent=True) or {}

            # --- Auth TradingView: "secret"/"token" JSON ou query/header ---
            if WEBHOOK_SECRET:
                tok = (payload.get("secret")
                       or request.args.get("secret")
                       or payload.get("token")
                       or request.args.get("token")
                       or request.headers.get("X-Webhook-Token"))
                if tok != WEBHOOK_SECRET:
                    log.warning("tv-kraken: Webhook: secret invalide")
                    return jsonify({"error": "unauthorized"}), 401

            # Log sans secret
            safe_payload = dict(payload)
            safe_payload.pop("secret", None)
            safe_payload.pop("token", None)
            log.info("tv-kraken: Webhook payload: %s", json.dumps(safe_payload, ensure_ascii=False))

            signal = (payload.get("signal") or "").upper()

            # Support PING
            if signal == "PING":
                return jsonify({"ok": True, "pong": True, "ts": int(time.time())}), 200

            if signal not in {"BUY", "SELL"}:
                return jsonify({"error": "signal invalide (BUY/SELL/PING)"}), 400

            symbol = _normalize_to_ccxt_symbol(payload.get("symbol") or _state.get("symbol", SYMBOL_DEFAULT))
            conf = int(payload.get("confidence") or payload.get("indicators_count") or 2)
            tp_pct, sl_pct = _tp_sl_from_confidence(conf)

            ex = _make_exchange()
            _load_markets(ex)  # chauffe le cache

            # ===== BUY =====
            if signal == "BUY":
                now = _now()
                st = dict(_state)
                if st.get("last_buy_ts", 0) and (now - st["last_buy_ts"] < BUY_COOL_SEC):
                    wait = BUY_COOL_SEC - (now - st["last_buy_ts"])
                    return jsonify({"ok": False, "reason": "buy_cooldown",
                                    "cooldown_remaining_sec": int(wait)}), 200

                if st.get("has_position"):
                    return jsonify({"ok": False, "reason": "position_already_open"}), 200

                requested_quote = float(payload.get("quote") or FIXED_QUOTE_PER_TRADE)
                if requested_quote < MIN_QUOTE_PER_TRADE:
                    return jsonify({"error": "sizing_error",
                                    "detail": f"Montant trop faible: min {MIN_QUOTE_PER_TRADE} {QUOTE_SYMBOL}"}), 400

                # cap par réserve QUOTE
                balances = ex.fetch_free_balance()
                avail_quote = float(balances.get(QUOTE_SYMBOL, balances.get(f"Z{QUOTE_SYMBOL}", 0.0)) or 0.0)
                usable_quote = max(0.0, avail_quote - QUOTE_RESERVE)
                quote_to_use = min(requested_quote, usable_quote)
                if quote_to_use <= 0:
                    return jsonify({"error": "not_enough_quote",
                                    "available": avail_quote, "quote_reserve": QUOTE_RESERVE}), 400

                # Ajuste SL si RISK_PCT plus strict
                if requested_quote * sl_pct > requested_quote * RISK_PCT:
                    sl_pct = RISK_PCT

                if ORDER_TYPE != "market":
                    return jsonify({"error": "Cette version ne gère que market"}), 400

                # --- Micro-chunking ---
                chunks = max(1, min(BUY_SPLIT_CHUNKS, 10))
                per_chunk_quote = quote_to_use / chunks

                total_qty = 0.0
                vw_cost = 0.0
                orders = []
                last_price = None

                for i in range(chunks):
                    try:
                        base_qty, price = _compute_base_qty_for_quote(ex, symbol, per_chunk_quote)
                    except Exception as e:
                        if chunks > 1:
                            log.warning("BUY chunk sizing failed (%s) -> fallback single", e)
                            base_qty, price = _compute_base_qty_for_quote(ex, symbol, quote_to_use)
                            chunks = 1
                        else:
                            raise

                    # arrondis finaux avant ordre
                    min_amount, _, step = _get_min_trade_info(ex, symbol, price)
                    if step:
                        base_qty = _round_floor(base_qty, step)
                    base_qty = _to_exchange_precision(ex, symbol, base_qty)

                    log.info("BUY[%d/%d] %s quote=%.2f -> qty=%.8f @~%.2f (resQ=%.2f, resB=%.8f)",
                             i+1, chunks, symbol, per_chunk_quote if chunks>1 else quote_to_use,
                             base_qty, price, QUOTE_RESERVE, BASE_RESERVE)

                    if DRY_RUN:
                        fill_price = price
                        order = {"dry_run": True, "side": "buy", "symbol": symbol,
                                 "qty": base_qty, "price": fill_price}
                    else:
                        if base_qty <= 0:
                            return jsonify({"error": "qty_rounding_zero"}), 400
                        order = ex.create_market_buy_order(symbol, base_qty)
                        fill_price = float(order.get("average") or order.get("price") or price)

                    total_qty += base_qty
                    vw_cost   += base_qty * fill_price
                    orders.append(order)
                    last_price = fill_price

                    if chunks > 1 and BUY_SPLIT_DELAY_MS > 0:
                        time.sleep(BUY_SPLIT_DELAY_MS / 1000.0)

                vwap = (vw_cost / total_qty) if total_qty > 0 else (last_price or 0.0)

                _with_state(lambda s: s.update({
                    "has_position": True,
                    "last_buy_ts": now,
                    "last_entry_price": vwap,
                    "last_qty": total_qty,
                    "symbol": symbol,
                }))

                if TRAILING_ENABLED and total_qty > 0:
                    threading.Thread(target=_monitor_trailing,
                                     args=(symbol, total_qty, vwap, conf, sl_pct),
                                     daemon=True).start()

                return jsonify({
                    "ok": True,
                    "side": "BUY",
                    "symbol": symbol,
                    "qty": total_qty,
                    "vwap": vwap,
                    "orders": orders,
                    "trailing": bool(TRAILING_ENABLED)
                }), 200

            # ===== SELL =====
            if signal == "SELL":
                st = dict(_state)
                symbol = _normalize_to_ccxt_symbol(st.get("symbol") or symbol)

                # solde en BASE
                base = symbol.split("/")[0]
                balances = ex.fetch_free_balance()
                free_base = float(
                    balances.get(base,
                                 balances.get(f"{base}.F",
                                              balances.get(f"X{base}", 0.0))) or 0.0
                )

                if free_base <= 0:
                    log.info("tv-kraken:Aucune quantité %s disponible pour SELL", base)
                    return jsonify({"ok": False, "reason": "no_base_available", "base_free": free_base}), 200

                # respect step/min
                t = ex.fetch_ticker(symbol)
                price = float(t.get("last") or t.get("close") or t.get("bid") or 0.0)
                min_amount, _, step = _get_min_trade_info(ex, symbol, price)
                qty_to_sell = free_base
                if step:
                    qty_to_sell = _round_floor(qty_to_sell, step)
                if min_amount and qty_to_sell < min_amount:
                    return jsonify({"ok": False, "reason": "below_min_amount",
                                    "qty": qty_to_sell, "min_amount": min_amount}), 200

                qty_to_sell = _to_exchange_precision(ex, symbol, qty_to_sell)
                if qty_to_sell <= 0:
                    return jsonify({"ok": False, "reason": "rounded_to_zero"}), 200

                orders = []
                if DRY_RUN:
                    orders.append({"dry_run": True, "side": "sell", "symbol": symbol, "qty": qty_to_sell})
                else:
                    # micro split SELL si demandé
                    chunks = max(1, min(SELL_SPLIT_CHUNKS, 10))
                    per = qty_to_sell / chunks
                    for i in range(chunks):
                        part = per
                        if step:
                            part = _round_floor(part, step)
                        part = _to_exchange_precision(ex, symbol, part)
                        if part > 0:
                            orders.append(ex.create_market_sell_order(symbol, part))
                        time.sleep(0.05)

                _with_state(lambda s: s.update({"has_position": False}))
                return jsonify({"ok": True, "side": "SELL", "symbol": symbol, "orders": orders}), 200

            # fallback
            return jsonify({"error": "unknown"}), 400

        except Exception as e:
            log.exception("ERROR:tv-kraken:Erreur serveur")
            return jsonify({"error": str(e)}), 500

# Charger l'état au démarrage du worker
_load_state()

# Entrée locale (Render utilisera gunicorn app:app)
if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=(LOG_LEVEL == "DEBUG"))
