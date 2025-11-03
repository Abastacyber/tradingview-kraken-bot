import os, json, time, logging, threading
from flask import Flask, request, jsonify
import ccxt

# ───────────────────────────── Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL,
                    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("tv-kraken")

# ───────────────────────────── Env / constants
EXCHANGE              = os.getenv("EXCHANGE", "kraken")
SYMBOL                = os.getenv("SYMBOL", "BTC/EUR")
ALLOW_PAYLOAD_SYMBOL  = os.getenv("ALLOW_PAYLOAD_SYMBOL", "true").lower() == "true"

BASE_RESERVE          = float(os.getenv("BASE_RESERVE", "0"))
QUOTE_RESERVE         = float(os.getenv("QUOTE_RESERVE", "0"))
FEE_BUFFER_PCT        = float(os.getenv("FEE_BUFFER_PCT", "0.0015"))

FIXED_QUOTE_PER_TRADE = float(os.getenv("FIXED_QUOTE_PER_TRADE", "50"))
MIN_QUOTE_PER_TRADE   = float(os.getenv("MIN_QUOTE_PER_TRADE", "10"))

BUY_SPLIT_CHUNKS      = int(os.getenv("BUY_SPLIT_CHUNKS", "1"))
BUY_SPLIT_DELAY_MS    = int(os.getenv("BUY_SPLIT_DELAY_MS", "300"))
SELL_SPLIT_CHUNKS     = int(os.getenv("SELL_SPLIT_CHUNKS", "1"))

ORDER_TYPE            = os.getenv("ORDER_TYPE", "market").lower()
DRY_RUN               = os.getenv("DRY_RUN", "false").lower() == "true"

BUY_COOL_SEC          = int(os.getenv("BUY_COOL_SEC", "180"))
RESTORE_ON_START      = os.getenv("RESTORE_ON_START", "true").lower() == "true"
STATE_FILE            = os.getenv("STATE_FILE", "/tmp/bot_state.json")
WEBHOOK_SECRET        = os.getenv("WEBHOOK_SECRET", "change-me")

# Anti-DDoS / cache / timings
PRIVATE_MIN_INTERVAL_MS   = int(os.getenv("PRIVATE_MIN_INTERVAL_MS", "1200"))
BAL_CACHE_TTL_SEC         = int(os.getenv("BAL_CACHE_TTL_SEC", "8"))
SELL_DUST_COOLDOWN_SEC    = int(os.getenv("SELL_DUST_COOLDOWN_SEC", "60"))
DDOS_LOCKOUT_COOLDOWN_SEC = int(os.getenv("DDOS_LOCKOUT_COOLDOWN_SEC", "90"))

# Anti-doublon d’alertes (même side+symbol reçus à la suite)
DEDUP_WINDOW_SEC       = int(os.getenv("DEDUP_WINDOW_SEC", "3"))

# Trailing (placeholders si utilisé par ton Pine)
TRAILING_ENABLED       = os.getenv("TRAILING_ENABLED", "true").lower() == "true"
TRAIL_ACTIVATE_PCT_CONF2 = float(os.getenv("TRAIL_ACTIVITE_PCT_CONF2", "0.003"))
TRAIL_ACTIVATE_PCT_CONF3 = float(os.getenv("TRAIL_ACTIVITE_PCT_CONF3", "0.005"))
TRAIL_GAP_CONF2          = float(os.getenv("TRAIL_GAP_CONF2", "0.0004"))
TRAIL_GAP_CONF3          = float(os.getenv("TRAIL_GAP_CONF3", "0.003"))

# ───────────────────────────── Flask
app = Flask(__name__)

# ───────────────────────────── Exchange init (une instance globale)
def new_exchange():
    apiKey = os.getenv("KRAKEN_API_KEY")
    secret = os.getenv("KRAKEN_API_SECRET")
    opts = {
        "apiKey": apiKey,
        "secret": secret,
        "enableRateLimit": True,
        "rateLimit": 1000,
        "options": {"adjustForTimeDifference": True}
    }
    ex = ccxt.kraken(opts)
    ex.load_markets()
    return ex

ex = new_exchange()

# ───────────────────────────── Anti-burst / cache / state
_last_private_call = 0.0
_private_lock = threading.Lock()
LOCKED_UNTIL = 0.0

BAL_CACHE = {"ts": 0.0, "free": {}}
LAST_DUST_SELL = {}        # symbol -> last timestamp (skip dust repeatedly)
LAST_BUY_TS = 0.0
LAST_ORDER_SEEN = {}       # anti-doublon: key = f"{signal}:{symbol}" -> ts

def _throttle_private():
    global _last_private_call
    with _private_lock:
        now = time.time()
        wait = (_last_private_call + (PRIVATE_MIN_INTERVAL_MS/1000.0)) - now
        if wait > 0:
            time.sleep(wait)
        _last_private_call = time.time()

def _set_lockout():
    global LOCKED_UNTIL
    LOCKED_UNTIL = time.time() + DDOS_LOCKOUT_COOLDOWN_SEC

def _locked_now():
    return time.time() < LOCKED_UNTIL

def invalidate_balance_cache():
    BAL_CACHE["ts"] = 0.0
    BAL_CACHE["free"] = {}

def fetch_free_balance_cached():
    now = time.time()
    if BAL_CACHE["free"] and (now - BAL_CACHE["ts"] <= BAL_CACHE_TTL_SEC):
        return BAL_CACHE["free"]
    _throttle_private()
    bal = ex.fetch_free_balance()
    BAL_CACHE["free"] = bal
    BAL_CACHE["ts"] = time.time()
    return bal

def market_min_amount(symbol: str) -> float:
    m = ex.markets.get(symbol)
    if not m:
        ex.load_markets()
        m = ex.markets.get(symbol)
    v = (m or {}).get("limits", {}).get("amount", {}).get("min")
    # fallback raisonnable pour BTC/EUR Kraken
    return float(v) if v else 0.00005

def ticker_price(symbol: str) -> float:
    t = ex.fetch_ticker(symbol)
    price = t.get("last") or ((t.get("bid", 0) + t.get("ask", 0)) / 2.0)
    return float(price)

def chunk_iter(n):
    for i in range(n):
        yield i

def normalize_symbol(raw_sym: str) -> str:
    """
    Normalise BTC/XBT pour Kraken via ccxt.
    Essaye 'BTC/QUOTE', sinon 'XBT/QUOTE'.
    """
    s = (raw_sym or "").upper().replace(" ", "")
    if "/" not in s:
        return s
    s_btc = s.replace("XBT", "BTC")
    if s_btc in ex.markets:
        return s_btc
    s_xbt = s_btc.replace("BTC", "XBT")
    if s_xbt in ex.markets:
        return s_xbt
    # reload au cas où
    ex.load_markets()
    if s_btc in ex.markets:
        return s_btc
    if s_xbt in ex.markets:
        return s_xbt
    return s  # on tente tel quel

# ───────────────────────────── Helpers order
def place_order(side, symbol, amount, price=None, order_type="market"):
    if DRY_RUN:
        log.info("DRY_RUN | %s %s amount=%s price=%s type=%s", side, symbol, amount, price, order_type)
        return {"id": "dry-run", "status": "ok"}

    params = {}
    if order_type == "market":
        return ex.create_market_order(symbol, side, amount, params)
    else:
        return ex.create_order(symbol, order_type, side, amount, price, params)

# ───────────────────────────── Routes
@app.get("/health")
def health():
    return jsonify({"status": "ok"}), 200

@app.post("/webhook")
def webhook():
    global LAST_BUY_TS

    if _locked_now():
        left = int(LOCKED_UNTIL - time.time())
        log.warning("locked_out | encore %ss | on ignore ce webhook", max(0, left))
        return jsonify({"status": "locked_out", "left_sec": max(0, left)}), 200

    try:
        payload = request.get_json(force=True, silent=False)
        log.debug("Webhook payload: %s", json.dumps(payload, ensure_ascii=False))

        if not payload or payload.get("secret") != WEBHOOK_SECRET:
            log.warning("bad_secret")
            return jsonify({"error": "bad secret"}), 401

        signal   = payload.get("signal", "").upper()
        tf       = payload.get("timeframe")  # non utilisé ici mais conservé pour logs
        reason   = payload.get("reason")
        px_in    = payload.get("price")
        ts       = payload.get("timestamp")
        force_close = bool(payload.get("force_close", False))

        raw_sym = (payload.get("symbol") if (ALLOW_PAYLOAD_SYMBOL and payload.get("symbol")) else SYMBOL)
        sym = normalize_symbol(raw_sym)
        if raw_sym != sym:
            log.debug("Symbol normalized | raw=%s -> sym=%s", raw_sym, sym)

        # Anti-doublon ultra rapproché (même signal + même symbole)
        now = time.time()
        key = f"{signal}:{sym}"
        last = LAST_ORDER_SEEN.get(key, 0.0)
        if (now - last) < DEDUP_WINDOW_SEC:
            left = int(DEDUP_WINDOW_SEC - (now - last))
            log.info("skip_dedup | signal=%s sym=%s | delta=%.2fs < %ss",
                     signal, sym, (now - last), DEDUP_WINDOW_SEC)
            return jsonify({"status": "skip", "reason": "dedup", "left_sec": max(0, left)}), 200
        LAST_ORDER_SEEN[key] = now

        # ───── SELL path ─────
        if signal == "SELL":
            log.info("sell_prepare | symbol=%s | force_close=%s", sym, force_close)

            # Anti-dust repeat cooldown
            last_skip = LAST_DUST_SELL.get(sym, 0.0)
            if (time.time() - last_skip) < SELL_DUST_COOLDOWN_SEC:
                left = int(SELL_DUST_COOLDOWN_SEC - (time.time() - last_skip))
                log.info("skip_sell | reason=dust_cooldown | left=%ss", left)
                return jsonify({"status": "skip", "reason": "dust_cooldown", "left_sec": left}), 200

            # Solde base disponible
            free = fetch_free_balance_cached()
            base_ccy = sym.split("/")[0].replace("XBT", "BTC")
            base_free = float(free.get(base_ccy, 0.0))

            min_amount = market_min_amount(sym)
            qty = max(0.0, base_free - BASE_RESERVE)

            log.info("sell_check | base_free=%.10f reserve=%.10f min_amount=%.10f qty=%.10f",
                     base_free, BASE_RESERVE, min_amount, qty)

            if qty < min_amount:
                LAST_DUST_SELL[sym] = time.time()
                log.info("skip_sell | reason=dust_too_small | qty=%.10f < min_amount=%.10f",
                         qty, min_amount)
                return jsonify({"status": "skip", "reason": "dust_too_small",
                                "qty": qty, "min": min_amount}), 200

            # Split éventuel
            chunks = max(1, SELL_SPLIT_CHUNKS)
            per = qty / chunks
            for i in chunk_iter(chunks):
                _throttle_private()
                place_order("sell", sym, per, order_type=ORDER_TYPE)
                if i < chunks - 1:
                    time.sleep(BUY_SPLIT_DELAY_MS/1000.0)

            # Invalider le cache solde pour éviter un 2e SELL avec ancien solde
            invalidate_balance_cache()

            log.info("sell_done | qty=%.10f chunks=%d", qty, chunks)
            return jsonify({"status": "ok", "action": "sell", "qty": qty}), 200

        # ───── BUY path ─────
        if signal == "BUY":
            now = time.time()
            if (now - LAST_BUY_TS) < BUY_COOL_SEC:
                left = int(BUY_COOL_SEC - (now - LAST_BUY_TS))
                log.info("skip_buy | reason=cooldown | left=%ss", left)
                return jsonify({"status": "skip", "reason": "cooldown", "left_sec": left}), 200

            quote_budget = float(payload.get("quote") or FIXED_QUOTE_PER_TRADE)
            if quote_budget < MIN_QUOTE_PER_TRADE:
                log.info("skip_buy | reason=below_min_quote | quote=%.2f < %.2f",
                         quote_budget, MIN_QUOTE_PER_TRADE)
                return jsonify({"status": "skip", "reason": "below_min_quote"}), 200

            px = float(px_in or ticker_price(sym))
            min_amount = market_min_amount(sym)

            net_quote = max(0.0, quote_budget - QUOTE_RESERVE)
            net_quote *= (1 - FEE_BUFFER_PCT)
            qty = net_quote / px

            if qty < min_amount:
                log.info("skip_buy | reason=amount_below_min | qty=%.8f < min=%.8f",
                         qty, min_amount)
                return jsonify({"status": "skip", "reason": "amount_below_min",
                                "qty": qty, "min": min_amount}), 200

            chunks = max(1, BUY_SPLIT_CHUNKS)
            per = qty / chunks
            for i in chunk_iter(chunks):
                _throttle_private()
                place_order("buy", sym, per, order_type=ORDER_TYPE)
                if i < chunks - 1:
                    time.sleep(BUY_SPLIT_DELAY_MS/1000.0)

            LAST_BUY_TS = time.time()
            invalidate_balance_cache()

            log.info("buy_done | qty=%.8f chunks=%d price=%.2f", qty, chunks, px)
            return jsonify({"status": "ok", "action": "buy", "qty": qty, "price": px}), 200

        # ───── PING / autres ─────
        if signal == "PING":
            return jsonify({"status": "pong"}), 200

        return jsonify({"status": "ignored", "detail": "unknown signal"}), 200

    except (ccxt.DDoSProtection, ccxt.RateLimitExceeded) as e:
        log.error("DDoS/RateLimit | %s", e)
        _set_lockout()
        return jsonify({"status": "locked_out", "detail": str(e)}), 200

    except ccxt.ExchangeError as e:
        msg = str(e)
        if "Temporary lockout" in msg:
            log.error("Kraken lockout | %s", msg)
            _set_lockout()
            return jsonify({"status": "locked_out", "detail": "kraken_temporary_lockout"}), 200
        log.exception("ExchangeError")
        return jsonify({"error": "exchange_error", "detail": msg}), 200

    except Exception as e:
        log.exception("Webhook error")
        return jsonify({"error": "webhook_exception", "detail": str(e)}), 500

# ───────────────────────────── Run local (Render utilise gunicorn en prod)
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
