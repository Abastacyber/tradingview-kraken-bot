import os
import json
import math
import time
import threading
import logging
from functools import lru_cache
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
MIN_QUOTE_PER_TRADE    = env_float("MIN_QUOTE_PER_TRADE", 10.0)      # garde-fou local
FEE_BUFFER_PCT         = env_float("FEE_BUFFER_PCT", 0.002)          # 0.2 %

# réserves
BASE_RESERVE           = env_float("BASE_RESERVE", 0.000005)
QUOTE_RESERVE          = env_float("QUOTE_RESERVE", 5.0)

# gestion risque / SL
RISK_PCT               = env_float("RISK_PCT", 0.02)     # max perte vs ticket (2%)
MAX_SL_PCT             = env_float("MAX_SL_PCT", 0.05)   # SL dur max (5%)

# cooldown achat (NB: c'est bien BUY_COOL_SEC que le code lit)
BUY_COOL_SEC           = env_int("BUY_COOL_SEC", 300)

# sécurité / bac à sable
DRY_RUN                = env_str("DRY_RUN", "false").lower() in ("1", "true", "yes")

# --- Secret compatible TradingView ---
WEBHOOK_SECRET         = env_str("WEBHOOK_SECRET", env_str("WEBHOOK_TOKEN", ""))
if not WEBHOOK_SECRET:
    # Webhook ouvert (utile pour tests uniquement)
    logging.warning("Aucun WEBHOOK_SECRET défini – webhook ouvert (OK pour tests)")

# Trailing côté bot
TRAILING_ENABLED         = env_str("TRAILING_ENABLED", "true").lower() in ("1", "true", "yes")
TRAIL_ACTIVATE_PCT_CONF2 = env_float("TRAIL_ACTIVATE_PCT_CONF2", 0.004) # +0.40 %
TRAIL_GAP_CONF2          = env_float("TRAIL_GAP_CONF2",        0.002)   # 0.20 %
TRAIL_ACTIVATE_PCT_CONF3 = env_float("TRAIL_ACTIVATE_PCT_CONF3", 0.006) # +0.60 %
TRAIL_GAP_CONF3          = env_float("TRAIL_GAP_CONF3",        0.003)   # 0.30 %

# Persistance d'état (simple fichier json)
STATE_FILE             = env_str("STATE_FILE", "/tmp/bot_state.json")
RESTORE_ON_START       = env_str("RESTORE_ON_START", "true").lower() in ("1", "true", "yes")

# Clés API
API_KEY                = env_str("KRAKEN_API_KEY", "")
API_SECRET             = env_str("KRAKEN_API_SECRET", "")

# Kraken options
KRAKEN_ENV             = env_str("KRAKEN_ENV", "mainnet").lower()         # "testnet" | "mainnet"
KRAKEN_DEFAULT_TYPE    = env_str("KRAKEN_DEFAULT_TYPE", "spot").lower()   # "spot" | "swap"

# Volume / micro-chunking (optionnel)
BUY_SPLIT_CHUNKS       = max(1, env_int("BUY_SPLIT_CHUNKS", 2))       # ex: 3 -> 3 ordres par BUY
BUY_SPLIT_DELAY_MS     = max(0, env_int("BUY_SPLIT_DELAY_MS", 500))   # pause entre micro-ordres
SELL_SPLIT_CHUNKS      = max(1, env_int("SELL_SPLIT_CHUNKS", 1))      # ex: 2 -> 2 ordres par SELL

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
    """
    Normalise un symbole éventuel en unified ccxt.
    - supporte "XBT" -> "BTC"
    - accepte "BTC/EUR", "btceur", "BTC-EUR"
    - fallback sur SYMBOL_DEFAULT si on ne sait pas parser
    """
    if not s:
        return SYMBOL_DEFAULT

    s = s.replace("-", "/").upper()
    if "/" not in s:
        for q in ("USDT", "USD", "USDC", "EUR", "BTC", "ETH"):
            if s.endswith(q):
                base = s[:-len(q)]
                s = f"{base}/{q}"
                break
        else:
            return SYMBOL_DEFAULT

    base, quote = s.split("/")
    if base == "XBT":  # alias Kraken
        base = "BTC"
    return f"{base}/{quote}"

def _make_exchange():
    _assert_env()
    ex = ccxt.kraken({
        "apiKey": API_KEY,
        "secret": API_SECRET,
        "options": {"defaultType": KRAKEN_DEFAULT_TYPE},  # spot ou swap
        "enableRateLimit": True,
    })
    if KRAKEN_ENV in ("testnet", "sandbox", "demo", "paper", "true", "1", "yes"):
        try:
            ex.set_sandbox_mode(True)
        except Exception:
            pass
    return ex

@lru_cache(maxsize=1)
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

    # Sanitize min_amount aberrant (ex: 1 BTC -> absurde en spot)
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
    """Retourne (qty_base_arrondie, prix) pour convertir un montant QUOTE -> BASE,
       en respectant minCost / minAmount / qtyStep, avec sanitation.
    """
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

    # contrôle final côté coût/qty après arrondi
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
    # (tp_pct, sl_pct) — ici non exploité par des ordres limites, conservé pour logs/monitor
    return (0.008, 0.005) if conf >= 3 else (0.003, 0.002)

def _trail_params(conf: int) -> Tuple[float, float]:
    return ((TRAIL_ACTIVATE_PCT_CONF3, TRAIL_GAP_CONF3)
            if conf >= 3 else
            (TRAIL_ACTIVATE_PCT_CONF2, TRAIL_GAP_CONF2))

# ========= Trailing monitor =========
def _monitor_trailing(symbol: str, qty: float, entry_price: float, conf: int, base_sl_pct: float):
    if not TRAILING_ENABLED or qty <= 0:
        return

    ex = _make_exchange()

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

            # Support PING de test
            if signal == "PING":
                return jsonify({"ok": True, "pong": True, "ts": int(time.time())}), 200

            if signal not in {"BUY", "SELL"}:
                return jsonify({"error": "signal invalide (BUY/SELL/PING)"}), 400

            symbol = _normalize_to_ccxt_symbol(payload.get("symbol") or _state.get("symbol", SYMBOL_DEFAULT))
            conf = int(payload.get("confidence") or payload.get("indicators_count") or 2)
            tp_pct, sl_pct = _tp_sl_from_confidence(conf)

            ex = _make_exchange()

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
                avail_quote = float(balances.get(QUOTE_SYMBOL, 0.0))
                usable_quote = max(0.0, avail_quote - QUOTE_RESERVE)
                quote_to_use = min(requested_quote, usable_quote)
                if quote_to_use <= 0:
                    return jsonify({"error": "Pas assez de QUOTE (réserve incluse)",
                                    "available": avail_quote, "quote_reserve": QUOTE_RESERVE}), 400

                # Ajuste SL si RISK_PCT plus strict
                if requested_quote * sl_pct > requested_quote * RISK_PCT:
                    log.warning("SL %.2f%% > RISK_PCT %.2f%% -> borné à RISK_PCT",
                                sl_pct*100, RISK_PCT*100)
                    sl_pct = RISK_PCT

                if ORDER_TYPE != "market":
                    return jsonify({"error": "Cette version ne gère que market"}), 400

                # --- Micro-chunking ---
                chunks = max(1, min(BUY_SPLIT_CHUNKS, 10))
                per_chunk_quote = quote_to_use / chunks

                total_qty = 0.0
                vw_cost = 0.0
                orders = []

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
                        order = ex.create_market_buy_order(symbol, base_qty)
                        fill_price = float(order.get("average") or order.get("price") or price)

                    total_qty += base_qty
                    vw_cost   += base_qty * fill_price
                    orders.append(order)

                    if chunks > 1 and BUY_SPLIT_DELAY_MS > 0:
                        time.sleep(BUY_SPLIT_DELAY_MS / 1000.0)

                vwap = (vw_cost / total_qty) if total_qty > 0 else price

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

                return jsonify({"ok": True,
                                "orders": orders,
                                "total_qty": total_qty,
                                "avg_price": vwap,
                                "tp_pct": tp_pct, "sl_pct": sl_pct,
                                "confidence": conf,
                                "trailing_enabled": TRAILING_ENABLED}), 200

            # ===== SELL =====
            force_close = bool(payload.get("force_close") or False)
            qty_override = payload.get("qty_base")
            balances = ex.fetch_free_balance()
            base_code = symbol.split("/")[0]
            avail_base = float(balances.get(base_code, 0.0))
            sellable = max(0.0, avail_base - BASE_RESERVE)

            if force_close:
                base_qty_to_sell = sellable
            elif qty_override is not None:
                try:
                    base_qty_to_sell = float(qty_override)
                except Exception:
                    return jsonify({"error": "qty_base invalide"}), 400
            else:
                requested_quote = float(payload.get("quote") or FIXED_QUOTE_PER_TRADE)
                try:
                    base_qty_to_sell, price = _compute_base_qty_for_quote(ex, symbol, requested_quote)
                except Exception as e:
                    log.warning("Sizing error SELL: %s", e)
                    return jsonify({"error": "sizing_error", "detail": str(e)}), 400

            base_qty_to_sell = min(base_qty_to_sell, sellable)
            if base_qty_to_sell <= 0:
                return jsonify({"error": "Pas de quantité base vendable (réserve incluse)",
                                "available_base": avail_base, "base_reserve": BASE_RESERVE}), 400

            if ORDER_TYPE != "market":
                return jsonify({"error": "Cette version ne gère que market"}), 400

            # Split SELL si demandé
            sell_chunks = max(1, min(SELL_SPLIT_CHUNKS, 10))
            min_amount, _, step = _get_min_trade_info(ex, symbol, float(ex.fetch_ticker(symbol).get("last") or 0.0))
            chunk_qty = base_qty_to_sell / sell_chunks
            if step:
                chunk_qty = _round_floor(chunk_qty, step)

            # si le chunk devient trop petit, fallback en 1
            if (min_amount and chunk_qty < min_amount) or chunk_qty <= 0:
                sell_chunks = 1
                chunk_qty = base_qty_to_sell

            orders = []
            remaining = base_qty_to_sell

            log.info("SELL %s total=%.8f (chunks=%d, chunk_qty≈%.8f, avail=%.8f, reserve=%.8f)",
                     symbol, base_qty_to_sell, sell_chunks, chunk_qty, avail_base, BASE_RESERVE)

            if DRY_RUN:
                _with_state(lambda s: s.update({"has_position": False, "last_qty": 0.0}))
                return jsonify({"ok": True, "dry_run": True, "action": "SELL",
                                "symbol": symbol, "qty": base_qty_to_sell}), 200

            for i in range(sell_chunks):
                q = chunk_qty if i < sell_chunks - 1 else remaining
                if step:
                    q = _round_floor(q, step)
                q = _to_exchange_precision(ex, symbol, q)
                if q <= 0:
                    continue
                try:
                    order = ex.create_market_sell_order(symbol, q)
                    orders.append(order)
                    remaining -= q
                except Exception as e:
                    log.warning("SELL chunk %d failed: %s", i+1, e)
                    if sell_chunks > 1 and remaining > 0:
                        try:
                            q2 = _to_exchange_precision(ex, symbol, remaining)
                            order = ex.create_market_sell_order(symbol, q2)
                            orders.append(order)
                            remaining = 0
                        except Exception as e2:
                            log.warning("SELL fallback failed: %s", e2)
                    break

            _with_state(lambda s: s.update({"has_position": False, "last_qty": 0.0}))
            return jsonify({"ok": True, "orders": orders, "sold_total": base_qty_to_sell}), 200

        except ccxt.InsufficientFunds as e:
            log.warning("InsufficientFunds: %s", e)
            return jsonify({"error": "InsufficientFunds", "detail": str(e)}), 400
        except ccxt.NetworkError as e:
            log.exception("NetworkError")
            return jsonify({"error": "NetworkError", "detail": str(e)}), 503
        except ccxt.BaseError as e:
            log.exception("ExchangeError")
            return jsonify({"error": "ExchangeError", "detail": str(e)}), 502
        except Exception as e:
            log.exception("Erreur serveur")
            return jsonify({"error": str(e)}), 500

# ========= Boot =========
_load_state()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "3000"))
    app.run(host="0.0.0.0", port=port)
