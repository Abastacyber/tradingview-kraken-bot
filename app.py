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

# =============================================================================
# Logging
# =============================================================================
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("tv-kraken")

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
EXCHANGE_NAME  = env_str("EXCHANGE", "kraken").lower()          # kraken
BASE_SYMBOL    = env_str("BASE_SYMBOL", "BTC").upper()          # ex: BTC
QUOTE_SYMBOL   = env_str("QUOTE_SYMBOL", "EUR").upper()         # ex: EUR
SYMBOL         = env_str("SYMBOL", f"{BASE_SYMBOL}/{QUOTE_SYMBOL}").upper()
ORDER_TYPE     = env_str("ORDER_TYPE", "market").lower()        # market only

# sizing
FIXED_QUOTE_PER_TRADE = env_float("FIXED_QUOTE_PER_TRADE", 10.0)  # € à dépenser par BUY
MIN_QUOTE_PER_TRADE   = env_float("MIN_QUOTE_PER_TRADE",   10.0)  # garde-fou
FEE_BUFFER_PCT        = env_float("FEE_BUFFER_PCT",        0.002) # 0.2%
QUOTE_RESERVE         = env_float("QUOTE_RESERVE",         0.0)   # € à laisser

# risk (valeurs dispo si tu veux les exploiter ensuite)
RISK_PCT   = env_float("RISK_PCT",   0.01)
MAX_SL_PCT = env_float("MAX_SL_PCT", 0.05)

# cooldown achat
BUY_COOL_SEC = env_int("BUY_COOL_SEC", 180)

# modes
DRY_RUN          = env_bool("DRY_RUN", False)
RESTORE_ON_START = env_bool("RESTORE_ON_START", True)
STATE_FILE       = env_str("STATE_FILE", "/tmp/bot_state.json")

# trailing (non utilisé pour passer ordre, mais conservé en config)
TRAILING_ENABLED         = env_bool("TRAILING_ENABLED", True)
TRAIL_ACTIVATE_PCT_CONF2 = env_float("TRAIL_ACTIVATE_PCT_CONF2", 0.003)
TRAIL_ACTIVATE_PCT_CONF3 = env_float("TRAIL_ACTIVATE_PCT_CONF3", 0.005)
TRAIL_GAP_CONF2          = env_float("TRAIL_GAP_CONF2", 0.0004)
TRAIL_GAP_CONF3          = env_float("TRAIL_GAP_CONF3", 0.003)

# sécu API / webhook
WEBHOOK_SECRET    = env_str("WEBHOOK_SECRET", "")
KRAKEN_API_KEY    = os.getenv("KRAKEN_API_KEY", "")
KRAKEN_API_SECRET = os.getenv("KRAKEN_API_SECRET", "")

# =============================================================================
# State (persistant simple)
# =============================================================================
DEFAULT_STATE: Dict[str, Any] = {
    "last_buy_ts": 0.0,
    "last_signal": None,
}
def load_state() -> Dict[str, Any]:
    if RESTORE_ON_START and os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                s = json.load(f)
                if isinstance(s, dict):
                    s = {**DEFAULT_STATE, **s}
                    log.info("State restauré depuis %s", STATE_FILE)
                    return s
        except Exception as e:
            log.warning("Impossible de charger le state (%s), on repart clean.", e)
    return dict(DEFAULT_STATE)

def save_state(state: Dict[str, Any]) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception as e:
        log.warning("Impossible d'écrire le state: %s", e)

STATE = load_state()

# =============================================================================
# Exchange init
# =============================================================================
if EXCHANGE_NAME != "kraken":
    raise RuntimeError("Cette version supporte Kraken (spot).")

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

market = exchange.market(SYMBOL)         # dict marché ccxt
amount_prec = market.get("precision", {}).get("amount")  # ex: 8
min_cost = (market.get("limits", {}) or {}).get("cost", {}).get("min")

# =============================================================================
# Utils
# =============================================================================
def round_amount(amount: float) -> float:
    """Arrondi l'amount base selon la précision de lot du marché."""
    if amount_prec is None:
        return amount
    step = 10 ** (-amount_prec)
    return math.floor(amount / step) * step

def fetch_balances() -> Tuple[float, float]:
    """
    Retourne (base_free, quote_free) en codes ccxt normalisés.
    ccxt normalise les devises Kraken (EUR, BTC, ETH, SOL, etc.).
    """
    bal = exchange.fetch_balance()
    base_free = float(bal.get(BASE_SYMBOL, {}).get("free", 0.0))
    quote_free = float(bal.get(QUOTE_SYMBOL, {}).get("free", 0.0))
    return base_free, quote_free

def compute_buy_amount(last_price: float, quote_override: Optional[float] = None) -> Tuple[float, float]:
    """
    Calcule (quote_to_spend, base_amount arrondi).
    """
    _, quote_free = fetch_balances()
    if last_price <= 0:
        return 0.0, 0.0

    # budget de départ
    spend = FIXED_QUOTE_PER_TRADE if quote_override is None else float(quote_override)

    # bornes
    spend = max(spend, MIN_QUOTE_PER_TRADE)
    # réserve + buffer frais
    max_spend = max(0.0, quote_free - QUOTE_RESERVE) * (1.0 - FEE_BUFFER_PCT)
    spend = min(spend, max_spend)

    # min cost de l'exchange si dispo
    if isinstance(min_cost, (int, float)) and min_cost > 0:
        spend = max(spend, float(min_cost))

    if spend <= 0:
        return 0.0, 0.0

    base_amt = round_amount(spend / last_price)
    if base_amt <= 0:
        return 0.0, 0.0

    return spend, base_amt

def enough_to_sell(min_amt: float = 0.0) -> Tuple[bool, float]:
    """Dispo base ? Renvoie (ok, base_free)."""
    base_free, _ = fetch_balances()
    if min_amt <= 0:
        # seuil mini très petit selon précision
        p = amount_prec if amount_prec is not None else 8
        min_amt = 10 ** (-p)
    return (base_free >= min_amt), base_free

def place_order(side: str, amount_base: float, symbol: str = SYMBOL) -> Dict[str, Any]:
    if DRY_RUN:
        log.info("DRY_RUN %s %s %s", side.upper(), amount_base, symbol)
        return {"dry_run": True, "side": side, "amount": amount_base, "symbol": symbol}

    if amount_base <= 0:
        raise RuntimeError("Amount <= 0")

    if ORDER_TYPE != "market":
        raise RuntimeError("Cette version ne gère que 'market'")

    if side.lower() == "buy":
        return exchange.create_market_buy_order(symbol, amount_base)   # amount en BASE
    elif side.lower() == "sell":
        return exchange.create_market_sell_order(symbol, amount_base)
    else:
        raise RuntimeError("Side inconnu")

def get_last_price(symbol: str = SYMBOL) -> float:
    try:
        t = exchange.fetch_ticker(symbol)
        price = float(t.get("last") or t.get("close") or 0.0)
        if price > 0:
            return price
    except Exception:
        pass
    # fallback orderbook
    try:
        ob = exchange.fetch_order_book(symbol, limit=5)
        bid = float(ob["bids"][0][0]) if ob["bids"] else 0.0
        ask = float(ob["asks"][0][0]) if ob["asks"] else 0.0
        return ask or bid or 0.0
    except Exception:
        return 0.0

# =============================================================================
# Flask
# =============================================================================
app = Flask(__name__)

@app.get("/health")
def health() -> Any:
    return {
        "ok": True,
        "exchange": EXCHANGE_NAME,
        "symbol": SYMBOL,
        "order_type": ORDER_TYPE,
        "dry_run": DRY_RUN,
    }, 200

@app.get("/balance")
def balance() -> Any:
    base_free, quote_free = fetch_balances()
    return {
        "ok": True,
        "base": BASE_SYMBOL,
        "base_free": base_free,
        "quote": QUOTE_SYMBOL,
        "quote_free": quote_free,
    }, 200

@app.get("/price")
def price() -> Any:
    return {"ok": True, "symbol": SYMBOL, "last": get_last_price(SYMBOL)}, 200

def read_payload() -> Dict[str, Any]:
    try:
        p = request.get_json(force=True, silent=False)
    except Exception:
        p = {}
    return p or {}

def check_secret(payload: Dict[str, Any]) -> None:
    """
    Accepte le secret en:
      - Header: X-Webhook-Secret
      - Query string: ?secret=...
      - JSON: {"secret": "..."}
    """
    if not WEBHOOK_SECRET:
        return
    given = (
        request.headers.get("X-Webhook-Secret")
        or request.args.get("secret")
        or str(payload.get("secret", ""))
    )
    if given != WEBHOOK_SECRET:
        raise RuntimeError("Webhook secret invalide")

@app.post("/webhook")
def webhook() -> Any:
    payload = read_payload()
    try:
        check_secret(payload)
    except Exception as e:
        log.warning("Secret KO: %s", e)
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    log.info("tv-kraken:Webhook payload: %s", json.dumps(payload, ensure_ascii=False))

    # champs utiles
    raw_signal   = str(payload.get("signal") or payload.get("type") or "").strip().upper()
    force_close  = bool(payload.get("force_close") or payload.get("force") or False)
    price_hint   = float(payload.get("price") or 0.0)
    symbol_in    = str(payload.get("symbol") or "").upper()
    quote_override = payload.get("quote")  # montant en QUOTE pour BUY (ex: euros)
    sell_amount   = payload.get("amount")  # quantité à vendre en BASE (optionnel)

    # normalisation signal
    if raw_signal in ("LONG",):
        raw_signal = "BUY"
    if raw_signal in ("SHORT", "CLOSE"):
        raw_signal = "SELL"
    if raw_signal not in ("BUY", "SELL"):
        return jsonify({"ok": False, "error": "signal_inconnu"}), 400

    # symbole à utiliser
    symbol = symbol_in if symbol_in in exchange.markets else SYMBOL
    base = exchange.market(symbol)["base"]
    quote = exchange.market(symbol)["quote"]

    # prix
    last_price = float(price_hint or 0.0)
    if last_price <= 0:
        last_price = get_last_price(symbol)

    # cooldown BUY
    now = time.time()
    if raw_signal == "BUY" and not force_close:
        last_buy = float(STATE.get("last_buy_ts", 0.0))
        if BUY_COOL_SEC > 0 and (now - last_buy) < BUY_COOL_SEC:
            wait = int(BUY_COOL_SEC - (now - last_buy))
            log.info("Cooldown achat actif (%ss restants)", wait)
            return jsonify({"ok": True, "skipped": "cooldown_buy", "wait_sec": wait}), 200

    try:
        if raw_signal == "BUY":
            # sizing
            spend, base_amt = compute_buy_amount(last_price, quote_override=quote_override)
            if spend <= 0 or base_amt <= 0:
                log.info("Sizing nul (spend=%.2f, amount=%.8f)", spend, base_amt)
                return jsonify({"ok": False, "error": "sizing_zero"}), 200

            log.info("BUY %s | spend≈%.2f %s => amount≈%.8f %s @ %.6f",
                     symbol, spend, quote, base_amt, base, last_price)
            order = place_order("buy", base_amt, symbol=symbol)

            STATE["last_buy_ts"] = now
            STATE["last_signal"] = "BUY"
            save_state(STATE)

            return jsonify({
                "ok": True, "side": "BUY", "symbol": symbol, "amount": base_amt,
                "spent_quote": spend, "price": last_price, "order": order
            }), 200

        # SELL
        ok, base_free = enough_to_sell()
        if not ok:
            log.info("Aucune quantité %s disponible pour SELL", base)
            return jsonify({"ok": False, "error": "no_base_available", "base_free": base_free}), 200

        amount = float(sell_amount) if sell_amount is not None else base_free
        amount = round_amount(max(0.0, amount))
        if amount <= 0:
            return jsonify({"ok": False, "error": "amount_zero"}), 200

        log.info("SELL %s | amount≈%.8f %s", symbol, amount, base)
        order = place_order("sell", amount, symbol=symbol)

        STATE["last_signal"] = "SELL"
        save_state(STATE)

        return jsonify({
            "ok": True, "side": "SELL", "symbol": symbol, "amount": amount,
            "order": order
        }), 200

    except ccxt.BaseError as ex:
        log.exception("Erreur ccxt: %s", ex)
        return jsonify({"ok": False, "error": f"ccxt:{type(ex).__name__}"}), 200
    except Exception as ex:
        log.exception("Erreur interne: %s", ex)
        return jsonify({"ok": False, "error": "internal_error"}), 500


# =============================================================================
# Entrypoint
# =============================================================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")), debug=False)
