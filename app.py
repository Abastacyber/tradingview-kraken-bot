import os
import json
import math
import time
import threading
import logging

from collections import OrderedDict
from functools import lru_cache
from typing import Any, Dict, Tuple, Optional, Callable
from flask import Flask, request, jsonify
import ccxt


# ===== Helpers ENV =====
def env_str(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return v if v not in (None, "") else default

def env_float(name: str, default: float = 0.0) -> float:
    try: return float(env_str(name, str(default)))
    except: return float(default)

def env_int(name: str, default: int = 0) -> int:
    try: return int(float(env_str(name, str(default))))
    except: return int(default)

# ===== ENV =====
LOG_LEVEL              = env_str("LOG_LEVEL", "INFO").upper()
EXCHANGE_NAME          = env_str("EXCHANGE", "kraken").lower()
BASE_SYMBOL            = env_str("BASE_SYMBOL", "BTC").upper()
QUOTE_SYMBOL           = env_str("QUOTE_SYMBOL", "EUR").upper()
SYMBOL_DEFAULT         = f"{BASE_SYMBOL}/{QUOTE_SYMBOL}"
ORDER_TYPE             = env_str("ORDER_TYPE", "market").lower()

FIXED_QUOTE_PER_TRADE  = env_float("FIXED_QUOTE_PER_TRADE", 25.0)
MIN_QUOTE_PER_TRADE    = env_float("MIN_QUOTE_PER_TRADE", 15.0)
FEE_BUFFER_PCT         = env_float("FEE_BUFFER_PCT", 0.002)

BASE_RESERVE           = env_float("BASE_RESERVE", 0.0)
QUOTE_RESERVE          = env_float("QUOTE_RESERVE", 0.0)

RISK_PCT               = env_float("RISK_PCT", 0.02)
MAX_SL_PCT             = env_float("MAX_SL_PCT", 0.05)
BUY_COOL_SEC           = env_int("BUY_COOL_SEC", 300)

DRY_RUN                = env_str("DRY_RUN", "false").lower() in ("1","true","yes")
WEBHOOK_SECRET         = env_str("WEBHOOK_SECRET", env_str("WEBHOOK_TOKEN", ""))

TRAILING_ENABLED          = env_str("TRAILING_ENABLED","true").lower() in ("1","true","yes")
TRAIL_ACTIVATE_PCT_CONF2  = env_float("TRAIL_ACTIVATE_PCT_CONF2", 0.004)
TRAIL_GAP_CONF2           = env_float("TRAIL_GAP_CONF2", 0.002)
TRAIL_ACTIVATE_PCT_CONF3  = env_float("TRAIL_ACTIVATE_PCT_CONF3", 0.006)
TRAIL_GAP_CONF3           = env_float("TRAIL_GAP_CONF3", 0.003)

STATE_FILE             = env_str("STATE_FILE", "/tmp/bot_state.json")
RESTORE_ON_START       = env_str("RESTORE_ON_START","true").lower() in ("1","true","yes")

API_KEY                = env_str("KRAKEN_API_KEY", env_str("API_KEY",""))
API_SECRET             = env_str("KRAKEN_API_SECRET", env_str("API_SECRET",""))

KRAKEN_ENV             = env_str("KRAKEN_ENV","mainnet").lower()
KRAKEN_DEFAULT_TYPE    = env_str("KRAKEN_DEFAULT_TYPE","spot").lower()

BUY_SPLIT_CHUNKS       = max(1, env_int("BUY_SPLIT_CHUNKS", 1))
BUY_SPLIT_DELAY_MS     = max(0, env_int("BUY_SPLIT_DELAY_MS", 300))
SELL_SPLIT_CHUNKS      = max(1, env_int("SELL_SPLIT_CHUNKS", 1))

# --- Shorting (margin spot)
ENABLE_SHORTING        = env_str("ENABLE_SHORTING","false").lower() in ("1","true","yes")
MARGIN_LEVERAGE        = max(1, env_int("MARGIN_LEVERAGE", 2))
ALLOW_PAYLOAD_SYMBOL   = env_str("ALLOW_PAYLOAD_SYMBOL","false").lower() in ("1","true","yes")

# ===== ORION Protocol v1 =====
APP_VERSION = env_str("APP_VERSION", "1.0.0-dev")
ORION_PROTOCOL = env_str("ORION_PROTOCOL", "orion-v1")

ALERT_MAX_AGE_SEC = env_int("ALERT_MAX_AGE_SEC", 120)
MIN_CONFIDENCE = env_int("MIN_CONFIDENCE", 60)
MAX_PRICE_DEVIATION_PCT = env_float(
    "MAX_PRICE_DEVIATION_PCT",
    0.005,
)
MAX_SEEN_ALERTS = max(
    100,
    env_int("MAX_SEEN_ALERTS", 2000),
)

# ===== Logs/Flask/State =====
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO))
log = logging.getLogger("tv-kraken")
app = Flask(__name__)

_state_lock = threading.Lock()
_position_lock = threading.Lock()
_seen_alerts_lock = threading.Lock()
_seen_alerts = OrderedDict()
_state: Dict[str, Any] = {
    "has_position": False,         # True si long ouvert
    "last_buy_ts": 0.0,
    "last_entry_price": 0.0,
    "last_qty": 0.0,
    "position_side": "none",       # "none" | "long" | "short"
    "symbol": SYMBOL_DEFAULT,
}

def _now() -> float: return time.time()

def _save_state():
    try:
        with _state_lock: tmp = json.dumps(_state)
        with open(STATE_FILE, "w", encoding="utf-8") as f: f.write(tmp)
    except Exception as e:
        log.warning("STATE save error: %s", e)

def _load_state():
    if not RESTORE_ON_START: return
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as f: data = json.load(f)
            with _state_lock: _state.update(data)
            log.info("STATE restored: %s", json.dumps(_state))
    except Exception as e:
        log.warning("STATE load error: %s", e)

def _with_state(mutator: Callable[[Dict[str, Any]], None]):
    with _state_lock:
        mutator(_state)
        snap = dict(_state)
    _save_state()
    return snap

# ===== Exchange helpers =====
def _assert_env():
    if EXCHANGE_NAME != "kraken":
        raise RuntimeError(f"Exchange non supporté: {EXCHANGE_NAME}")
    if not API_KEY or not API_SECRET:
        raise RuntimeError("KRAKEN_API_KEY / KRAKEN_API_SECRET manquants")

def _normalize_to_ccxt_symbol(s: str) -> str:
    if not s: return SYMBOL_DEFAULT
    s = s.replace("-", "/").upper()
    if "/" not in s:
        for q in ("USDT","USD","USDC","EUR","BTC","ETH"):
            if s.endswith(q):
                s = f"{s[:-len(q)]}/{q}"
                break
        else:
            return SYMBOL_DEFAULT
    base, quote = s.split("/")
    if base == "XBT": base = "BTC"
    return f"{base}/{quote}"

def _maybe_symbol_from_payload(payload_symbol: Optional[str]) -> str:
    if ALLOW_PAYLOAD_SYMBOL and payload_symbol:
        return _normalize_to_ccxt_symbol(payload_symbol)
    return _state.get("symbol", SYMBOL_DEFAULT)

def _make_exchange():
    _assert_env()
    ex = ccxt.kraken({
        "apiKey": API_KEY,
        "secret": API_SECRET,
        "options": {"defaultType": KRAKEN_DEFAULT_TYPE},
        "enableRateLimit": True,
    })
    if KRAKEN_ENV in ("testnet","sandbox","demo","paper","true","1","yes"):
        try: ex.set_sandbox_mode(True)
        except Exception: pass
    return ex

@lru_cache(maxsize=1)
def _load_markets(ex): return ex.load_markets()

def _amount_step_from_market(
    market: Dict[str, Any],
) -> Optional[float]:

    precision = (
        market.get("precision") or {}
    ).get("amount")

    if precision is not None:
        try:
            precision = float(precision)

            # CCXT peut fournir directement le pas minimal.
            if 0 < precision < 1:
                return precision

            # Compatibilité avec un format exprimé en décimales.
            if precision >= 1:
                return 10 ** (-int(precision))

        except (TypeError, ValueError, OverflowError):
            pass

    info = market.get("info") or {}

    for key in ("lotSz", "lotSize", "qtyStep", "minQty"):
        if key in info:
            try:
                value = float(info[key])
                if value > 0:
                    return value
            except (TypeError, ValueError):
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
    if min_amount and price and (min_amount * price) > 200:
        log.warning("Ignoring absurd min_amount=%s (~%.2f %s)", min_amount, min_amount*price, symbol.split("/")[1])
        min_amount = 0.0
    return min_amount, min_cost, step

def _round_floor(value: float, step: float) -> float:
    if not step or step <= 0: return value
    return math.floor(value / step) * step

def _to_exchange_precision(ex, symbol: str, amount: float) -> float:
    try: return float(ex.amount_to_precision(symbol, amount))
    except: return amount

def _compute_base_qty_for_quote(ex, symbol: str, quote_amt: float) -> Tuple[float, float]:
    t = ex.fetch_ticker(symbol)
    price = float(t.get("last") or t.get("close") or t.get("ask") or t.get("bid") or 0.0)
    if price <= 0: raise RuntimeError("Prix invalide (ticker)")
    min_amount, min_cost, step = _get_min_trade_info(ex, symbol, price)
    qty = (quote_amt / price) * (1.0 - FEE_BUFFER_PCT)
    if min_cost and (qty * price) < min_cost: qty = min_cost / price
    if min_amount and qty < min_amount: qty = min_amount
    if step: qty = _round_floor(qty, step)
    if qty <= 0:
        required_quote = max(min_cost, (min_amount or 0)*price) or 0.0
        required_quote *= (1.0 + FEE_BUFFER_PCT)
        raise RuntimeError(f"Montant trop faible. Essaie >= ~{required_quote:.2f} {symbol.split('/')[1]}")
    return qty, price

def _tp_sl_from_confidence(conf: int) -> Tuple[float, float]:
    return (0.008, 0.005) if conf >= 3 else (0.003, 0.002)

def _trail_params(conf: int) -> Tuple[float, float]:
    return ((TRAIL_ACTIVATE_PCT_CONF3, TRAIL_GAP_CONF3) if conf >= 3 else (TRAIL_ACTIVATE_PCT_CONF2, TRAIL_GAP_CONF2))

# ===== Trailing (long only, simple) =====
def _monitor_trailing(symbol: str, qty: float, entry: float, conf: int, base_sl_pct: float):
    if not TRAILING_ENABLED or qty <= 0: return
    ex = _make_exchange()
    activate_pct, gap = _trail_params(conf)
    max_price = entry
    base_sl_pct = min(base_sl_pct, MAX_SL_PCT)
    initial_stop = entry * (1.0 - base_sl_pct)
    activated = False
    log.info("[TRAIL] start %s qty=%.8f entry=%.2f conf=%s baseSL=%.4f", symbol, qty, entry, conf, base_sl_pct)
    while True:
        try:
            t = ex.fetch_ticker(symbol)
            last = float(t.get("last") or t.get("close") or 0.0)
            if last <= 0: time.sleep(3); continue
            if last <= initial_stop:
                log.warning("[TRAIL] initial SL hit (%.2f <= %.2f) -> SELL", last, initial_stop)
                try:
                    _, _, step = _get_min_trade_info(ex, symbol, last)
                    q = _round_floor(qty, step) if step else qty
                    q = _to_exchange_precision(ex, symbol, q)
                    if not DRY_RUN: ex.create_market_sell_order(symbol, q)
                except Exception as e:
                    log.warning("[TRAIL] SELL initial failed: %s", e)
                _with_state(lambda s: s.update({"has_position": False, "position_side": "none"}))
                break
            if not activated and last >= entry * (1.0 + activate_pct):
                activated = True
                log.info("[TRAIL] activated at %.2f", last)
            if activated:
                if last > max_price: max_price = last
                trail_stop = max(initial_stop, max_price * (1.0 - gap))
                if last <= trail_stop:
                    log.info("[TRAIL] stop hit %.2f <= %.2f -> SELL", last, trail_stop)
                    try:
                        _, _, step = _get_min_trade_info(ex, symbol, last)
                        q = _round_floor(qty, step) if step else qty
                        q = _to_exchange_precision(ex, symbol, q)
                        if not DRY_RUN: ex.create_market_sell_order(symbol, q)
                    except Exception as e:
                        log.warning("[TRAIL] SELL failed: %s", e)
                    _with_state(lambda s: s.update({"has_position": False, "position_side": "none"}))
                    break
            time.sleep(3)
        except Exception as e:
            log.warning("[TRAIL] error: %s", e)
            time.sleep(3)
    log.info("[TRAIL] finished")


def _remember_alert(alert_id: str) -> bool:
    """
    Enregistre un identifiant d'alerte.

    Retourne False si l'alerte a déjà été reçue.
    """
    with _seen_alerts_lock:
        if alert_id in _seen_alerts:
            return False

        _seen_alerts[alert_id] = time.time()

        while len(_seen_alerts) > MAX_SEEN_ALERTS:
            _seen_alerts.popitem(last=False)

        return True


def _validate_orion_payload(
    payload: Dict[str, Any],
) -> Tuple[bool, str]:
    """
    Valide un message ORION Protocol v1.

    Retourne :
        (True, "") si le message est valide.
        (False, "raison") si le message doit être refusé.
    """

    # 1. Vérification du protocole
    protocol = str(payload.get("protocol", "")).strip()

    if protocol != ORION_PROTOCOL:
        return False, "invalid_protocol"

    # 2. Vérification du signal
    signal = str(payload.get("signal", "")).strip().upper()

    if signal not in {"BUY", "SELL", "PING"}:
        return False, "invalid_signal"

    # PING est seulement un test de communication.
    # Il n'a pas besoin des informations d'un trade.
    if signal == "PING":
        return True, ""

    # 3. Vérification de l'identifiant unique
    alert_id = str(payload.get("alert_id", "")).strip()

    if not alert_id:
        return False, "missing_alert_id"

    if len(alert_id) > 160:
        return False, "invalid_alert_id"

    # 4. Vérification du symbole
    raw_symbol = str(payload.get("symbol", "")).strip()

    if not raw_symbol:
        return False, "missing_symbol"

    symbol = _normalize_to_ccxt_symbol(raw_symbol)

    if symbol != SYMBOL_DEFAULT:
        return False, "invalid_symbol"

    # 5. Vérification du timeframe
    timeframe = str(payload.get("timeframe", "")).strip()

    if timeframe != "15":
        return False, "invalid_timeframe"

    # 6. Vérification de la confiance
    try:
        confidence = int(payload.get("confidence"))
    except (TypeError, ValueError):
        return False, "invalid_confidence"

    if confidence < 0 or confidence > 100:
        return False, "invalid_confidence"

    if confidence < MIN_CONFIDENCE:
        return False, "confidence_too_low"

    # 7. Vérification du prix TradingView
    try:
        price = float(payload.get("price"))
    except (TypeError, ValueError):
        return False, "invalid_price"

    if not math.isfinite(price) or price <= 0:
        return False, "invalid_price"


    # 8. Vérification de l'heure réelle d'envoi
    try:
        sent_at_ms = int(payload.get("sent_at"))
    except (TypeError, ValueError):
        return False, "invalid_sent_at"

    if sent_at_ms <= 0:
        return False, "invalid_sent_at"

    alert_time_sec = sent_at_ms / 1000.0
    alert_age_sec = time.time() - alert_time_sec

    if alert_age_sec > ALERT_MAX_AGE_SEC:
        return False, "stale_alert"

    # Tolérance de 30 secondes en cas de léger décalage d'horloge.
    if alert_age_sec < -30:
        return False, "future_alert"

    # 9. Vérification du Stop Loss pour un BUY
    if signal == "BUY":
        try:
            stop_price = float(payload.get("stop_price"))
        except (TypeError, ValueError):
            return False, "invalid_stop_price"

        if stop_price <= 0:
            return False, "invalid_stop_price"

        if stop_price >= price:
            return False, "stop_loss_above_entry"

        stop_distance_pct = (price - stop_price) / price

        if stop_distance_pct > MAX_SL_PCT:
            return False, "stop_loss_too_wide"

    return True, ""

# ===== ORION Decision Engine =====
def _orion_decide(
    payload: Dict[str, Any],
) -> Tuple[str, str]:
    """
    Première couche du moteur de décision ORION.

    Retourne :
        ("execute", "reason") -> le trade peut continuer
        ("reject", "reason")  -> le trade doit être refusé
        ("wait", "reason")    -> réservé aux futures décisions ORION

    Cette version reste volontairement prudente.
    Elle prépare l'architecture du futur cerveau ORION
    sans encore remplacer la stratégie TradingView.
    """

    signal = str(
        payload.get("signal", "")
    ).strip().upper()

    confidence = int(
        payload.get("confidence", 0)
    )

    # PING n'est jamais une décision de trading.
    if signal == "PING":
        return "reject", "ping_not_trade"

    # Sécurité supplémentaire.
    if signal not in {"BUY", "SELL"}:
        return "reject", "unsupported_signal"

    # Première règle décisionnelle ORION.
    # Une confiance insuffisante ne doit jamais atteindre Kraken.
    if confidence < MIN_CONFIDENCE:
        return "reject", "confidence_below_orion_threshold"

    # Pour cette première version,
    # ORION autorise le trade après les contrôles précédents.
    return "execute", "orion_v1_pass"

# ===== Routes =====
@app.get("/")
def index():
    return jsonify({"service": "tv-kraken-bot", "status": "ok"}), 200

@app.get("/health")
def health():
    return jsonify({
        "status": "ok",
        "service": "orion",
        "version": APP_VERSION,
        "protocol": ORION_PROTOCOL,
        "symbol_default": SYMBOL_DEFAULT,
        "creds_ok": bool(API_KEY and API_SECRET),
        "secret_set": bool(WEBHOOK_SECRET),
        "dry_run": DRY_RUN,
        "shorting": ENABLE_SHORTING,
        "has_position": bool(_state["has_position"]),
        "ts": int(time.time())
    }), 200

@app.get("/debug/balances")
def debug_balances():
    try:
        if WEBHOOK_SECRET:
            tok = (request.args.get("secret") or request.headers.get("X-Webhook-Token"))
            if tok != WEBHOOK_SECRET:
                return jsonify({"error": "unauthorized"}), 401
        ex = _make_exchange()
        b = ex.fetch_balance()
        return jsonify({"free": b.get("free", {}), "used": b.get("used", {}), "total": b.get("total", {})}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.post("/webhook")
def webhook():
    with _position_lock:
        try:
            payload = request.get_json(silent=True) or {}
            if WEBHOOK_SECRET:
                tok = (payload.get("secret") or request.args.get("secret")
                       or payload.get("token") or request.args.get("token")
                       or request.headers.get("X-Webhook-Token"))
                if tok != WEBHOOK_SECRET:
                    log.error("Bad secret")
                    return jsonify({"error": "unauthorized"}), 401

            safe = dict(payload); safe.pop("secret", None); safe.pop("token", None)
            log.info("Webhook payload: %s", json.dumps(safe, ensure_ascii=False))
            is_valid, validation_error = _validate_orion_payload(payload)

            if not is_valid:
                log.warning(
                    "ORION payload rejected: %s",
                    validation_error,
                )
                return jsonify({
                    "accepted": False,
                    "decision": "rejected",
                    "reason": validation_error,
                    "protocol_expected": ORION_PROTOCOL,
                }), 400

            signal = str(
                payload.get("signal", "")
            ).strip().upper()

            # PING = simple test de communication.
            # Il ne doit pas être enregistré comme une alerte de trading.
            if signal == "PING":
                return jsonify({
                    "ok": True,
                    "pong": True,
                    "protocol": ORION_PROTOCOL,
                    "ts": int(time.time()),
                }), 200

            # Protection contre les alertes reçues plusieurs fois.
            alert_id = str(
                payload.get("alert_id", "")
            ).strip()

            if not _remember_alert(alert_id):
                log.warning(
                    "Duplicate ORION alert rejected: %s",
                    alert_id,
                )
                return jsonify({
                    "accepted": False,
                    "decision": "rejected",
                    "reason": "duplicate_alert",
                    "alert_id": alert_id,
                }), 409

            # Sécurité supplémentaire, même si le validateur
            # a déjà vérifié le signal.
            if signal not in {"BUY", "SELL"}:
                return jsonify({
                    "accepted": False,
                    "decision": "rejected",
                    "reason": "invalid_signal",
                    "allowed_signals": ["BUY", "SELL", "PING"],
                }), 400
            # ===== ORION Decision Gate =====
            decision, decision_reason = _orion_decide(payload)

            log.info(
                "ORION decision=%s reason=%s alert_id=%s",
                decision,
                decision_reason,
                alert_id,
            )

            if decision != "execute":
                return jsonify({
                    "accepted": True,
                    "decision": decision,
                    "reason": decision_reason,
                    "alert_id": alert_id,
                }), 200
            symbol = _maybe_symbol_from_payload(
                payload.get("symbol")
            )

            conf = int(
                payload.get("confidence")
                or payload.get("indicators_count")
                or 2
            )
            reason = str(payload.get("reason",""))[:160]
            tp_pct, sl_pct = _tp_sl_from_confidence(conf)

            ex = _make_exchange()
            _load_markets(ex)

            # ============= BUY (open long OR close short) =============
            if signal == "BUY":
                st = dict(_state)
                # Si short ouvert -> BUY ferme le short (quantité connue)
                if st.get("position_side") == "short" and st.get("last_qty", 0) > 0:
                    qty_to_buy = st["last_qty"]
                    qty_to_buy = _to_exchange_precision(ex, symbol, qty_to_buy)
                    if not DRY_RUN: order = ex.create_market_buy_order(symbol, qty_to_buy)
                    else: order = {"dry_run": True, "side":"buy", "qty": qty_to_buy}
                    _with_state(lambda s: s.update({
                        "has_position": False, "position_side":"none", "last_qty":0.0
                    }))
                    return jsonify({"ok": True, "side":"buy-close-short", "symbol": symbol,
                                    "amount": qty_to_buy, "order": order, "confidence": conf,
                                    "reason": reason}), 200

                # Sinon on ouvre un long (comme avant)
                now = _now()
                if _state.get("last_buy_ts", 0) and (now - _state["last_buy_ts"] < BUY_COOL_SEC):
                    wait = BUY_COOL_SEC - (now - _state["last_buy_ts"])
                    return jsonify({"ok": False, "reason":"buy_cooldown",
                                    "cooldown_remaining_sec": int(wait)}), 200

                requested_quote = float(payload.get("quote") or FIXED_QUOTE_PER_TRADE)
                if requested_quote < MIN_QUOTE_PER_TRADE:
                    return jsonify({"error":"sizing_error",
                                    "detail": f"Montant trop faible: min {MIN_QUOTE_PER_TRADE} {QUOTE_SYMBOL}"}), 400

                balances = ex.fetch_free_balance()
                avail_quote = float(balances.get(QUOTE_SYMBOL, 0.0))
                usable_quote = max(0.0, avail_quote - QUOTE_RESERVE)
                quote_to_use = min(requested_quote, usable_quote)
                if quote_to_use <= 0:
                    return jsonify({"error":"insufficient_quote","available":avail_quote,
                                    "quote_reserve": QUOTE_RESERVE}), 400

                chunks = max(1, min(BUY_SPLIT_CHUNKS, 10))
                per_chunk_quote = quote_to_use / chunks
                total_qty, vw_cost = 0.0, 0.0
                last_price, orders = 0.0, []
                for i in range(chunks):
                    base_qty, price = _compute_base_qty_for_quote(ex, symbol, per_chunk_quote)
                    last_price = price
                    _, _, step = _get_min_trade_info(ex, symbol, price)
                    if step: base_qty = _round_floor(base_qty, step)
                    base_qty = _to_exchange_precision(ex, symbol, base_qty)
                    if DRY_RUN:
                        fill_price = price
                        order = {"dry_run":True, "side":"buy", "symbol":symbol, "qty":base_qty, "price":fill_price}
                    else:
                        order = ex.create_market_buy_order(symbol, base_qty)
                        fill_price = float(order.get("average") or order.get("price") or price)
                    total_qty += base_qty
                    vw_cost += base_qty * fill_price
                    orders.append(order)
                    if chunks > 1 and BUY_SPLIT_DELAY_MS > 0:
                        time.sleep(BUY_SPLIT_DELAY_MS/1000.0)
                vwap = (vw_cost / total_qty) if total_qty > 0 else last_price

                _with_state(lambda s: s.update({
                    "has_position": True, "position_side":"long",
                    "last_buy_ts": _now(), "last_entry_price": vwap,
                    "last_qty": total_qty, "symbol": symbol
                }))

                if TRAILING_ENABLED and total_qty > 0:
                    threading.Thread(target=_monitor_trailing,
                                     args=(symbol, total_qty, vwap, conf, min(sl_pct, RISK_PCT)),
                                     daemon=True).start()
                return jsonify({"ok": True, "side":"buy-open-long", "symbol": symbol,
                                "amount": total_qty, "avg_price": vwap,
                                "orders": orders, "confidence": conf, "reason": reason}), 200

            # ============= SELL (close long OR open short) =============
            if signal == "SELL":

                # En DRY_RUN, fermer d'abord la position longue simulée.
                if DRY_RUN:
                    st = dict(_state)

                    if (
                        st.get("position_side") == "long"
                        and st.get("has_position")
                        and st.get("last_qty", 0) > 0
                    ):
                        qty_to_sell = float(st["last_qty"])
                        order = {
                            "dry_run": True,
                            "side": "sell",
                            "symbol": symbol,
                            "qty": qty_to_sell,
                        }

                        _with_state(
                            lambda s: s.update({
                                "has_position": False,
                                "position_side": "none",
                                "last_qty": 0.0,
                            })
                        )

                        return jsonify({
                            "ok": True,
                            "side": "sell-close-long",
                            "symbol": symbol,
                            "amount": qty_to_sell,
                            "order": order,
                            "reason": reason,
                        }), 200

                balances = ex.fetch_free_balance()
                base = symbol.split("/")[0]
                base_free = float(balances.get(base, 0.0))

                # 1) S'il y a du BTC libre -> on ferme le long
                if base_free > 0:
                    ticker = ex.fetch_ticker(symbol)
                    price  = float(ticker.get("last") or ticker.get("close") or 0.0) or 1.0
                    min_amount, _, step = _get_min_trade_info(ex, symbol, price)
                    qty_to_sell = max(0.0, base_free - BASE_RESERVE)
                    if step: qty_to_sell = _round_floor(qty_to_sell, step)
                    qty_to_sell = _to_exchange_precision(ex, symbol, qty_to_sell)
                    if qty_to_sell < max(min_amount, 0.0):
                        return jsonify({"ok": False, "skipped":"insufficient-base",
                                        "base_free": base_free, "min_amount": min_amount}), 200
                    if DRY_RUN: order = {"dry_run":True, "side":"sell", "symbol":symbol, "qty":qty_to_sell}
                    else: order = ex.create_market_sell_order(symbol, qty_to_sell)
                    _with_state(lambda s: s.update({"has_position": False, "position_side":"none", "last_qty":0.0}))
                    return jsonify({"ok": True, "side":"sell-close-long", "symbol": symbol,
                                    "amount": qty_to_sell, "order": order, "reason": reason}), 200

                # 2) Sinon pas de BTC : ouvrir un short si autorisé
                if not ENABLE_SHORTING:
                    return jsonify({"ok": False, "skipped":"no_base_and_short_disabled"}), 200

                requested_quote = float(payload.get("quote") or FIXED_QUOTE_PER_TRADE)
                if requested_quote < MIN_QUOTE_PER_TRADE:
                    return jsonify({"error":"sizing_error",
                                    "detail": f"Montant trop faible: min {MIN_QUOTE_PER_TRADE} {QUOTE_SYMBOL}"}), 400

                # quantité à vendre (base) calibrée sur le "quote" et le levier
                base_qty, price = _compute_base_qty_for_quote(ex, symbol, requested_quote)
                # avec levier N, Kraken gère la marge; nous vendons "base_qty * leverage" ?
                # Par sécurité, on vend "base_qty" et on passe 'leverage' à l'API.
                _, _, step = _get_min_trade_info(ex, symbol, price)
                if step: base_qty = _round_floor(base_qty, step)
                base_qty = _to_exchange_precision(ex, symbol, base_qty)

                params = {"leverage": str(MARGIN_LEVERAGE)} if MARGIN_LEVERAGE else {}
                if DRY_RUN:
                    order = {"dry_run": True, "side":"sell", "symbol":symbol, "qty":base_qty, "leverage": MARGIN_LEVERAGE}
                else:
                    # create_order: type, side, amount, price=None, params={}
                    order = ex.create_order(symbol, "market", "sell", base_qty, None, params)

                _with_state(lambda s: s.update({
                    "has_position": True, "position_side":"short",
                    "last_entry_price": price, "last_qty": base_qty, "symbol": symbol
                }))
                return jsonify({"ok": True, "side":"sell-open-short", "symbol": symbol,
                                "amount": base_qty, "order": order, "leverage": MARGIN_LEVERAGE,
                                "reason": reason}), 200

            return jsonify({"error": f"unknown-signal:{signal}"}), 400

        except Exception as e:
            log.exception("webhook error")
            return jsonify({"error": str(e)}), 500

# ===== Boot =====
_load_state()

if __name__ == "__main__":
    port = int(os.getenv("PORT","10000"))
    app.run(host="0.0.0.0", port=port)

