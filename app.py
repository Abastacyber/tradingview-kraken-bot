# app.py
from __future__ import annotations

import os
import json
import time
import math
import logging
from typing import Any, Dict, Tuple, Optional

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
EXCHANGE_NAME = env_str("EXCHANGE", "kraken").lower()
BASE_SYMBOL   = env_str("BASE_SYMBOL", "BTC").upper()
QUOTE_SYMBOL  = env_str("QUOTE_SYMBOL", "USDT").upper()
SYMBOL        = env_str("SYMBOL", f"{BASE_SYMBOL}/{QUOTE_SYMBOL}").upper()
ORDER_TYPE    = env_str("ORDER_TYPE", "market").lower()

FIXED_QUOTE_PER_TRADE = env_float("FIXED_QUOTE_PER_TRADE", 0.0)
MIN_QUOTE_PER_TRADE   = env_float("MIN_QUOTE_PER_TRADE", 10.0)      # garde-fou local
FEE_BUFFER_PCT        = env_float("FEE_BUFFER_PCT", 0.002)          # 0.2%
QUOTE_RESERVE         = env_float("QUOTE_RESERVE", 0.0)             # € à ne jamais dépenser
BASE_RESERVE          = env_float("BASE_RESERVE", 0.0)              # quantité base à conserver

# gestion risque
RISK_PCT   = env_float("RISK_PCT",   0.02)  # 2%
MAX_SL_PCT = env_float("MAX_SL_PCT", 0.05)  # non utilisé ici, dispo pour évolutions

# cooldown achat
BUY_COOL_SEC = env_int("BUY_COOL_SEC", 300)

# split (facultatif)
BUY_SPLIT_CHUNKS     = max(1, env_int("BUY_SPLIT_CHUNKS", 1))
BUY_SPLIT_DELAY_MS   = max(0, env_int("BUY_SPLIT_DELAY_MS", 300))
SELL_SPLIT_CHUNKS    = max(1, env_int("SELL_SPLIT_CHUNKS", 1))

# sandbox & état
DRY_RUN          = env_bool("DRY_RUN", False)
RESTORE_ON_START = env_bool("RESTORE_ON_START", True)
STATE_FILE       = env_str("STATE_FILE", "/tmp/bot_state.json")

# trailing (non bloquant)
TRAILING_ENABLED        = env_bool("TRAILING_ENABLED", True)
TRAIL_ACTIVATE_PCT_CONF2 = env_float("TRAIL_ACTIVATE_PCT_CONF2", 0.003)
TRAIL_ACTIVATE_PCT_CONF3 = env_float("TRAIL_ACTIVATE_PCT_CONF3", 0.005)
TRAIL_GAP_CONF2          = env_float("TRAIL_GAP_CONF2", 0.0004)
TRAIL_GAP_CONF3          = env_float("TRAIL_GAP_CONF3", 0.003)

# sécu API / webhook
WEBHOOK_SECRET    = env_str("WEBHOOK_SECRET", "")
KRAKEN_API_KEY    = os.getenv("KRAKEN_API_KEY", "")
KRAKEN_API_SECRET = os.getenv("KRAKEN_API_SECRET", "")

# Kraken: mapping fiat “Z***” (équivalent ccxt normalisé)
_KRAKEN_FIAT_MAP = {
    "EUR": "ZEUR",
    "USD": "ZUSD",
    "GBP": "ZGBP",
    "JPY": "ZJPY",
}


# -----------------------------------------------------------------------------
# Safe state logger
# -----------------------------------------------------------------------------
_STATE_LAST = 0.0

def safe_log_state(extra: Dict[str, Any] | None = None) -> None:
    """Log un mini snapshot d'état/config au plus toutes les 30s."""
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
        logger.debug("state=%s", json.dumps(snapshot))
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
    raise RuntimeError("Cette version cible Kraken (spot).")

kraken_kwargs = {
    "apiKey": KRAKEN_API_KEY or "",
    "secret": KRAKEN_API_SECRET or "",
    "enableRateLimit": True,
    "options": {"adjustForTimeDifference": True},
}
exchange = ccxt.kraken(kraken_kwargs)

# Précharge les marchés
exchange.load_markets()
if SYMBOL not in exchange.markets:
    raise RuntimeError(f"Symbole inconnu sur Kraken : {SYMBOL}")

market = exchange.market(SYMBOL)
base_id  = market.get("base", BASE_SYMBOL)     # ex: 'SOL'
quote_id = market.get("quote", QUOTE_SYMBOL)   # ex: 'EUR'

safe_log_state({"stage": "exchange_init"})


# -----------------------------------------------------------------------------
# Utils
# -----------------------------------------------------------------------------
def _quote_ccxt(code: str) -> str:
    """Kraken a des codes fiat 'Z***' côté API; ccxt normalise.
    On passe par le ticker normalisé (EUR, USD...)."""
    return code.upper()


def _fetch_balance_spot() -> Dict[str, Any]:
    try:
        return exchange.fetch_balance({"type": "spot"})
    except Exception:
        return exchange.fetch_balance()


def _avail_balances() -> Tuple[float, float]:
    """Retourne (base_free, quote_free) en unités normalisées (ex: SOL, EUR).
       Gère variantes Kraken: SOLF / ZEUR / XETH, etc.
    """
    bal = _fetch_balance_spot()
    free = bal.get("free", {}) or {}

    def _pick_free(symbol_code: str) -> float:
        # 1) clé standard dans free
        v = free.get(symbol_code)
        if v is not None:
            return float(v)
        # 2) entrée 'bal[symbol_code]["free"]'
        v = (bal.get(symbol_code) or {}).get("free")
        if v is not None:
            return float(v)
        # 3) variantes Kraken (funding 'F' / préfixes Z/X)
        candidates = [symbol_code + "F", "Z" + symbol_code, "X" + symbol_code]
        for alt in candidates:
            v = free.get(alt)
            if v is not None:
                return float(v)
            v = (bal.get(alt) or {}).get("free")
            if v is not None:
                return float(v)
        # 4) fiat map pour EUR, USD, etc.
        mapped = _KRAKEN_FIAT_MAP.get(symbol_code)
        if mapped:
            v = free.get(mapped) or (bal.get(mapped) or {}).get("free")
            if v is not None:
                return float(v)
        return 0.0

    base_free = _pick_free(base_id)
    quote_free = _pick_free(_quote_ccxt(QUOTE_SYMBOL))
    return base_free, quote_free


def _min_notional_from_market() -> float:
    limits = market.get("limits") or {}
    cost = limits.get("cost") or {}
    min_cost = cost.get("min")
    return float(min_cost) if min_cost is not None else 0.0


def _round_amount(amount: float) -> float:
    """Respecte la précision de lot du marché (nombre de décimales ccxt)."""
    p_amount = (market.get("precision") or {}).get("amount")
    if p_amount is None:
        return float(amount)
    factor = 10 ** int(p_amount)
    return math.floor(float(amount) * factor) / factor


def _current_price(signal: str, price_hint: float = 0.0) -> float:
    """Prix last/close, sinon meilleur ask/bid."""
    last = 0.0
    try:
        t = exchange.fetch_ticker(SYMBOL)
        last = float(t.get("last") or t.get("close") or 0.0)
    except Exception:
        last = 0.0

    if not last and price_hint:
        last = float(price_hint)

    if last <= 0:
        try:
            ob = exchange.fetch_order_book(SYMBOL, limit=5)
            if signal == "BUY":
                last = float(ob["asks"][0][0])
            else:
                last = float(ob["bids"][0][0])
        except Exception:
            pass
    return float(last)


def _compute_buy_amount(price: float, quote_override: float | None = None) -> Tuple[float, float]:
    """Calcule (quote_to_spend, base_amount arrondi)."""
    base_free, quote_free = _avail_balances()

    # Réserve + buffer
    max_spend = max(0.0, quote_free - QUOTE_RESERVE)
    max_spend *= (1.0 - FEE_BUFFER_PCT)

    # Montant voulu
    if quote_override is not None and quote_override > 0:
        spend = min(max_spend, float(quote_override))
    elif FIXED_QUOTE_PER_TRADE > 0:
        spend = min(max_spend, FIXED_QUOTE_PER_TRADE)
    else:
        spend = min(max_spend, quote_free * RISK_PCT)

    # garde-fous locaux
    spend = max(spend, 0.0)
    spend = max(spend, MIN_QUOTE_PER_TRADE)

    # garde-fou marché
    min_cost = _min_notional_from_market()
    if min_cost > 0:
        spend = max(spend, min_cost)

    if spend <= 0 or price <= 0:
        return 0.0, 0.0

    base_amt = spend / price
    base_amt = max(0.0, base_amt - BASE_RESERVE)  # au cas où
    base_amt = _round_amount(base_amt)
    return spend, base_amt


def _enough_to_sell(min_amount: float = 0.0) -> Tuple[bool, float]:
    base_free, _ = _avail_balances()
    # Par défaut, seuil min = 10^-precision
    if min_amount <= 0:
        prec = (market.get("precision") or {}).get("amount", 6)
        min_amount = 10 ** (-int(prec))
    sellable = max(0.0, base_free - BASE_RESERVE)
    return (sellable >= min_amount), _round_amount(sellable)


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


@app.get("/")
def root() -> Any:
    return jsonify({
        "ok": True,
        "msg": "TV-Kraken bot up",
        "symbol": SYMBOL,
        "exchange": EXCHANGE_NAME,
        "endpoints": ["/health", "/config", "/balance", "/webhook (POST)"],
    }), 200


@app.get("/health")
def health() -> Any:
    return {"ok": True, "symbol": SYMBOL, "exchange": EXCHANGE_NAME}, 200


@app.get("/config")
def config_view() -> Any:
    cfg = {
        "symbol": SYMBOL,
        "base_symbol": BASE_SYMBOL,
        "quote_symbol": QUOTE_SYMBOL,
        "order_type": ORDER_TYPE,
        "fixed_quote": FIXED_QUOTE_PER_TRADE,
        "min_quote": MIN_QUOTE_PER_TRADE,
        "fee_buffer_pct": FEE_BUFFER_PCT,
        "quote_reserve": QUOTE_RESERVE,
        "base_reserve": BASE_RESERVE,
        "risk_pct": RISK_PCT,
        "buy_cool_sec": BUY_COOL_SEC,
        "dry_run": DRY_RUN,
        "trailing_enabled": TRAILING_ENABLED,
        "buy_split_chunks": BUY_SPLIT_CHUNKS,
        "buy_split_delay_ms": BUY_SPLIT_DELAY_MS,
        "sell_split_chunks": SELL_SPLIT_CHUNKS,
    }
    return {"ok": True, "config": cfg}, 200


@app.get("/balance")
def balance_view() -> Any:
    base_free, quote_free = _avail_balances()
    return {
        "ok": True,
        "base": {base_id: base_free},
        "quote": {quote_id: quote_free},
    }, 200


def _check_secret(req) -> None:
    given = req.headers.get("X-Webhook-Secret") or req.args.get("secret") or ""
    # TradingView ne peut envoyer que dans le payload -> on check aussi plus bas
    if WEBHOOK_SECRET and given and given != WEBHOOK_SECRET:
        raise RuntimeError("Webhook secret invalide")


@app.post("/webhook")
def webhook() -> Any:
    # Sécurité
    try:
        _check_secret(request)
    except Exception as e:
        logger.warning("Secret KO: %s", e)
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    # Payload
    try:
        payload = request.get_json(force=True, silent=False)
    except Exception:
        payload = {}
    payload = payload or {}

    # Secret dans le JSON (flux TradingView recommandé)
    js_secret = str(payload.get("secret", "")).strip()
    if WEBHOOK_SECRET and js_secret != WEBHOOK_SECRET:
        logger.warning("Secret KO: Webhook secret invalide")
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    logger.info("tv-kraken:Webhook payload: %s", json.dumps(payload, ensure_ascii=False))
    safe_log_state({"stage": "webhook_received"})

    # Champs utiles
    signal = str(payload.get("signal", payload.get("type", ""))).strip().upper()
    force_close = bool(payload.get("force_close") or payload.get("force", False))
    price_hint = float(payload.get("price") or 0.0)

    # Overrides sizing (optionnels)
    quote_override = payload.get("quote")  # € à dépenser côté BUY
    amount_override = payload.get("amount")  # qty base côté SELL
    try:
        quote_override = float(quote_override) if quote_override is not None else None
    except Exception:
        quote_override = None
    try:
        amount_override = float(amount_override) if amount_override is not None else None
    except Exception:
        amount_override = None

    # Normalisation signaux
    if signal == "LONG":
        signal = "BUY"
    if signal in ("SHORT", "CLOSE"):
        signal = "SELL"

    if signal not in ("BUY", "SELL"):
        return jsonify({"ok": False, "error": "signal_inconnu"}), 400

    # Prix
    last_price = _current_price(signal, price_hint)
    if last_price <= 0:
        return jsonify({"ok": False, "error": "price_unavailable"}), 200

    # Cooldown BUY (hors force_close)
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

            spend, base_amt = _compute_buy_amount(last_price, quote_override)
            if spend <= 0 or base_amt <= 0:
                logger.info("Sizing nul (spend=%.2f, amount=%.8f)", spend, base_amt)
                return jsonify({"ok": False, "error": "sizing_zero"}), 200

            logger.info(
                "BUY %s | spend≈%.2f %s => amount≈%.8f %s @ %.4f",
                SYMBOL, spend, QUOTE_SYMBOL, base_amt, BASE_SYMBOL, last_price,
            )

            # Split facultatif
            results: list[Dict[str, Any]] = []
            chunks = max(1, BUY_SPLIT_CHUNKS)
            amt_per = _round_amount(base_amt / chunks)
            for i in range(chunks):
                if amt_per <= 0:
                    break
                order = _place_order("buy", amt_per)
                results.append(order)
                if BUY_SPLIT_DELAY_MS > 0 and i < chunks - 1:
                    time.sleep(BUY_SPLIT_DELAY_MS / 1000.0)

            # maj state
            STATE["last_buy_ts"] = now
            STATE["last_signal"] = "BUY"
            save_state(STATE)

            return jsonify({"ok": True, "side": "BUY", "amount": base_amt, "orders": results}), 200

        elif signal == "SELL":
            ok, sellable = _enough_to_sell()
            if not ok or sellable <= 0:
                logger.info("Aucune quantité %s disponible pour SELL", BASE_SYMBOL)
                return jsonify({"ok": False, "error": "no_base_available"}), 200

            amount = _round_amount(amount_override if amount_override and amount_override > 0 else sellable)
            if amount <= 0:
                return jsonify({"ok": False, "error": "amount_zero"}), 200

            logger.info("SELL %s | amount≈%.8f %s", SYMBOL, amount, BASE_SYMBOL)

            results: list[Dict[str, Any]] = []
            chunks = max(1, SELL_SPLIT_CHUNKS)
            amt_per = _round_amount(amount / chunks)
            for i in range(chunks):
                if amt_per <= 0:
                    break
                order = _place_order("sell", amt_per)
                results.append(order)

            STATE["last_signal"] = "SELL"
            save_state(STATE)

            return jsonify({"ok": True, "side": "SELL", "amount": amount, "orders": results}), 200

        # unreachable
        return jsonify({"ok": False, "error": "unreachable"}), 400

    except ccxt.BaseError as ex:
        logger.exception("Erreur ccxt: %s", ex)
        return jsonify({"ok": False, "error": f"ccxt:{type(ex).__name__}"}), 200
    except Exception as ex:
        logger.exception("Erreur interne: %s", ex)
        return jsonify({"ok": False, "error": "internal_error"}), 500


# -----------------------------------------------------------------------------
# Gunicorn / Dev
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")), debug=False)
