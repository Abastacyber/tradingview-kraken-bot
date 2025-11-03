# app.py
# TV → Render → Kraken (CCXT)
# - Retries réseau (backoff)
# - Cache balance (fallback si Kraken reset)
# - Anti-doublon webhook (3s)
# - Normalisation symboles XBT/BTC
# - BUY par montant en EUR (quote) -> quantité arrondie
# - SELL sur solde libre, filtrage "dust" / min_amount
# - Réponses 200 même si erreur réseau (skip_reason)

import os
import time
import json
import math
import hmac
import random
import logging
from hashlib import sha256
from datetime import datetime, timedelta

from flask import Flask, request, jsonify

import ccxt
import requests
import urllib3

# ───────────────────────── Config env
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "Ramses293")
PORT = int(os.getenv("PORT", "10000"))

API_KEY = os.getenv("KRAKEN_API_KEY") or os.getenv("KRAKEN_KEY") or ""
API_SECRET = os.getenv("KRAKEN_API_SECRET") or os.getenv("KRAKEN_SECRET") or ""

SYMBOL_DEFAULT = os.getenv("SYMBOL_DEFAULT", "BTC/EUR")  # interne en BTC/EUR, mappé correctement côté CCXT
FIXED_QUOTE_EUR = float(os.getenv("FIXED_QUOTE_EUR", "100"))  # Montant EUR par défaut si non fourni dans le payload
BUY_COOL_SEC = int(os.getenv("BUY_COOL_SEC", "180"))          # anti spam des BUY
SELL_COOL_SEC = int(os.getenv("SELL_COOL_SEC", "5"))          # petit cooldown SELL

# Retry/backoff
RETRY_ATTEMPTS = int(os.getenv("RETRY_ATTEMPTS", "3"))
RETRY_BASE_SEC = float(os.getenv("RETRY_BASE_SEC", "0.5"))

# Cache balance
BAL_TTL_SEC = int(os.getenv("BAL_TTL_SEC", "5"))

# Logger
logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("tv-kraken")

# ───────────────────────── CCXT Kraken
kraken = ccxt.kraken({
    "apiKey": API_KEY,
    "secret": API_SECRET,
    "enableRateLimit": True,
    # Kraken peut être chatouilleux → petit délai
    "timeout": 20000,
})

# Pour tracer ce que CCXT envoie (utile en DEBUG)
kraken.session.headers.update({"User-Agent": "python-requests/2.x ccxt"})

# ───────────────────────── Flask
app = Flask(__name__)

# ───────────────────────── Utilitaires

RETRYABLE = (
    ccxt.NetworkError,
    ccxt.DDoSProtection,
    ccxt.ExchangeNotAvailable,
    requests.exceptions.ConnectionError,
    urllib3.exceptions.ProtocolError,
    ConnectionResetError,
)

def with_retry(fn, *args, **kwargs):
    for i in range(RETRY_ATTEMPTS):
        try:
            return fn(*args, **kwargs)
        except RETRYABLE as e:
            wait = RETRY_BASE_SEC * (2 ** i) + random.uniform(0, 0.25)
            logger.warning(f"retryable error {type(e).__name__}: {e} | attempt={i+1}/{RETRY_ATTEMPTS} | sleep={wait:.2f}s")
            time.sleep(wait)
    # dernière tentative (laisse lever l'erreur pour que l'appelant décide)
    return fn(*args, **kwargs)

def now_ts_ms() -> int:
    return int(time.time() * 1000)

# ── Normalisation des symboles
def normalize_symbol(sym_in: str) -> str:
    """
    Accepte 'BTC/EUR' ou 'XBT/EUR' → travaille en 'BTC/EUR' (CCXT mappe vers XXBTZEUR).
    """
    s = (sym_in or SYMBOL_DEFAULT).upper().replace("XBT", "BTC")
    # sécurité format
    if "/" not in s:
        if s.startswith("BTC"):
            s = "BTC/EUR"
        else:
            s = SYMBOL_DEFAULT
    return s

# ── Chargement des marchés + infos de précision/min amount
_markets_cache = None
def load_markets_if_needed():
    global _markets_cache
    if _markets_cache is None:
        _markets_cache = with_retry(kraken.load_markets)

def get_market(symbol: str):
    load_markets_if_needed()
    m = kraken.markets.get(symbol)
    if not m:
        # Essaye XBT/EUR si pas trouvé
        alt = symbol.replace("BTC", "XBT")
        m = kraken.markets.get(alt)
    return m

def amount_to_precision(symbol: str, amount: float) -> float:
    return float(kraken.amount_to_precision(symbol, amount))

# ── Balance cache
_last_bal = None
_last_bal_at = 0

def fetch_free_balance_cached() -> dict:
    global _last_bal, _last_bal_at
    try:
        if (time.time() - _last_bal_at) <= BAL_TTL_SEC and _last_bal is not None:
            return _last_bal
        bal = with_retry(kraken.fetch_free_balance)
        _last_bal = bal
        _last_bal_at = time.time()
        logger.debug(f"balance refreshed (cached {BAL_TTL_SEC}s)")
        return bal
    except RETRYABLE as e:
        logger.error(f"balance fetch error (network): {e}")
        return _last_bal or {}

def invalidate_balance_cache():
    global _last_bal_at
    _last_bal_at = 0

# ── Prix last
def fetch_last_price(symbol: str) -> float:
    t = with_retry(kraken.fetch_ticker, symbol)
    # 'last' ou 'close'
    price = t.get("last") or t.get("close") or t.get("bid") or t.get("ask")
    if not price:
        raise RuntimeError("no_price")
    return float(price)

# ── Anti-doublon (3s)
_recent_signals = {}  # key -> ts_ms

def is_duplicate(key: str, window_sec=3) -> bool:
    now = time.time()
    # purge légère
    for k, v in list(_recent_signals.items()):
        if now - v > 5:
            _recent_signals.pop(k, None)
    if key in _recent_signals and (now - _recent_signals[key]) < window_sec:
        return True
    _recent_signals[key] = now
    return False

# ───────────────────────── Trading helpers

_last_buy_at = 0
_last_sell_at = 0

def compute_min_amount(symbol: str) -> float:
    m = get_market(symbol)
    if m and m.get("limits") and m["limits"].get("amount") and m["limits"]["amount"].get("min"):
        return float(m["limits"]["amount"]["min"])
    # safe fallback pour BTC sur Kraken
    return 0.00001

def create_market_buy_by_quote(symbol: str, quote_eur: float):
    """
    Calcule la quantité en BTC pour un BUY market à partir d'un montant EUR.
    """
    px = fetch_last_price(symbol)
    qty_raw = max(quote_eur / px, 0.0)
    qty = amount_to_precision(symbol, qty_raw)
    min_amt = compute_min_amount(symbol)
    if qty < min_amt:
        return None, f"min_amount_not_met qty={qty} < {min_amt}"
    order = with_retry(kraken.create_market_buy_order, symbol, qty)
    invalidate_balance_cache()
    return order, None

def create_market_sell_all_free(symbol: str):
    base_ccy = symbol.split("/")[0]
    free = fetch_free_balance_cached()
    qty_raw = float(free.get(base_ccy, 0.0))
    min_amt = compute_min_amount(symbol)
    if qty_raw <= 0:
        return None, f"dust_too_small qty={qty_raw}"
    qty = amount_to_precision(symbol, qty_raw)
    if qty < min_amt:
        return None, f"dust_too_small qty={qty} < {min_amt}"
    order = with_retry(kraken.create_market_sell_order, symbol, qty)
    invalidate_balance_cache()
    return order, None

# ───────────────────────── Flask routes

@app.get("/")
def root():
    return jsonify({"name": "tv-kraken-bot", "status": "ok"}), 200

@app.get("/health")
def health():
    return "ok", 200

@app.post("/webhook")
def webhook():
    global _last_buy_at, _last_sell_at
    t0 = time.time()
    try:
        payload = request.get_json(force=True, silent=False)
    except Exception:
        logger.exception("webhook json parse error")
        return jsonify({"ok": False, "error": "bad_json"}), 400

    # Logs utiles
    try:
        log_payload = {**payload}
        if "secret" in log_payload:
            log_payload["secret"] = "***"
        logger.debug(f"tv-kraken | Webhook payload: {json.dumps(log_payload, ensure_ascii=False)}")
    except Exception:
        pass

    # Secret
    if payload.get("secret") != WEBHOOK_SECRET:
        logger.warning("invalid secret")
        return jsonify({"ok": False, "error": "forbidden"}), 403

    # Anti-doublon
    sig = payload.get("signal", "").upper()
    sym_in = payload.get("symbol") or SYMBOL_DEFAULT
    key = f"{sig}|{sym_in}|{payload.get('timestamp', 0)}"
    if is_duplicate(key, window_sec=3):
        logger.info(f"dedup | key={key} | skipped (<3s)")
        return jsonify({"ok": True, "skip": True, "reason": "dup"}), 200

    # Normalisation symbole
    symbol = normalize_symbol(sym_in)

    # PING utilitaire
    if sig == "PING":
        return jsonify({"ok": True, "pong": True}), 200

    # Type ordre (market attendu)
    order_type = (payload.get("type") or "market").lower()
    if order_type != "market":
        return jsonify({"ok": False, "error": "only_market_supported"}), 200

    # BUY
    if sig == "BUY":
        # anti-spam
        if (time.time() - _last_buy_at) < BUY_COOL_SEC:
            return jsonify({"ok": True, "skip": True, "reason": "buy_cooldown"}), 200

        quote = float(payload.get("quote") or FIXED_QUOTE_EUR)
        try:
            # sécurité : si pas assez d'EUR libre, skip
            free = fetch_free_balance_cached()
            eur_free = float(free.get("EUR", 0.0))
            if eur_free <= 0 or eur_free < max(quote * 0.99, 5.0):
                logger.info(f"buy_skip | not_enough_EUR free={eur_free} wanted≈{quote}")
                return jsonify({"ok": True, "skip": True, "reason": "not_enough_eur", "eur_free": eur_free}), 200

            order, err = create_market_buy_by_quote(symbol, quote)
            if err:
                logger.info(f"buy_skip | {err}")
                return jsonify({"ok": True, "skip": True, "reason": err}), 200

            _last_buy_at = time.time()
            logger.info(f"buy_done | tx={order.get('id')} qty={order.get('amount')} {symbol}")
            return jsonify({"ok": True, "side": "buy", "symbol": symbol, "order": order}), 200

        except RETRYABLE as e:
            logger.error(f"buy network error: {e}")
            return jsonify({"ok": False, "skip": True, "reason": "network_error"}), 200
        except Exception as e:
            logger.exception("buy error")
            return jsonify({"ok": False, "error": str(e)}), 500

    # SELL (force_close ignoré ici, on vend le free)
    if sig == "SELL":
        if (time.time() - _last_sell_at) < SELL_COOL_SEC:
            return jsonify({"ok": True, "skip": True, "reason": "sell_cooldown"}), 200
        try:
            order, err = create_market_sell_all_free(symbol)
            if err:
                logger.info(f"skip_sell | reason={err}")
                return jsonify({"ok": True, "skip": True, "reason": err}), 200

            _last_sell_at = time.time()
            logger.info(f"sell_done | qty={order.get('amount')} chunks={1}")
            return jsonify({"ok": True, "side": "sell", "symbol": symbol, "order": order}), 200

        except RETRYABLE as e:
            logger.error(f"sell network error: {e}")
            return jsonify({"ok": False, "skip": True, "reason": "network_error"}), 200
        except Exception as e:
            logger.exception("sell error")
            return jsonify({"ok": False, "error": str(e)}), 500

    # Signal inconnu
    return jsonify({"ok": False, "error": "bad_signal"}), 200


# ───────────────────────── Entrée
if __name__ == "__main__":
    # Pré-charge les marchés au boot (évite la latence sur 1er appel)
    try:
        load_markets_if_needed()
        logger.info("markets loaded")
    except Exception as e:
        logger.warning(f"load_markets failed (will retry later): {e}")

    app.run(host="0.0.0.0", port=PORT)
