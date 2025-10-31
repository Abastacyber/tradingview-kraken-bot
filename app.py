# app.py
# ─────────────────────────────────────────────────────────────────────────────
# Render + TradingView webhook -> Kraken (via CCXT)
# Gère BUY/SELL market, split d'ordres, cooldown, réserves, DRY-RUN
# Logs verbeux pour diagnostiquer : "skip_*" et "order_*"
# ─────────────────────────────────────────────────────────────────────────────

import os
import json
import time
import math
import threading
import logging
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from flask import Flask, request, jsonify
import ccxt

# ───────────────────────────────── Config & utils

def to_bool(v: Optional[str], default=False) -> bool:
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "y", "yes", "true", "on"}

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("tv-kraken")

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
STATE_FILE     = os.getenv("STATE_FILE", "/tmp/bot_state.json")
RESTORE_STATE  = to_bool(os.getenv("RESTORE_ON_START", "true"), True)

EXCHANGE_ID    = os.getenv("EXCHANGE", "kraken").lower()
API_KEY        = os.getenv("KRAKEN_API_KEY")
API_SECRET     = os.getenv("KRAKEN_API_SECRET")

SYMBOL_ENV     = os.getenv("SYMBOL", "BTC/EUR")
ORDER_TYPE     = os.getenv("ORDER_TYPE", "market").lower()

ALLOW_PAYLOAD_SYMBOL = to_bool(os.getenv("ALLOW_PAYLOAD_SYMBOL", "true"), True)

FIXED_QUOTE_PER_TRADE = float(os.getenv("FIXED_QUOTE_PER_TRADE", "50"))
MIN_QUOTE_PER_TRADE   = float(os.getenv("MIN_QUOTE_PER_TRADE", "10"))
RISK_PCT              = float(os.getenv("RISK_PCT", "0.01"))
FEE_BUFFER_PCT        = float(os.getenv("FEE_BUFFER_PCT", "0.0015"))

QUOTE_RESERVE         = float(os.getenv("QUOTE_RESERVE", "0"))
BASE_RESERVE          = float(os.getenv("BASE_RESERVE", "0"))

BUY_SPLIT_CHUNKS      = int(os.getenv("BUY_SPLIT_CHUNKS", "1"))
BUY_SPLIT_DELAY_MS    = int(os.getenv("BUY_SPLIT_DELAY_MS", "300"))
SELL_SPLIT_CHUNKS     = int(os.getenv("SELL_SPLIT_CHUNKS", "1"))

BUY_COOL_SEC          = int(os.getenv("BUY_COOL_SEC", "0"))

DRY_RUN               = to_bool(os.getenv("DRY_RUN", "false"), False)

# ───────────────────────────────── Helpers symboles

def normalize_symbol(sym: str) -> str:
    """
    Unifie symboles venant de TradingView ou de l'ENV :
    - XBT -> BTC
    - ZEUR -> EUR
    - enlève espaces, remplace '-' par '/'
    """
    s = (sym or "").upper().replace(" ", "").replace("-", "/")
    s = s.replace("XXBT", "BTC").replace("XBT", "BTC")
    s = s.replace("ZEUR", "EUR")
    if "/" not in s and len(s) >= 6:
        s = s[:3] + "/" + s[3:]
    return s

# ───────────────────────────────── State (cooldown / in-memory)

_state_lock = threading.Lock()
_state: Dict[str, Any] = {
    "last_buy_ts": None,
    "last_sell_ts": None,
}

def load_state():
    if RESTORE_STATE and os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
                _state.update(data)
                logger.info("state_restored", extra={"state": _state})
        except Exception as e:
            logger.warning("state_restore_failed %s", e)

def save_state():
    if not RESTORE_STATE:
        return
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(_state, f)
    except Exception as e:
        logger.warning("state_save_failed %s", e)

load_state()

def cooldown_active() -> bool:
    with _state_lock:
        ts = _state.get("last_buy_ts")
    if ts is None or BUY_COOL_SEC <= 0:
        return False
    return (time.time() - float(ts)) < BUY_COOL_SEC

def mark_buy_now():
    with _state_lock:
        _state["last_buy_ts"] = time.time()
    save_state()

def mark_sell_now():
    with _state_lock:
        _state["last_sell_ts"] = time.time()
    save_state()

# ───────────────────────────────── CCXT

_exchange: Optional[ccxt.Exchange] = None
_markets_loaded = False
_markets_lock = threading.Lock()

def get_exchange() -> ccxt.Exchange:
    global _exchange, _markets_loaded
    if _exchange is None:
        cls = getattr(ccxt, EXCHANGE_ID)
        _exchange = cls({
            "apiKey": API_KEY,
            "secret": API_SECRET,
            "enableRateLimit": True,
            "timeout": 20000,
        })
    with _markets_lock:
        if not _markets_loaded:
            _exchange.load_markets()
            _markets_loaded = True
    return _exchange

def market_symbol_for_ccxt(ex: ccxt.Exchange, sym: str) -> str:
    """Laisse ccxt mapper BTC/EUR -> marché Kraken (XXBT/EUR ou XXBTZEUR)"""
    sym_norm = normalize_symbol(sym)
    # ccxt se débrouille avec BTC/EUR; s'il n'existe pas, on tente XBT/EUR
    if sym_norm in ex.markets:
        return sym_norm
    alt = sym_norm.replace("BTC", "XBT")
    if alt in ex.markets:
        return alt
    # dernier recours : essaie de split et retrouver id
    base, quote = sym_norm.split("/")
    for m in ex.markets:
        b = ex.markets[m].get("base", "")
        q = ex.markets[m].get("quote", "")
        if b in {base, "XBT"} and q in {quote, "ZEUR"}:
            return ex.markets[m]["symbol"]
    return sym_norm  # on tente quand même

def fetch_balances(ex: ccxt.Exchange) -> Dict[str, float]:
    bal = ex.fetch_balance()
    # Kraken: 'total' & 'free' clés; certaines quotes ont préfixe Z
    result = {}
    for k, v in (bal.get("free") or {}).items():
        result[k.upper()] = float(v or 0.0)
    # Harmonise EUR/ZEUR, BTC/XBT
    if "ZEUR" in result:
        result["EUR"] = result.get("EUR", 0.0) + result["ZEUR"]
    if "XXBT" in result:
        result["BTC"] = result.get("BTC", 0.0) + result["XXBT"]
    if "XBT" in result:
        result["BTC"] = result.get("BTC", 0.0) + result["XBT"]
    return result

# ───────────────────────────────── Core calc

def compute_buy_quote(payload: Dict[str, Any], free_eur: float) -> float:
    # priorité : payload.quote > FIXED_QUOTE_PER_TRADE > RISK_PCT * free
    q = payload.get("quote")
    if q is not None:
        try:
            q = float(q)
        except Exception:
            q = None
    if q is None or q <= 0:
        q = FIXED_QUOTE_PER_TRADE if FIXED_QUOTE_PER_TRADE > 0 else (free_eur * RISK_PCT)
    return float(q)

def chunks(n: int) -> int:
    return max(1, int(n))

# ───────────────────────────────── Flask

app = Flask(__name__)

@app.get("/")
def index():
    cfg = {
        "ok": True,
        "exchange": EXCHANGE_ID,
        "symbol_env": SYMBOL_ENV,
        "allow_payload_symbol": ALLOW_PAYLOAD_SYMBOL,
        "order_type": ORDER_TYPE,
        "fixed_quote": FIXED_QUOTE_PER_TRADE,
        "min_quote": MIN_QUOTE_PER_TRADE,
        "risk_pct": RISK_PCT,
        "fee_buffer_pct": FEE_BUFFER_PCT,
        "buy_split_chunks": BUY_SPLIT_CHUNKS,
        "buy_cool_sec": BUY_COOL_SEC,
        "dry_run": DRY_RUN,
        "state": _state,
    }
    return jsonify({"ok": True, "config": cfg}), 200

@app.get("/health")
def health():
    return "ok", 200

# ───────────────────────────────── Webhook

@app.post("/webhook")
def webhook():
    # Sécurité
    try:
        payload = request.get_json(force=True, silent=False)
    except Exception:
        return jsonify({"ok": False, "error": "invalid_json"}), 400

    secret = str(payload.get("secret", ""))
    if WEBHOOK_SECRET and secret != WEBHOOK_SECRET:
        logger.warning("Secret KO: Webhook secret invalide")
        return jsonify({"ok": False, "error": "forbidden"}), 401

    signal = str(payload.get("signal", "")).upper().strip()
    sym_in = normalize_symbol(payload.get("symbol") or SYMBOL_ENV)
    sym_use = sym_in if ALLOW_PAYLOAD_SYMBOL else normalize_symbol(SYMBOL_ENV)

    logger.info("tv-kraken:Webhook payload: %s", json.dumps(payload, ensure_ascii=False))

    try:
        ex = get_exchange()
        sym_ccxt = market_symbol_for_ccxt(ex, sym_use)
        ticker = ex.fetch_ticker(sym_ccxt)
        ask = float(ticker.get("ask") or ticker.get("last") or ticker.get("close"))
        bid = float(ticker.get("bid") or ticker.get("last") or ticker.get("close"))
        balances = fetch_balances(ex)
    except Exception as e:
        logger.exception("market_or_balance_failed")
        return jsonify({"ok": False, "error": f"market_or_balance_failed: {e}"}), 500

    base, quote = normalize_symbol(sym_use).split("/")
    base_ccy = "BTC" if base in {"BTC", "XBT", "XXBT"} else base
    quote_ccy = "EUR" if quote in {"EUR", "ZEUR"} else quote

    # ───────────────── BUY
    if signal == "BUY":
        # Cooldown
        if cooldown_active():
            logger.info("skip_buy | reason=cooldown_active | cooldown_sec=%s", BUY_COOL_SEC)
            return jsonify({"ok": True, "skipped": "cooldown_active"}), 200

        free_eur = balances.get("EUR", 0.0) if quote_ccy == "EUR" else balances.get(quote_ccy, 0.0)
        quote_amt = compute_buy_quote(payload, free_eur)
        if quote_amt < MIN_QUOTE_PER_TRADE:
            logger.info("skip_buy | reason=below_min_quote | quote=%s min=%s", quote_amt, MIN_QUOTE_PER_TRADE)
            return jsonify({"ok": True, "skipped": "below_min_quote"}), 200

        need_eur = quote_amt * (1.0 + FEE_BUFFER_PCT) + QUOTE_RESERVE
        if free_eur < need_eur:
            logger.info("skip_buy | reason=not_enough_quote | free=%s need=%s", free_eur, need_eur)
            return jsonify({"ok": True, "skipped": "not_enough_quote"}), 200

        # Split en N morceaux
        n = chunks(BUY_SPLIT_CHUNKS)
        per_q = quote_amt / n
        placed = []
        logger.info("state=buy_prepare | symbol=%s | chunks=%s | per_quote=%s | ask=%s", sym_ccxt, n, per_q, ask)

        if DRY_RUN:
            mark_buy_now()
            logger.info("dry_run_buy | symbol=%s | total_quote=%s", sym_ccxt, quote_amt)
            return jsonify({"ok": True, "dry_run": True, "action": "BUY", "symbol": sym_ccxt, "quote": quote_amt}), 200

        for i in range(n):
            # calc amount
            raw_amount = per_q / ask
            amount = float(ex.amount_to_precision(sym_ccxt, raw_amount))
            if amount <= 0:
                logger.info("skip_buy | reason=amount_to_precision_zero | raw=%s", raw_amount)
                continue
            try:
                logger.info("create_order | BUY | i=%s/%s | amount=%s", i+1, n, amount)
                order = ex.create_order(sym_ccxt, "market", "buy", amount)
                placed.append(order)
                time.sleep(BUY_SPLIT_DELAY_MS / 1000.0 if i < n-1 else 0)
            except Exception as e:
                logger.exception("create_order_failed_buy")
                return jsonify({"ok": False, "error": f"create_order_failed_buy: {e}"}), 500

        mark_buy_now()
        logger.info("order_result_buy | count=%s | data=%s", len(placed), json.dumps(placed))
        return jsonify({"ok": True, "action": "BUY", "orders": placed}), 200

    # ───────────────── SELL
    if signal == "SELL":
        # Montant dispo en base
        # Kraken peut exposer BTC sous BTC/XXBT/XBT : on aggrège
        free_base = (
            balances.get(base_ccy, 0.0)
            + balances.get("XBT", 0.0)
            + balances.get("XXBT", 0.0)
        ) if base_ccy == "BTC" else balances.get(base_ccy, 0.0)

        force_close = bool(payload.get("force_close", True))
        if not force_close:
            logger.info("info_sell | force_close=false (on vendra au plus proche du free-base)")
        sell_amt = max(0.0, free_base - BASE_RESERVE)
        if sell_amt <= 0:
            logger.info("skip_sell | reason=no_base_amount | free_base=%s reserve=%s", free_base, BASE_RESERVE)
            return jsonify({"ok": True, "skipped": "no_base_amount"}), 200

        n = chunks(SELL_SPLIT_CHUNKS)
        per_amt = sell_amt / n
        placed = []
        logger.info("state=sell_prepare | symbol=%s | chunks=%s | per_amount=%s | bid=%s", sym_ccxt, n, per_amt, bid)

        if DRY_RUN:
            mark_sell_now()
            logger.info("dry_run_sell | symbol=%s | total_amount=%s", sym_ccxt, sell_amt)
            return jsonify({"ok": True, "dry_run": True, "action": "SELL", "symbol": sym_ccxt, "amount": sell_amt}), 200

        for i in range(n):
            amt = float(ex.amount_to_precision(sym_ccxt, per_amt))
            if amt <= 0:
                logger.info("skip_sell | reason=amount_to_precision_zero | raw=%s", per_amt)
                continue
            try:
                logger.info("create_order | SELL | i=%s/%s | amount=%s", i+1, n, amt)
                order = ex.create_order(sym_ccxt, "market", "sell", amt)
                placed.append(order)
                # Pas de délai nécessaire mais on peut en mettre si besoin
            except Exception as e:
                logger.exception("create_order_failed_sell")
                return jsonify({"ok": False, "error": f"create_order_failed_sell: {e}"}), 500

        mark_sell_now()
        logger.info("order_result_sell | count=%s | data=%s", len(placed), json.dumps(placed))
        return jsonify({"ok": True, "action": "SELL", "orders": placed}), 200

    # ───────────────── PING / Autres
    if signal == "PING":
        return jsonify({"ok": True, "pong": True, "ts": time.time()}), 200

    return jsonify({"ok": False, "error": "unknown_signal"}), 400


# ───────────────────────────────── Entrypoint (Render lit PORT)
if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
