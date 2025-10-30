from __future__ import annotations

import os
import json
import time
import math
import logging
from typing import Any, Dict, Tuple, Optional

from flask import Flask, request, jsonify
import ccxt  # type: ignore

# ───────────────────────── Logging ─────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("tv-kraken")

# ───────────────────────── Helpers ENV ─────────────────────────
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

# ───────────────────────── Config ─────────────────────────
EXCHANGE_NAME  = env_str("EXCHANGE", "kraken").lower()
BASE_SYMBOL    = env_str("BASE_SYMBOL", "BTC").upper()
QUOTE_SYMBOL   = env_str("QUOTE_SYMBOL", "USDT").upper()
SYMBOL_ENV     = env_str("SYMBOL", f"{BASE_SYMBOL}/{QUOTE_SYMBOL}").upper()

ORDER_TYPE     = env_str("ORDER_TYPE", "market").lower()

# sizing / coûts
FIXED_QUOTE_PER_TRADE = env_float("FIXED_QUOTE_PER_TRADE", 10.0)
MIN_QUOTE_PER_TRADE   = env_float("MIN_QUOTE_PER_TRADE", 10.0)
FEE_BUFFER_PCT        = env_float("FEE_BUFFER_PCT", 0.002)

# réserves
BASE_RESERVE          = env_float("BASE_RESERVE", 0.0)     # garde une petite qty base
QUOTE_RESERVE         = env_float("QUOTE_RESERVE", 0.0)    # garde une petite qty quote

# gestion risque / SL (infos log uniquement ici)
RISK_PCT              = env_float("RISK_PCT", 0.02)
MAX_SL_PCT            = env_float("MAX_SL_PCT", 0.05)

# cooldown achat
BUY_COOL_SEC          = env_int("BUY_COOL_SEC", 300)

# sandbox & état
DRY_RUN               = env_bool("DRY_RUN", False)
RESTORE_ON_START      = env_bool("RESTORE_ON_START", True)
STATE_FILE            = env_str("STATE_FILE", "/tmp/bot_state.json")

# Trailing (valeurs exposées dans les logs, pas d’ordres auto ici)
TRAILING_ENABLED         = env_bool("TRAILING_ENABLED", True)
TRAIL_ACTIVATE_PCT_CONF2 = env_float("TRAIL_ACTIVATE_PCT_CONF2", 0.003)
TRAIL_ACTIVATE_PCT_CONF3 = env_float("TRAIL_ACTIVATE_PCT_CONF3", 0.005)
TRAIL_GAP_CONF2          = env_float("TRAIL_GAP_CONF2", 0.0004)
TRAIL_GAP_CONF3          = env_float("TRAIL_GAP_CONF3", 0.003)

# sécu API / webhook
WEBHOOK_SECRET        = env_str("WEBHOOK_SECRET", "")
KRAKEN_API_KEY        = os.getenv("KRAKEN_API_KEY", "")
KRAKEN_API_SECRET     = os.getenv("KRAKEN_API_SECRET", "")

# option sandbox
KRAKEN_ENV            = os.getenv("KRAKEN_ENV", "mainnet").lower()  # "mainnet" | "testnet"

# ───────────────────────── State persistence ─────────────────────────
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
                    log.info("STATE restauré depuis %s", STATE_FILE)
                    return s
        except Exception as e:
            log.warning("STATE load error (%s), reset.", e)
    return dict(_DEFAULT_STATE)

def save_state(state: Dict[str, Any]) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception as e:
        log.warning("STATE save error: %s", e)

STATE = load_state()

# ───────────────────────── Exchange init ─────────────────────────
if EXCHANGE_NAME != "kraken":
    raise RuntimeError("Cette version supporte Kraken (spot) uniquement.")

ex_kwargs = {
    "apiKey": KRAKEN_API_KEY or "",
    "secret": KRAKEN_API_SECRET or "",
    "enableRateLimit": True,
    "options": {"adjustForTimeDifference": True},
}
exchange = ccxt.kraken(ex_kwargs)
if KRAKEN_ENV in ("testnet", "sandbox", "paper", "true", "1", "yes"):
    try:
        exchange.set_sandbox_mode(True)
    except Exception:
        pass

markets = exchange.load_markets()

# ───────────────────────── Utils & market helpers ─────────────────────────
def normalize_symbol(s: str) -> str:
    """
    Accepte 'btc-eur', 'BTCEUR', 'BTC/EUR', et mappe XBT->BTC.
    """
    if not s:
        return SYMBOL_ENV
    s = s.replace("-", "/").upper()
    if "/" not in s:
        # essaie d’inférer le séparateur
        for q in ("USDT", "USDC", "USD", "EUR", "BTC", "ETH"):
            if s.endswith(q):
                base = s[:-len(q)]
                s = f"{base}/{q}"
                break
        else:
            return SYMBOL_ENV
    base, quote = s.split("/")
    if base == "XBT":  # alias Kraken
        base = "BTC"
    return f"{base}/{quote}"

def ensure_market(symbol: str) -> Dict[str, Any]:
    if symbol not in markets:
        raise RuntimeError(f"Symbole inconnu sur Kraken: {symbol}")
    return markets[symbol]

def amount_precision(symbol: str) -> Optional[int]:
    m = ensure_market(symbol)
    prec = (m.get("precision") or {}).get("amount")
    return int(prec) if prec is not None else None

def round_amount(symbol: str, amount: float) -> float:
    p = amount_precision(symbol)
    if p is None:
        return float(amount)
    step = 10 ** (-p)
    return math.floor(float(amount) / step) * step

def market_limits(symbol: str) -> Tuple[float, float]:
    """
    Retourne (min_amount, min_cost) si disponibles, sinon (0.0, 0.0).
    """
    m = ensure_market(symbol)
    limits = m.get("limits") or {}
    min_amount = float((limits.get("amount") or {}).get("min") or 0.0)
    min_cost   = float((limits.get("cost") or {}).get("min") or 0.0)
    return (min_amount, min_cost)

def fetch_balances(symbol: Optional[str] = None) -> Tuple[float, float]:
    """
    Retourne (base_free, quote_free) pour le symbole donné (ccxt normalisé).
    """
    sym = normalize_symbol(symbol or SYMBOL_ENV)
    m = ensure_market(sym)
    base = m["base"]    # ex: SOL
    quote = m["quote"]  # ex: EUR

    bal = exchange.fetch_balance()
    base_free = float((bal.get(base) or {}).get("free") or 0.0)
    quote_free = float((bal.get(quote) or {}).get("free") or 0.0)
    return base_free, quote_free

def fetch_price(symbol: str) -> Tuple[float, float, float]:
    """
    Retourne (last, bid, ask) si dispo, sinon 0.0.
    """
    t = exchange.fetch_ticker(symbol)
    last = float(t.get("last") or t.get("close") or 0.0)
    bid  = float(t.get("bid")  or 0.0)
    ask  = float(t.get("ask")  or 0.0)
    return last, bid, ask

# ───────────────────────── Sizing BUY/SELL ─────────────────────────
def compute_buy_amount(symbol: str, last_price: float, quote_override: Optional[float]) -> Tuple[float, float]:
    """
    Calcule (spend_quote, base_amount arrondi). Applique réserves, min_cost/min_amount et buffer.
    """
    _, quote_free = fetch_balances(symbol)

    # max dépensable (réserve)
    usable_quote = max(0.0, quote_free - max(0.0, QUOTE_RESERVE))
    usable_quote *= (1.0 - FEE_BUFFER_PCT)

    # montant cible
    spend = float(quote_override) if (quote_override and quote_override > 0) else float(FIXED_QUOTE_PER_TRADE)

    # bornes locales
    spend = max(spend, 0.0)
    spend = min(spend, usable_quote)
    if spend < MIN_QUOTE_PER_TRADE:
        # si pas assez, on signale
        return (0.0, 0.0)

    min_amount, min_cost = market_limits(symbol)
    if min_cost and spend < min_cost:
        spend = min_cost  # on relève au minimum d’order notional

    if last_price <= 0:
        return (0.0, 0.0)

    base_amt = spend / last_price
    # respecte min_amount si présent
    if min_amount and base_amt < min_amount:
        base_amt = min_amount

    base_amt = round_amount(symbol, base_amt)
    # Re-valide après arrondi
    if base_amt <= 0:
        return (0.0, 0.0)

    return (spend, base_amt)

def compute_sell_amount(symbol: str, last_price: float, amount_base: Optional[float], quote_override: Optional[float]) -> float:
    """
    Retourne la quantité BASE à vendre (arrondie) selon:
      - amount_base (prioritaire) ou
      - quote_override converti en base, ou
      - tout le 'free' (moins réserve).
    """
    base_free, _ = fetch_balances(symbol)
    sellable = max(0.0, base_free - max(0.0, BASE_RESERVE))

    if amount_base and amount_base > 0:
        qty = min(float(amount_base), sellable)
    elif quote_override and quote_override > 0 and last_price > 0:
        qty = min(float(quote_override)/last_price, sellable)
    else:
        qty = sellable

    qty = round_amount(symbol, qty)

    # Respect min_amount si dispo
    min_amount, _ = market_limits(symbol)
    if min_amount and qty > 0 and qty < min_amount:
        # si on n'atteint pas, on tente de relèver au mini dans la limite du sellable
        qty = min_amount if sellable >= min_amount else 0.0

    return float(qty)

# ───────────────────────── Orders ─────────────────────────
def place_market_order(symbol: str, side: str, amount_base: float) -> Dict[str, Any]:
    if amount_base <= 0:
        raise RuntimeError("amount_base <= 0")

    if ORDER_TYPE != "market":
        raise RuntimeError("Cette version ne gère que 'market'")

    if DRY_RUN:
        log.info("DRY_RUN %s %s %s", side.upper(), amount_base, symbol)
        return {"dry_run": True, "side": side, "amount": amount_base, "symbol": symbol}

    if side.lower() == "buy":
        return exchange.create_market_buy_order(symbol, amount_base)
    elif side.lower() == "sell":
        return exchange.create_market_sell_order(symbol, amount_base)
    else:
        raise RuntimeError("Side inconnu (buy/sell)")

# ───────────────────────── Flask ─────────────────────────
app = Flask(__name__)

def check_secret(req) -> None:
    # On accepte: header, query, body JSON
    given = (
        req.headers.get("X-Webhook-Secret")
        or req.headers.get("X-Webhook-Token")
        or req.args.get("secret")
        or req.args.get("token")
        or (req.json or {}).get("secret")
        or (req.json or {}).get("token")
        or ""
    )
    if WEBHOOK_SECRET and given != WEBHOOK_SECRET:
        raise RuntimeError("Webhook secret invalide")

def safe_log_state(extra: Dict[str, Any] | None = None) -> None:
    try:
        snap = {
            "symbol_env": SYMBOL_ENV,
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
            snap.update(extra)
        log.debug("state=%s", json.dumps(snap))
    except Exception:
        pass

@app.get("/")
def root():
    return jsonify({
        "service": "tv-kraken-bot",
        "status": "ok",
        "endpoints": ["/health", "/price?symbol=BTC/EUR", "/balance?symbol=BTC/EUR", "/debug/limits?symbol=BTC/EUR", "/webhook"],
    }), 200

@app.get("/health")
def health():
    return jsonify({"ok": True, "exchange": EXCHANGE_NAME, "symbol_env": SYMBOL_ENV, "dry_run": DRY_RUN}), 200

@app.get("/price")
def price():
    try:
        symbol = normalize_symbol(request.args.get("symbol") or SYMBOL_ENV)
        ensure_market(symbol)
        last, bid, ask = fetch_price(symbol)
        return jsonify({"symbol": symbol, "last": last, "bid": bid, "ask": ask}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.get("/balance")
def balance():
    try:
        symbol = normalize_symbol(request.args.get("symbol") or SYMBOL_ENV)
        m = ensure_market(symbol)
        base, quote = m["base"], m["quote"]
        base_free, quote_free = fetch_balances(symbol)
        return jsonify({"symbol": symbol, "base": base, "quote": quote, "base_free": base_free, "quote_free": quote_free}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.get("/debug/limits")
def debug_limits():
    try:
        symbol = normalize_symbol(request.args.get("symbol") or SYMBOL_ENV)
        m = ensure_market(symbol)
        last, bid, ask = fetch_price(symbol)
        return jsonify({
            "symbol": symbol,
            "price": {"last": last, "bid": bid, "ask": ask},
            "limits": m.get("limits") or {},
            "precision": m.get("precision") or {},
            "info_keys": list((m.get("info") or {}).keys()),
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.post("/webhook")
def webhook():
    # Auth
    try:
        check_secret(request)
    except Exception as e:
        log.warning("Secret KO: %s", e)
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    # Payload
    try:
        payload = request.get_json(force=True, silent=False) or {}
    except Exception:
        payload = {}

    # Log sans secret
    safe_payload = dict(payload)
    safe_payload.pop("secret", None)
    safe_payload.pop("token", None)
    log.info("Webhook payload: %s", json.dumps(safe_payload, ensure_ascii=False))
    safe_log_state({"stage": "webhook_received"})

    # Signal & normalisation
    signal = str(payload.get("signal") or payload.get("type") or "").strip().upper()
    if signal == "PING":
        return jsonify({"ok": True, "pong": True, "ts": int(time.time())}), 200
    if signal in ("LONG",):
        signal = "BUY"
    if signal in ("SHORT", "CLOSE"):
        signal = "SELL"
    if signal not in ("BUY", "SELL"):
        return jsonify({"ok": False, "error": "signal_inconnu"}), 400

    # Symbole
    symbol = normalize_symbol(payload.get("symbol") or SYMBOL_ENV)
    try:
        ensure_market(symbol)
    except Exception as e:
        return jsonify({"ok": False, "error": f"bad_symbol: {e}"}), 400

    # Prix
    price_hint = float(payload.get("price") or 0.0)
    try:
        last, bid, ask = fetch_price(symbol)
        last_price = float(last or price_hint or 0.0)
    except Exception:
        last_price = price_hint or 0.0

    # Fallback via orderbook
    if last_price <= 0:
        try:
            ob = exchange.fetch_order_book(symbol, limit=5)
            if signal == "BUY":
                last_price = float(ob["asks"][0][0])
            else:
                last_price = float(ob["bids"][0][0])
        except Exception:
            pass

    # Paramètres sizing
    quote_override = None
    if payload.get("quote") is not None:
        try:
            qv = float(payload.get("quote"))
            quote_override = qv if qv > 0 else None
        except Exception:
            quote_override = None

    amount_override = None
    for k in ("amount", "qty", "qty_base"):
        if payload.get(k) is not None:
            try:
                av = float(payload.get(k))
                amount_override = av if av > 0 else None
                break
            except Exception:
                pass

    # Cooldown BUY (sauf force)
    force_close = bool(payload.get("force_close") or payload.get("force") or False)
    now = time.time()
    if signal == "BUY" and not force_close:
        last_buy = float(STATE.get("last_buy_ts", 0.0))
        if BUY_COOL_SEC > 0 and (now - last_buy) < BUY_COOL_SEC:
            wait = int(BUY_COOL_SEC - (now - last_buy))
            log.info("Cooldown achat actif (%ss restants)", wait)
            return jsonify({"ok": True, "skipped": "cooldown_buy", "wait_sec": wait}), 200

    # ─── BUY ───
    if signal == "BUY":
        if last_price <= 0:
            return jsonify({"ok": False, "error": "price_unavailable"}), 200
        spend, base_amt = compute_buy_amount(symbol, last_price, quote_override)
        if spend <= 0 or base_amt <= 0:
            base_free, quote_free = fetch_balances(symbol)
            return jsonify({
                "ok": False,
                "error": "sizing_error",
                "detail": "Montant trop faible ou fonds indisponibles.",
                "quote_free": quote_free, "min_quote": MIN_QUOTE_PER_TRADE
            }), 200

        log.info("BUY %s | spend≈%.2f %s => amount≈%.8f @ %.6f",
                 symbol, spend, ensure_market(symbol)["quote"], base_amt, last_price)
        try:
            order = place_market_order(symbol, "buy", base_amt)
        except ccxt.BaseError as ex:
            log.exception("ccxt error BUY: %s", ex)
            return jsonify({"ok": False, "error": f"ccxt:{type(ex).__name__}", "detail": str(ex)}), 200

        STATE["last_buy_ts"] = now
        STATE["last_signal"] = "BUY"
        save_state(STATE)
        return jsonify({"ok": True, "side": "BUY", "symbol": symbol, "amount": base_amt, "order": order}), 200

    # ─── SELL ───
    if signal == "SELL":
        if last_price <= 0:
            return jsonify({"ok": False, "error": "price_unavailable"}), 200

        qty = compute_sell_amount(symbol, last_price, amount_override, quote_override)
        if qty <= 0:
            base_free, _ = fetch_balances(symbol)
            return jsonify({
                "ok": False,
                "error": "no_base_available",
                "detail": "Quantité vendable insuffisante (réserve incluse ou min lot non atteint).",
                "base_free": base_free, "base_reserve": BASE_RESERVE
            }), 200

        log.info("SELL %s | amount≈%.8f", symbol, qty)
        try:
            order = place_market_order(symbol, "sell", qty)
        except ccxt.BaseError as ex:
            log.exception("ccxt error SELL: %s", ex)
            return jsonify({"ok": False, "error": f"ccxt:{type(ex).__name__}", "detail": str(ex)}), 200

        STATE["last_signal"] = "SELL"
        save_state(STATE)
        return jsonify({"ok": True, "side": "SELL", "symbol": symbol, "amount": qty, "order": order}), 200

    # unreachable
    return jsonify({"ok": False, "error": "unreachable"}), 400

# ───────────────────────── Entrypoint ─────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")), debug=False)
