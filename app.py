# app.py  — TV → Kraken (ccxt)  • maker BUY (postOnly) + TTL/reprice/fallback • SELL market
# ─────────────────────────────────────────────────────────────────────────────────────────
import os, time, json, math, logging, hashlib, hmac
from typing import Tuple, Dict, Any
from flask import Flask, request, jsonify
import ccxt

# ──────────────── ENV & CONFIG
PORT            = int(os.getenv("PORT", "8000"))
LOG_LEVEL       = os.getenv("LOG_LEVEL", "INFO").upper()
WEBHOOK_SECRET  = os.getenv("WEBHOOK_SECRET", "TVRENDERKR4K3N93!")
DEFAULT_SYMBOL  = os.getenv("DEFAULT_SYMBOL", "XBT/EUR")

# Déduplication (éviter double traitement si double alerte TV)
DEDUPE_TTL_SEC  = float(os.getenv("DEDUPE_TTL_SEC", "3"))

# ccxt options
CCXT_TIMEOUT_MS = int(os.getenv("CCXT_TIMEOUT_MS", "20000"))  # 20s
RATE_LIMIT      = True

# Maker defaults (peuvent être surchargés par le JSON Pine)
MAKER_OFFSET_BPS = float(os.getenv("MAKER_OFFSET_BPS", "3.0"))
MAKER_TTL_SEC    = int(os.getenv("MAKER_TTL_SEC", "10"))
MAKER_REPRICE    = int(os.getenv("MAKER_REPRICE", "2"))
MAKER_FALLBACK   = os.getenv("MAKER_FALLBACK", "market").lower()  # "market" | "none"

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("tv-kraken")

# ──────────────── CCXT init
def new_exchange() -> ccxt.Exchange:
    ex = ccxt.kraken({
        "apiKey": os.getenv("KRAKEN_KEY", ""),
        "secret": os.getenv("KRAKEN_SECRET", ""),
        "enableRateLimit": RATE_LIMIT,
        "timeout": CCXT_TIMEOUT_MS,
        "options": {"adjustForTimeDifference": True},
    })
    ex.load_markets()
    return ex

ex = new_exchange()

# ──────────────── Utils marché
def market_limits(symbol: str) -> Dict[str, float]:
    m = ex.market(symbol)
    lim   = m.get("limits", {}) or {}
    prec  = m.get("precision", {}) or {}
    return {
        "min_cost": float((lim.get("cost") or {}).get("min") or 0.0),
        "min_amt":  float((lim.get("amount") or {}).get("min") or 0.0),
        "p_amount": int(prec.get("amount", 8) or 8),
        "p_price":  int(prec.get("price",  2) or 2),
    }

def best_bid_ask(symbol: str) -> Tuple[float, float]:
    # robuste: tente order book puis fallback ticker
    for _ in range(3):
        try:
            ob = ex.fetch_order_book(symbol, 5)
            bid = ob["bids"][0][0] if ob["bids"] else None
            ask = ob["asks"][0][0] if ob["asks"] else None
            if bid is None or ask is None:
                t = ex.fetch_ticker(symbol)
                bid = float(t["bid"])
                ask = float(t["ask"])
            return float(bid), float(ask)
        except ccxt.NetworkError as e:
            log.warning(f"best_bid_ask retry: {e}")
            time.sleep(0.7)
    # dernier essai "brut"
    t = ex.fetch_ticker(symbol)
    return float(t["bid"]), float(t["ask"])

def round_amt(symbol: str, amount: float) -> float:
    return float(ex.amount_to_precision(symbol, amount))

def round_price(symbol: str, price: float) -> float:
    return float(ex.price_to_precision(symbol, price))

def notional_ok(symbol: str, price: float, amount: float) -> bool:
    lim = market_limits(symbol)
    cost = float(price) * float(amount)
    return (lim["min_cost"] == 0 or cost >= lim["min_cost"]) and (lim["min_amt"] == 0 or amount >= lim["min_amt"])

def fetch_free(code: str) -> float:
    # 3 essais réseau
    for _ in range(3):
        try:
            bal = ex.fetch_free_balance()
            return float(bal.get(code, 0) or 0.0)
        except ccxt.NetworkError as e:
            log.warning(f"fetch_free retry: {e}")
            time.sleep(0.8)
    bal = ex.fetch_free_balance()
    return float(bal.get(code, 0) or 0.0)

# ──────────────── Dédup HMAC
_LAST_SEEN: Dict[str, float] = {}

def dedup_key(payload: Dict[str, Any]) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hmac.new(b"dedup", body.encode(), hashlib.sha256).hexdigest()[:16]
    sym = payload.get("symbol", "?")
    sig = payload.get("signal", "?")
    return f"{sig}|{sym}|{digest}"

def is_duplicate(payload: Dict[str, Any]) -> bool:
    k = dedup_key(payload)
    now = time.time()
    last = _LAST_SEEN.get(k, 0)
    if (now - last) < DEDUPE_TTL_SEC:
        return True
    _LAST_SEEN[k] = now
    return False

# ──────────────── Exécution
def place_market(symbol: str, side: str, quote_eur: float = None, amount: float = None):
    bid, ask = best_bid_ask(symbol)
    px = ask if side == "buy" else bid
    if amount is None:
        if quote_eur is None or quote_eur <= 0:
            raise ValueError("amount or quote_eur required for market order")
        amount = float(quote_eur) / float(px)
    amount = round_amt(symbol, amount)
    if not notional_ok(symbol, px, amount):
        raise ValueError("dust_too_small_or_below_min_notional")
    log.info(f"create_market_order side={side} amt={amount} px≈{px:.2f} sym={symbol}")
    return ex.create_market_order(symbol, side, amount)

def place_limit_maker_buy(symbol: str, quote_eur: float, maker_cfg: Dict[str, Any]):
    """
    TRY: postOnly limit under bid with offset(bps) • wait TTL • cancel • reprice (N) • fallback (market|none)
    maker_cfg keys: post_only(bool), offset_bps(float), ttl_sec(int), reprice_retries(int), fallback(str)
    """
    post_only  = bool(maker_cfg.get("post_only", True))
    offset_bps = float(maker_cfg.get("offset_bps", MAKER_OFFSET_BPS))
    ttl_sec    = int(maker_cfg.get("ttl_sec", MAKER_TTL_SEC))
    retries    = int(maker_cfg.get("reprice_retries", MAKER_REPRICE))
    fallback   = str(maker_cfg.get("fallback", MAKER_FALLBACK)).lower()

    bid, _ask = best_bid_ask(symbol)
    price  = round_price(symbol, bid * (1.0 - offset_bps/10000.0))
    amount = round_amt(symbol, float(quote_eur) / float(price))
    if not post_only:
        # si pas postOnly, autant utiliser direct un limit “classique” (peu d'intérêt ici)
        return ex.create_limit_buy_order(symbol, amount, price)

    if not notional_ok(symbol, price, amount):
        raise ValueError("dust_too_small_or_below_min_notional (maker)")

    params = {"postOnly": True, "timeInForce": "GTC"}
    log.info(f"maker BUY sym={symbol} amt={amount} @ {price:.2f} (offset {offset_bps}bps) TTL={ttl_sec}s retries={retries}")
    order = ex.create_order(symbol, "limit", "buy", amount, price, params)

    deadline = time.time() + ttl_sec
    while time.time() < deadline:
        o = ex.fetch_order(order["id"], symbol)
        st = (o.get("status") or "").lower()
        if st in ("closed", "filled"):
            log.info(f"maker filled id={o['id']} filled={o.get('filled')} cost={o.get('cost')}")
            return o
        time.sleep(1.0)

    # pas rempli → cancel + reprice
    try:
        ex.cancel_order(order["id"], symbol)
    except Exception as e:
        log.warning(f"cancel maker failed (maybe already canceled): {e}")

    if retries > 0:
        maker_cfg2 = dict(maker_cfg)
        maker_cfg2["reprice_retries"] = retries - 1
        return place_limit_maker_buy(symbol, quote_eur, maker_cfg2)

    if fallback == "market":
        log.info("maker not filled → fallback MARKET BUY")
        return place_market(symbol, "buy", quote_eur=quote_eur)

    raise RuntimeError("maker not filled and no fallback")

def force_close_all(symbol: str):
    base = ex.market(symbol)["base"]  # e.g. "XBT"
    free_amt = fetch_free(base)
    if free_amt <= 0:
        return {"info": "nothing_to_sell", "amount": 0}
    free_amt = round_amt(symbol, free_amt)
    log.info(f"force_close SELL all free {base} → {free_amt}")
    return ex.create_market_order(symbol, "sell", free_amt)

# ──────────────── Flask App
app = Flask(__name__)

@app.get("/health")
def health():
    return jsonify({"ok": True})

@app.get("/ready")
def ready():
    try:
        ex.fetch_time()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 503

@app.get("/version")
def version():
    return jsonify({
        "ccxt": ccxt.__version__,
        "exchange": "kraken",
        "symbol_default": DEFAULT_SYMBOL,
        "rate_limit": RATE_LIMIT,
        "timeout_ms": CCXT_TIMEOUT_MS
    })

@app.post("/webhook")
def webhook():
    # 1) parse JSON
    try:
        payload = request.get_json(force=True, silent=False)
    except Exception as e:
        log.error(f"bad_json: {e}")
        return jsonify({"ok": False, "error": "bad_json"}), 400

    # 2) auth
    if not payload or payload.get("secret") != WEBHOOK_SECRET:
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    # 3) dedup
    if is_duplicate(payload):
        log.info(f"dedup drop {dedup_key(payload)}")
        return jsonify({"ok": True, "dedup": True})

    # 4) fields
    signal   = (payload.get("signal") or "").upper()
    symbol   = payload.get("symbol") or DEFAULT_SYMBOL
    order_ty = (payload.get("type") or "market").lower()
    quote    = float(payload.get("quote", 0) or 0)
    amount   = payload.get("amount")
    amount   = float(amount) if amount is not None else None
    maker    = payload.get("maker") or {}
    simulate = bool(payload.get("simulate", False))
    force_cl = bool(payload.get("force_close", False))

    # 5) simulate (dry-run)
    if simulate:
        return jsonify({
            "ok": True, "simulate": True,
            "would": {"signal": signal, "symbol": symbol, "type": order_ty, "quote": quote, "amount": amount, "maker": maker}
        })

    # 6) execute
    try:
        if signal == "BUY":
            if order_ty == "limit_maker" and maker.get("post_only", True):
                if quote <= 0:
                    raise ValueError("quote required for limit_maker BUY")
                res = place_limit_maker_buy(symbol, quote, maker)
            else:
                # simple market BUY
                if amount is None and quote <= 0:
                    raise ValueError("amount or quote required for market BUY")
                res = place_market(symbol, "buy", quote_eur=quote, amount=amount)

        elif signal == "SELL":
            if force_cl:
                res = force_close_all(symbol)
            else:
                # simple market SELL — amount obligatoire si pas force_close
                if amount is None:
                    raise ValueError("amount required for SELL (or set force_close=true)")
                res = place_market(symbol, "sell", amount=amount)

        else:
            return jsonify({"ok": False, "error": "unknown_signal"}), 400

        return jsonify({"ok": True, "order": res})

    except ValueError as ve:
        log.error(f"validation error: {ve}")
        return jsonify({"ok": False, "error": str(ve)}), 400
    except ccxt.BaseError as ce:
        # NetworkError, InvalidOrder, InsufficientFunds, etc.
        log.error(f"ccxt error: {type(ce).__name__}: {ce}")
        return jsonify({"ok": False, "error": f"ccxt:{type(ce).__name__}", "detail": str(ce)}), 502
    except Exception as e:
        log.exception("unhandled")
        return jsonify({"ok": False, "error": "internal", "detail": str(e)}), 500

# ──────────────── Runner
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
