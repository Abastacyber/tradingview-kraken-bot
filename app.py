# app.py
from __future__ import annotations

import os
import json
import time
import math
import logging
import threading
from typing import Any, Dict, Tuple, Optional

from flask import Flask, request, jsonify
import ccxt  # type: ignore

# =============================================================================
# Logging
# =============================================================================
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("tv-kraken")

# Empêcher deux ordres simultanés (free tier Render)
ORDER_LOCK = threading.Lock()

# =============================================================================
# Helpers ENV
# =============================================================================
def env_str(name: str, default: Optional[str] = None) -> str:
    v = os.getenv(name, default)
    if v is None:
        raise RuntimeError(f"Missing env var: {name}")
    return v

def env_int(name: str, default: int = 0) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default

def env_float(name: str, default: float = 0.0) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default

def env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "y", "on")

# =============================================================================
# Config
# =============================================================================
EXCHANGE_NAME             = env_str("EXCHANGE", "kraken").lower()

BASE_SYMBOL               = env_str("BASE_SYMBOL", "BTC").upper()
QUOTE_SYMBOL              = env_str("QUOTE_SYMBOL", "USDT").upper()
SYMBOL                    = env_str("SYMBOL", f"{BASE_SYMBOL}/{QUOTE_SYMBOL}").upper()

ORDER_TYPE                = env_str("ORDER_TYPE", "market").lower()

FIXED_QUOTE_PER_TRADE     = env_float("FIXED_QUOTE_PER_TRADE", 10.0)
MIN_QUOTE_PER_TRADE       = env_float("MIN_QUOTE_PER_TRADE", 10.0)   # garde-fou local
FEE_BUFFER_PCT            = env_float("FEE_BUFFER_PCT", 0.002)       # 0.2%
QUOTE_RESERVE             = env_float("QUOTE_RESERVE", 0.0)          # réserve en quote
BASE_RESERVE              = env_float("BASE_RESERVE", 0.0)           # réserve en base (ne pas tout vendre)

# gestion du risque (info, pas d’ordre auto de SL ici)
RISK_PCT                  = env_float("RISK_PCT", 0.02)              # 2% si FIXED <=0
MAX_SL_PCT                = env_float("MAX_SL_PCT", 0.05)

# cooldown achat
BUY_COOL_SEC              = env_int("BUY_COOL_SEC", 300)

# split d’ordres (facultatif)
BUY_SPLIT_CHUNKS          = max(1, env_int("BUY_SPLIT_CHUNKS", 1))
SELL_SPLIT_CHUNKS         = max(1, env_int("SELL_SPLIT_CHUNKS", 1))
BUY_SPLIT_DELAY_MS        = max(0, env_int("BUY_SPLIT_DELAY_MS", 300))
SELL_SPLIT_DELAY_MS       = max(0, env_int("SELL_SPLIT_DELAY_MS", 300))

# sandbox & état
DRY_RUN                   = env_bool("DRY_RUN", False)
RESTORE_ON_START          = env_bool("RESTORE_ON_START", True)
STATE_FILE                = env_str("STATE_FILE", "/tmp/bot_state.json")

# trailing (valeurs exposées, pas d’ordres auto)
TRAILING_ENABLED          = env_bool("TRAILING_ENABLED", True)
TRAIL_ACTIVATE_PCT_CONF2  = env_float("TRAIL_ACTIVATE_PCT_CONF2", 0.003)
TRAIL_ACTIVATE_PCT_CONF3  = env_float("TRAIL_ACTIVATE_PCT_CONF3", 0.005)
TRAIL_GAP_CONF2           = env_float("TRAIL_GAP_CONF2", 0.0004)
TRAIL_GAP_CONF3           = env_float("TRAIL_GAP_CONF3", 0.003)

# sécu API / webhook
WEBHOOK_SECRET            = env_str("WEBHOOK_SECRET", "")
KRAKEN_API_KEY            = os.getenv("KRAKEN_API_KEY", "")   # sans guillemets dans Render
KRAKEN_API_SECRET         = os.getenv("KRAKEN_API_SECRET", "")

# =============================================================================
# Safe state logger (espaces uniquement, pas de lambda)
# =============================================================================
_STATE_LAST_TS = 0.0
def safe_log_state(extra: Dict[str, Any] | None = None) -> None:
    global _STATE_LAST_TS
    try:
        now = time.time()
        if now - _STATE_LAST_TS < 30:
            return
        _STATE_LAST_TS = now
        snapshot: Dict[str, Any] = {
            "exchange": EXCHANGE_NAME,
            "symbol": SYMBOL,
            "order_type": ORDER_TYPE,
            "fixed_quote": FIXED_QUOTE_PER_TRADE,
            "min_quote": MIN_QUOTE_PER_TRADE,
            "risk_pct": RISK_PCT,
            "max_sl_pct": MAX_SL_PCT,
            "buy_cool_sec": BUY_COOL_SEC,
            "trailing": TRAILING_ENABLED,
            "dry_run": DRY_RUN,
            "split_buy": [BUY_SPLIT_CHUNKS, BUY_SPLIT_DELAY_MS],
            "split_sell": [SELL_SPLIT_CHUNKS, SELL_SPLIT_DELAY_MS],
        }
        if extra:
            snapshot.update(extra)
        logger.debug("state=%s", json.dumps(snapshot, ensure_ascii=False))
    except Exception as e:
        logger.debug("state_log_error=%s", e)

# =============================================================================
# State persistence
# =============================================================================
_DEFAULT_STATE: Dict[str, Any] = {"last_buy_ts": 0.0, "last_signal": None}

def load_state() -> Dict[str, Any]:
    if RESTORE_ON_START and os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                s = json.load(f)
            if isinstance(s, dict):
                s = {**_DEFAULT_STATE, **s}
                logger.info("State restauré depuis %s", STATE_FILE)
                return s
        except Exception as e:
            logger.warning("Impossible de charger le state (%s), on repart clean.", e)
    return dict(_DEFAULT_STATE)

def save_state(state: Dict[str, Any]) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception as e:
        logger.warning("Impossible d'écrire le state: %s", e)

STATE = load_state()

# =============================================================================
# Exchange init
# =============================================================================
if EXCHANGE_NAME != "kraken":
    raise RuntimeError("Ce bot est prévu pour Kraken (spot) dans cette version.")

kraken_kwargs = {
    "apiKey": KRAKEN_API_KEY or "",
    "secret": KRAKEN_API_SECRET or "",
    "enableRateLimit": True,
    "options": {"adjustForTimeDifference": True},
}
exchange = ccxt.kraken(kraken_kwargs)

exchange.load_markets()
if SYMBOL not in exchange.markets:
    raise RuntimeError(f"Symbole inconnu sur Kraken : {SYMBOL}")

market = exchange.market(SYMBOL)
base_id = market.get("base", BASE_SYMBOL)
# quote_id = market.get("quote", QUOTE_SYMBOL)  # non utilisé directement

safe_log_state({"stage": "exchange_init"})

# =============================================================================
# Utils sizing / balances / arrondis
# =============================================================================
def _avail_balances() -> Tuple[float, float]:
    bal = exchange.fetch_balance()
    base_free = float(bal.get(base_id, {}).get("free", 0.0))
    quote_free = float(bal.get(QUOTE_SYMBOL, {}).get("free", 0.0))  # ccxt normalise
    return base_free, quote_free

def _min_notional_from_market() -> float:
    limits = market.get("limits") or {}
    cost = limits.get("cost") or {}
    min_cost = cost.get("min")
    return float(min_cost) if min_cost is not None else 0.0

def _amount_precision() -> Optional[int]:
    p = (market.get("precision", {}) or {}).get("amount")
    return int(p) if p is not None else None

def _price_precision() -> Optional[int]:
    p = (market.get("precision", {}) or {}).get("price")
    return int(p) if p is not None else None

def _round_amount(amount: float) -> float:
    p = _amount_precision()
    if p is None:
        return amount
    step = 10 ** (-p)
    return math.floor(amount / step) * step

def _round_price(price: float) -> float:
    p = _price_precision()
    if p is None:
        return price
    step = 10 ** (-p)
    return math.floor(price / step) * step

def _compute_buy_amount(price: float) -> Tuple[float, float]:
    _, quote_free = _avail_balances()
    max_spend = max(0.0, quote_free - QUOTE_RESERVE)
    max_spend *= (1.0 - FEE_BUFFER_PCT)

    spend = FIXED_QUOTE_PER_TRADE if FIXED_QUOTE_PER_TRADE > 0 else max_spend * RISK_PCT
    spend = min(spend, max_spend)
    spend = max(spend, MIN_QUOTE_PER_TRADE)

    min_cost = _min_notional_from_market()
    if min_cost > 0:
        spend = max(spend, min_cost)

    if spend <= 0:
        return 0.0, 0.0

    base_amt = spend / max(price, 1e-9)
    base_amt = _round_amount(base_amt)

    amt_limits = (market.get("limits") or {}).get("amount") or {}
    min_amt = amt_limits.get("min")
    if min_amt is not None:
        base_amt = max(base_amt, float(min_amt))

    return spend, base_amt

def _enough_to_sell(min_amount: float = 0.0) -> bool:
    base_free, _ = _avail_balances()
    if min_amount <= 0:
        min_amount = max(BASE_RESERVE, 10 ** (-( _amount_precision() or 6 )))
    return base_free > min_amount

# =============================================================================
# Orders (avec split optionnel)
# =============================================================================
def _place_order(side: str, amount: float) -> Dict[str, Any]:
    if DRY_RUN:
        logger.info("DRY_RUN %s %s %s", side.upper(), amount, SYMBOL)
        return {"dry_run": True, "side": side, "amount": amount, "symbol": SYMBOL}

    if amount <= 0:
        raise RuntimeError("Amount <= 0")

    if ORDER_TYPE != "market":
        raise RuntimeError("Cette version ne gère que l'ordre 'market'")

    if side.lower() == "buy":
        return exchange.create_market_buy_order(SYMBOL, amount)  # qty en BASE
    elif side.lower() == "sell":
        return exchange.create_market_sell_order(SYMBOL, amount)
    else:
        raise RuntimeError("Side inconnu")

def _place_order_split(side: str, total_amount: float, chunks: int, delay_ms: int) -> Dict[str, Any]:
    if chunks <= 1:
        return _place_order(side, total_amount)

    each = _round_amount(total_amount / chunks)
    if each <= 0:
        return _place_order(side, total_amount)

    results = []
    remaining = total_amount
    for i in range(chunks):
        amt = each if i < (chunks - 1) else _round_amount(remaining)
        if amt <= 0:
            break
        res = _place_order(side, amt)
        results.append(res)
        remaining = max(0.0, remaining - amt)
        if i < chunks - 1 and delay_ms > 0:
            time.sleep(delay_ms / 1000.0)

    return {"chunks": chunks, "side": side, "symbol": SYMBOL, "last": results[-1] if results else {}}

# =============================================================================
# Flask
# =============================================================================
app = Flask(__name__)

@app.get("/")
def index() -> Any:
    return jsonify({
        "service": "tv-kraken webhook",
        "health": "/health",
        "webhook": "/webhook (POST)",
        "symbol": SYMBOL,
        "exchange": EXCHANGE_NAME,
        "howto": {
            "webhook_url_example": "/webhook?secret=***",
            "json_example": {"signal": "BUY", "secret": "***"}
        },
    }), 200

@app.get("/health")
def health() -> Any:
    return {"ok": True, "symbol": SYMBOL, "exchange": EXCHANGE_NAME}, 200

def _extract_secret(req) -> str:
    body = {}
    try:
        body = req.get_json(silent=True) or {}
    except Exception:
        body = {}
    return (
        req.headers.get("X-Webhook-Secret")
        or req.args.get("secret")
        or str(body.get("secret") or "")
    )

def _normalize_signal(sig: str) -> str:
    s = sig.strip().upper()
    if s == "LONG":
        s = "BUY"
    if s in ("SHORT", "CLOSE"):
        s = "SELL"
    return s

def _current_price(hint: float, side: str) -> float:
    last_price = 0.0
    try:
        ticker = exchange.fetch_ticker(SYMBOL)
        last_price = float(ticker.get("last") or ticker.get("close") or 0.0)
    except Exception:
        last_price = 0.0

    if last_price <= 0:
        last_price = float(hint or 0.0)

    if last_price <= 0:
        try:
            ob = exchange.fetch_order_book(SYMBOL, limit=5)
            if side == "BUY" and ob.get("asks"):
                last_price = float(ob["asks"][0][0])
            elif side == "SELL" and ob.get("bids"):
                last_price = float(ob["bids"][0][0])
        except Exception:
            pass

    return _round_price(last_price) if last_price > 0 else 0.0

@app.post("/webhook")
def webhook() -> Any:
    given = _extract_secret(request)
    if WEBHOOK_SECRET and given != WEBHOOK_SECRET:
        logger.warning("Secret KO: Webhook secret invalide")
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    try:
        payload = request.get_json(force=True, silent=True) or {}
    except Exception:
        payload = {}
    logger.info("tv-kraken:Webhook payload: %s", json.dumps(payload, ensure_ascii=False))
    safe_log_state({"stage": "webhook_received"})

    signal_in = str(payload.get("signal") or payload.get("type") or "").strip()
    if not signal_in:
        return jsonify({"ok": False, "error": "missing_signal"}), 400
    signal = _normalize_signal(signal_in)

    force_close = bool(payload.get("force_close") or payload.get("force") or False)
    price_hint = float(payload.get("price") or 0.0)

    if signal not in ("BUY", "SELL"):
        return jsonify({"ok": False, "error": "signal_inconnu"}), 400

    if not ORDER_LOCK.acquire(blocking=False):
        return jsonify({"ok": False, "skipped": "busy"}), 200

    try:
        now = time.time()
        if signal == "BUY" and not force_close:
            last_buy = float(STATE.get("last_buy_ts", 0.0))
            if BUY_COOL_SEC > 0 and (now - last_buy) < BUY_COOL_SEC:
                wait = int(BUY_COOL_SEC - (now - last_buy))
                logger.info("Cooldown achat actif (%ss restants)", wait)
                return jsonify({"ok": True, "skipped": "cooldown_buy", "wait_sec": wait}), 200

        last_price = _current_price(price_hint, signal)
        if last_price <= 0:
            return jsonify({"ok": False, "error": "price_unavailable"}), 200

        if signal == "BUY":
            _, quote_free = _avail_balances()
            if quote_free <= max(0.0, QUOTE_RESERVE):
                logger.info("Pas de quote dispo (%.2f %s)", quote_free, QUOTE_SYMBOL)
                return jsonify({"ok": False, "error": "quote_insufficient"}), 200

            spend, base_amt = _compute_buy_amount(last_price)
            if spend <= 0 or base_amt <= 0:
                logger.info("Sizing nul (spend=%.2f, amount=%.8f)", spend, base_amt)
                return jsonify({"ok": False, "error": "sizing_zero"}), 200

            logger.info(
                "BUY %s | spend≈%.2f %s => amount≈%.8f %s @ %.8f",
                SYMBOL, spend, QUOTE_SYMBOL, base_amt, BASE_SYMBOL, last_price
            )
            res = _place_order_split("buy", base_amt, BUY_SPLIT_CHUNKS, BUY_SPLIT_DELAY_MS)

            STATE["last_buy_ts"] = now
            STATE["last_signal"] = "BUY"
            save_state(STATE)

            return jsonify({"ok": True, "side": "BUY", "amount": base_amt, "order": res}), 200

        else:  # SELL
            base_free, _ = _avail_balances()
            sellable = max(0.0, base_free - BASE_RESERVE)
            sellable = _round_amount(sellable)
            if sellable <= 0 or not _enough_to_sell(sellable * 0.2):
                logger.info("Aucune quantité %s disponible pour SELL", BASE_SYMBOL)
                return jsonify({"ok": False, "error": "no_base_available"}), 200

            logger.info("SELL %s | amount≈%.8f %s", SYMBOL, sellable, BASE_SYMBOL)
            res = _place_order_split("sell", sellable, SELL_SPLIT_CHUNKS, SELL_SPLIT_DELAY_MS)

            STATE["last_signal"] = "SELL"
            save_state(STATE)

            return jsonify({"ok": True, "side": "SELL", "amount": sellable, "order": res}), 200

    except ccxt.BaseError as ex:
        logger.exception("Erreur ccxt: %s", ex)
        return jsonify({"ok": False, "error": f"ccxt:{type(ex).__name__}"}), 200
    except Exception as ex:
        logger.exception("Erreur interne: %s", ex)
        return jsonify({"ok": False, "error": "internal_error"}), 500
    finally:
        try:
            ORDER_LOCK.release()
        except Exception:
            pass

# =============================================================================
# Endpoints utilitaires (debug)
# =============================================================================
@app.get("/balance")
def balance() -> Any:
    try:
        base_free, quote_free = _avail_balances()
        return {
            "ok": True,
            "symbol": SYMBOL,
            "base": {"asset": BASE_SYMBOL, "free": base_free},
            "quote": {"asset": QUOTE_SYMBOL, "free": quote_free},
        }, 200
    except Exception as e:
        return {"ok": False, "error": str(e)}, 500

@app.get("/ticker")
def ticker() -> Any:
    try:
        t = exchange.fetch_ticker(SYMBOL)
        return {"ok": True, "ticker": t}, 200
    except Exception as e:
        return {"ok": False, "error": str(e)}, 500

@app.get("/config")
def config_view() -> Any:
    cfg = {
        "EXCHANGE": EXCHANGE_NAME,
        "SYMBOL":           SYMBOL,
        "ORDER_TYPE":       ORDER_TYPE,
        "FIXED_QUOTE_PER_TRADE": FIXED_QUOTE_PER_TRADE,
        "MIN_QUOTE_PER_TRADE":   MIN_QUOTE_PER_TRADE,
        "FEE_BUFFER_PCT":   FEE_BUFFER_PCT,
        "QUOTE_RESERVE":    QUOTE_RESERVE,
        "BASE_RESERVE":     BASE_RESERVE,
        "RISK_PCT":         RISK_PCT,
        "MAX_SL_PCT":       MAX_SL_PCT,
        "BUY_COOL_SEC":     BUY_COOL_SEC,
        "BUY_SPLIT":        [BUY_SPLIT_CHUNKS, BUY_SPLIT_DELAY_MS],
        "SELL_SPLIT":       [SELL_SPLIT_CHUNKS, SELL_SPLIT_DELAY_MS],
        "TRAILING_ENABLED": TRAILING_ENABLED,
        "TRAIL_CONF2":      [TRAIL_ACTIVATE_PCT_CONF2, TRAIL_GAP_CONF2],
        "TRAIL_CONF3":      [TRAIL_ACTIVATE_PCT_CONF3, TRAIL_GAP_CONF3],
        "DRY_RUN":          DRY_RUN,
        "RESTORE_ON_START": RESTORE_ON_START,
        "STATE_FILE":       STATE_FILE,
        "WEBHOOK_SECRET_SET": bool(WEBHOOK_SECRET),
    }
    safe_log_state({"stage": "config_view"})
    return {"ok": True, "config": cfg}, 200

# =============================================================================
# Gunicorn / Dev
# =============================================================================
if __name__ == "__main__":
    # Dev only (sur Render : gunicorn app:app)
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")), debug=False)
