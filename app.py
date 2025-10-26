# app.py
from __future__ import annotations

import os
import json
import time
import math
import logging
from typing import Any, Dict, Tuple, Optional, Callable

from flask import Flask, request, jsonify
import ccxt  # type: ignore

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("tv-kraken")

# -----------------------------------------------------------------------------
# Helpers ENV
# -----------------------------------------------------------------------------
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

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
EXCHANGE_NAME         = env_str("EXCHANGE",        "kraken").lower()
BASE_SYMBOL           = env_str("BASE_SYMBOL",     "BTC").upper()
QUOTE_SYMBOL          = env_str("QUOTE_SYMBOL",    "USDT").upper()
SYMBOL                = env_str("SYMBOL",          f"{BASE_SYMBOL}/{QUOTE_SYMBOL}").upper()
ORDER_TYPE            = env_str("ORDER_TYPE",      "market").lower()

FIXED_QUOTE_PER_TRADE = env_float("FIXED_QUOTE_PER_TRADE", 10.0)
MIN_QUOTE_PER_TRADE   = env_float("MIN_QUOTE_PER_TRADE",   10.0)  # garde-fou local
FEE_BUFFER_PCT        = env_float("FEE_BUFFER_PCT",        0.002) # 0.2%
QUOTE_RESERVE         = env_float("QUOTE_RESERVE",         0.0)

# gestion risque
RISK_PCT              = env_float("RISK_PCT",      0.02)  # 2%
MAX_SL_PCT            = env_float("MAX_SL_PCT",    0.05)  # 5%

# cooldown achat
BUY_COOL_SEC          = env_int("BUY_COOL_SEC",    300)

# sandbox & état
DRY_RUN               = env_bool("DRY_RUN",        False)
RESTORE_ON_START      = env_bool("RESTORE_ON_START", True)
STATE_FILE            = env_str("STATE_FILE",      "/tmp/bot_state.json")

# trailing (non bloquant : pas d’ordres auto ici, mais valeurs disponibles)
TRAILING_ENABLED      = env_bool("TRAILING_ENABLED", True)
TRAIL_ACTIVATE_PCT_CONF2 = env_float("TRAIL_ACTIVATE_PCT_CONF2", 0.003)
TRAIL_ACTIVATE_PCT_CONF3 = env_float("TRAIL_ACTIVATE_PCT_CONF3", 0.005)
TRAIL_GAP_CONF2          = env_float("TRAIL_GAP_CONF2",       0.0004)
TRAIL_GAP_CONF3          = env_float("TRAIL_GAP_CONF3",       0.003)

# sécu API / webhook
WEBHOOK_SECRET        = env_str("WEBHOOK_SECRET",  "")
KRAKEN_API_KEY        = os.getenv("KRAKEN_API_KEY", "")
KRAKEN_API_SECRET     = os.getenv("KRAKEN_API_SECRET", "")

# Kraken: symboles fiat courants pour lecture de balance
_KRAKEN_FIAT_MAP = {
    "EUR": "ZEUR",
    "USD": "ZUSD",
    "GBP": "ZGBP",
    "JPY": "ZJPY",
}

# -----------------------------------------------------------------------------
# Safe state logger (aucun lambda, aucune parenthèse piégeuse)
# -----------------------------------------------------------------------------
import json as _json
_STATE_LAST = 0.0

def safe_log_state(extra: dict | None = None) -> None:
    """Log un petit snapshot de l'état/config au plus toutes les 30s."""
    global _STATE_LAST
    try:
        now = time.time()
        if now - _STATE_LAST < 30:
            return
        _STATE_LAST = now
        snapshot = {
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
        }
        if extra:
            snapshot.update(extra)
        logger.debug("state=%s", _json.dumps(snapshot))
    except Exception as e:
        logger.debug("state_log_error=%s", e)

# -----------------------------------------------------------------------------
# State persistence
# -----------------------------------------------------------------------------
_DEFAULT_STATE: Dict[str, Any] = {
    "last_buy_ts": 0.0,
    "last_signal": None,
}

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

# -----------------------------------------------------------------------------
# Exchange init
# -----------------------------------------------------------------------------
if EXCHANGE_NAME != "kraken":
    raise RuntimeError("Ce bot est prévu pour Kraken (spot) dans cette version.")

kraken_kwargs = {
    "apiKey": KRAKEN_API_KEY or "",
    "secret": KRAKEN_API_SECRET or "",
    "enableRateLimit": True,
    "options": {"adjustForTimeDifference": True},
}
exchange = ccxt.kraken(kraken_kwargs)

# Précharge les marchés (pour precision/limits)
exchange.load_markets()
if SYMBOL not in exchange.markets:
    raise RuntimeError(f"Symbole inconnu sur Kraken : {SYMBOL}")

market = exchange.market(SYMBOL)
base_id = market.get("base", BASE_SYMBOL)
quote_id = market.get("quote", QUOTE_SYMBOL)

safe_log_state({"stage": "exchange_init"})

# -----------------------------------------------------------------------------
# Utils sizing / balances
# -----------------------------------------------------------------------------
def _quote_ccxt(code: str) -> str:
    """Kraken a des codes fiat 'Z***' en raw, mais ccxt normalise.
       Pour la lecture 'free' via ccxt, on utilise le ticker normalisé (EUR, USD...).
    """
    return code.upper()

def _avail_balances() -> Tuple[float, float]:
    """retourne (base_free, quote_free) en unités normalisées (ex: SOL, EUR)"""
    bal = exchange.fetch_balance()
    base_free = float(bal.get(base_id, {}).get("free", 0.0))
    quote_free = float(bal.get(_quote_ccxt(QUOTE_SYMBOL), {}).get("free", 0.0))
    return base_free, quote_free

def _min_notional_from_market() -> float:
    """Essaye de déterminer un minimum de ticket via les 'limits' du marché."""
    limits = market.get("limits") or {}
    cost = limits.get("cost") or {}
    min_cost = cost.get("min")
    return float(min_cost) if min_cost is not None else 0.0

def _round_amount(amount: float) -> float:
    """Respecte la précision de lot du marché."""
    precision = market.get("precision", {})
    p_amount = precision.get("amount")
    if p_amount is None:
        return amount
    step = 10 ** (-p_amount)
    return math.floor(amount / step) * step

def _compute_buy_amount(price: float) -> Tuple[float, float]:
    """Calcule (quote_to_spend, base_amount)."""
    base_free, quote_free = _avail_balances()

    # Réserve + buffer
    max_spend = max(0.0, quote_free - QUOTE_RESERVE)
    max_spend *= (1.0 - FEE_BUFFER_PCT)

    # Montant voulu
    spend = FIXED_QUOTE_PER_TRADE if FIXED_QUOTE_PER_TRADE > 0 else max_spend * RISK_PCT

    # garde-fous locaux
    spend = max(spend, 0.0)
    spend = min(spend, max_spend)
    spend = max(spend, MIN_QUOTE_PER_TRADE)

    # garde-fou marché (si on connaît un min cost)
    min_cost = _min_notional_from_market()
    if min_cost > 0:
        spend = max(spend, min_cost)

    if spend <= 0:
        return 0.0, 0.0

    base_amt = spend / max(price, 1e-9)
    base_amt = _round_amount(base_amt)
    return spend, base_amt

def _enough_to_sell(min_amount: float = 0.0) -> bool:
    base_free, _ = _avail_balances()
    if min_amount <= 0:
        # par défaut, un très petit seuil > 0
        min_amount = 10 ** (-(market.get("precision", {}).get("amount", 6)))
    return base_free >= min_amount

# -----------------------------------------------------------------------------
# Orders
# -----------------------------------------------------------------------------
def _place_order(side: str, amount: float) -> Dict[str, Any]:
    if DRY_RUN:
        logger.info("DRY_RUN %s %s %s", side.upper(), amount, SYMBOL)
        return {"dry_run": True, "side": side, "amount": amount, "symbol": SYMBOL}

    if amount <= 0:
        raise RuntimeError("Amount <= 0")

    if ORDER_TYPE != "market":
        raise RuntimeError("Cette version ne gère que l'ordre 'market'")

    if side.lower() == "buy":
        return exchange.create_market_buy_order(SYMBOL, amount)  # amount = BASE qty
    elif side.lower() == "sell":
        return exchange.create_market_sell_order(SYMBOL, amount)
    else:
        raise RuntimeError("Side inconnu")

# -----------------------------------------------------------------------------
# Flask
# -----------------------------------------------------------------------------
app = Flask(__name__)

@app.get("/health")
def health() -> Any:
    return {"ok": True, "symbol": SYMBOL, "exchange": EXCHANGE_NAME}, 200

def _check_secret(req) -> None:
    given = req.headers.get("X-Webhook-Secret") or req.args.get("secret") or ""
    if WEBHOOK_SECRET and given != WEBHOOK_SECRET:
        raise RuntimeError("Webhook secret invalide")

@app.post("/webhook")
def webhook() -> Any:
    try:
        _check_secret(request)
    except Exception as e:
        logger.warning("Secret KO: %s", e)
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    try:
        payload = request.get_json(force=True, silent=False)
    except Exception:
        payload = {}
    payload = payload or {}

    logger.info("tv-kraken:Webhook payload: %s", json.dumps(payload, ensure_ascii=False))
    safe_log_state({"stage": "webhook_received"})

    signal = str(payload.get("signal", payload.get("type", ""))).strip().upper()
    force_close = bool(payload.get("force_close") or payload.get("force", False))
    price_hint = float(payload.get("price") or 0.0)

    # Prix courant (si hint absent)
    try:
        ticker = exchange.fetch_ticker(SYMBOL)
        last_price = float(ticker.get("last") or ticker.get("close") or price_hint or 0.0)
    except Exception:
        last_price = price_hint or 0.0

    if not last_price or last_price <= 0:
        # second essai via orderbook
        try:
            ob = exchange.fetch_order_book(SYMBOL, limit=5)
            if signal == "BUY":
                last_price = float(ob["asks"][0][0])
            else:
                last_price = float(ob["bids"][0][0])
        except Exception:
            pass

    if signal not in ("BUY", "SELL", "LONG", "SHORT", "CLOSE"):
        return jsonify({"ok": False, "error": "signal inconnu"}), 400

    # Normalisation
    if signal == "LONG":
        signal = "BUY"
    if signal in ("SHORT", "CLOSE"):
        signal = "SELL"

    # Cooldown achat (sur signaux BUY uniquement, sauf force_close)
    now = time.time()
    if signal == "BUY" and not force_close:
        last_buy = float(STATE.get("last_buy_ts", 0.0))
        if BUY_COOL_SEC > 0 and (now - last_buy) < BUY_COOL_SEC:
            wait = int(BUY_COOL_SEC - (now - last_buy))
            logger.info("Cooldown achat actif (%ss restants)", wait)
            return jsonify({"ok": True, "skipped": "cooldown_buy", "wait_sec": wait}), 200

    # Branches
    try:
        if signal == "BUY":
            _, quote_free = _avail_balances()
            if quote_free <= max(QUOTE_RESERVE, 0.0):
                logger.info("Pas de quote dispo (%.2f %s)", quote_free, QUOTE_SYMBOL)
                return jsonify({"ok": False, "error": "quote_insufficient"}), 200

            if last_price <= 0:
                return jsonify({"ok": False, "error": "price_unavailable"}), 200

            spend, base_amt = _compute_buy_amount(last_price)
            if spend <= 0 or base_amt <= 0:
                logger.info("Sizing nul (spend=%.2f, amount=%.8f)", spend, base_amt)
                return jsonify({"ok": False, "error": "sizing_zero"}), 200

            # Place order
            logger.info("BUY %s | spend≈%.2f %s => amount≈%.8f %s @ %.4f",
                        SYMBOL, spend, QUOTE_SYMBOL, base_amt, BASE_SYMBOL, last_price)
            order = _place_order("buy", base_amt)

            # maj state
            STATE["last_buy_ts"] = now
            STATE["last_signal"] = "BUY"
            save_state(STATE)

            return jsonify({"ok": True, "side": "BUY", "amount": base_amt, "order": order}), 200

        elif signal == "SELL":
            # quantité dispo ?
            if not _enough_to_sell():
                logger.info("tv-kraken:Aucune quantité %s disponible pour SELL", BASE_SYMBOL)
                return jsonify({"ok": False, "error": "no_base_available"}), 200

            base_free, _ = _avail_balances()
            # On vend tout le 'free' (simple)
            amount = _round_amount(base_free)
            if amount <= 0:
                return jsonify({"ok": False, "error": "amount_zero"}), 200

            logger.info("SELL %s | amount≈%.8f %s", SYMBOL, amount, BASE_SYMBOL)
            order = _place_order("sell", amount)

            STATE["last_signal"] = "SELL"
            save_state(STATE)

            return jsonify({"ok": True, "side": "SELL", "amount": amount, "order": order}), 200

        # jamais atteint (signaux normalisés)
        return jsonify({"ok": False, "error": "unreachable"}), 400

    except ccxt.BaseError as ex:
        logger.exception("Erreur ccxt: %s", ex)
        return jsonify({"ok": False, "error": f"ccxt:{type(ex).__name__}"}), 200
    except Exception as ex:
        logger.exception("Erreur interne: %s", ex)
        return jsonify({"ok": False, "error": "internal_error"}), 500


# -----------------------------------------------------------------------------
# Gunicorn entrypoint
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    # Dev only
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")), debug=False)
