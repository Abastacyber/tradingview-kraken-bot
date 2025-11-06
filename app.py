# app.py
import os, json, time, math, hashlib, logging, traceback
from typing import Tuple, Dict, Any
from flask import Flask, request, jsonify
import ccxt

# ───────────── Logging ─────────────
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("tv-kraken")

# ───────────── Config ─────────────
TV_SECRET       = os.environ.get("TV_WEBHOOK_SECRET", "")
DEFAULT_EUR_2   = float(os.environ.get("DEFAULT_EUR_2", "25"))
DEFAULT_EUR_3   = float(os.environ.get("DEFAULT_EUR_3", "35"))
SYMBOL_FALLBACK = os.environ.get("SYMBOL_FALLBACK", "").strip()  # ex "XBT/EUR"

if not os.environ.get("KRAKEN_KEY") or not os.environ.get("KRAKEN_SECRET"):
    log.warning("KRAKEN api keys are missing in env!")

if not TV_SECRET:
    log.warning("TV_WEBHOOK_SECRET is empty. Webhook will reject payloads.")

# ───────────── Exchange ─────────────
def make_exchange() -> ccxt.Exchange:
    ex = ccxt.kraken({
        "apiKey": os.environ.get("KRAKEN_KEY"),
        "secret": os.environ.get("KRAKEN_SECRET"),
        "enableRateLimit": True,
        "timeout": 20000,
        "options": {
            "adjustForTimeDifference": True,
        },
    })
    ex.load_markets()
    return ex

EXCHANGE = make_exchange()

# ───────────── Helpers ─────────────
def normalize_symbol_for_kraken(sym: str) -> str:
    """Map TV symbol to Kraken symbol."""
    if not sym:
        return SYMBOL_FALLBACK or "XBT/EUR"
    s = sym.upper().replace("BTC/", "XBT/")
    # Sécurise quelques alias courants
    s = s.replace("DOGE/", "XDG/") if "DOGE/" in s else s
    if s not in EXCHANGE.markets:
        # tente la fallback si fournie
        if SYMBOL_FALLBACK and SYMBOL_FALLBACK in EXCHANGE.markets:
            log.warning(f"Symbol {s} not found. Using fallback {SYMBOL_FALLBACK}")
            return SYMBOL_FALLBACK
        # dernière chance : BTC/EUR
        if "XBT/EUR" in EXCHANGE.markets:
            log.warning(f"Symbol {s} not found. Using XBT/EUR")
            return "XBT/EUR"
        raise ccxt.BadSymbol(f"kraken has no market symbol {s}")
    return s

def market_info(sym: str) -> Dict[str, Any]:
    m = EXCHANGE.market(sym)
    # Certaines versions de ccxt remontent limits.cost.min, sinon None
    min_cost = None
    if "limits" in m and m["limits"]:
        lc = m["limits"].get("cost") if m["limits"] else None
        if lc and lc.get("min"):
            min_cost = float(lc["min"])
    return {
        "precision": m.get("precision", {}),
        "limits": m.get("limits", {}),
        "min_cost": min_cost,  # peut être None
    }

def round_step(value: float, step: float) -> float:
    if step is None or step == 0:
        return value
    return math.floor(value / step) * step

def quantize_amount(sym: str, amount: float) -> float:
    m = EXCHANGE.market(sym)
    prec_amt = m.get("precision", {}).get("amount")
    step = None
    # Certaines bourses exposent un "step" via m['info'] ; Kraken pas toujours.
    # On se contente de precision -> decimals
    if prec_amt is not None:
        q = float(f"{amount:.{prec_amt}f}")
        return q
    return amount

# cache balance très court pour limiter les allers-retours
_BAL_CACHE = {"t": 0.0, "free": {}}
def fetch_free_balance_cached(ttl: float = 3.0) -> Dict[str, float]:
    now = time.time()
    if now - _BAL_CACHE["t"] < ttl and _BAL_CACHE["free"]:
        return _BAL_CACHE["free"]
    for attempt in range(1, 4):
        try:
            bal = EXCHANGE.fetch_free_balance()
            _BAL_CACHE["t"] = now
            _BAL_CACHE["free"] = bal
            return bal
        except Exception as e:
            log.warning(f"fetch_free_balance attempt {attempt} failed: {e}")
            time.sleep(0.8 * attempt)
    raise

def retry_ccxt(callable_fn, *args, **kwargs):
    for attempt in range(1, 4):
        try:
            return callable_fn(*args, **kwargs)
        except ccxt.NetworkError as e:
            log.error(f"NetworkError (attempt {attempt}): {e}")
        except ccxt.ExchangeError as e:
            # 5xx, overload, etc.
            if "EAPI:Rate limit exceeded" in str(e):
                log.error(f"Rate limit (attempt {attempt})")
            else:
                log.error(f"ExchangeError (attempt {attempt}): {e}")
        except Exception as e:
            log.error(f"Unexpected (attempt {attempt}): {e}")
        time.sleep(1.0 * attempt)
    raise

def choose_notional_eur(conf: int) -> float:
    if conf >= 3:
        return DEFAULT_EUR_3
    return DEFAULT_EUR_2

def build_hash(payload: Dict[str, Any]) -> str:
    # Pour déduplication simple
    base = f"{payload.get('signal','')}-{payload.get('symbol','')}-{payload.get('timestamp','')}"
    return hashlib.sha1(base.encode()).hexdigest()

# dédup mémoire courte
_LAST_SEEN: Dict[str, float] = {}

def is_duplicate(payload: Dict[str, Any], window_sec: float = 3.0) -> bool:
    h = build_hash(payload)
    now = time.time()
    last = _LAST_SEEN.get(h)
    if last and (now - last) < window_sec:
        return True
    _LAST_SEEN[h] = now
    return False

def compute_amount_for_buy(sym: str, price: float, conf: int) -> float:
    notional_eur = choose_notional_eur(conf)
    amount = notional_eur / price
    amount = quantize_amount(sym, amount)
    # respect min_cost / min_amount
    info = market_info(sym)
    min_amount = info["limits"].get("amount", {}).get("min")
    if min_amount:
        amount = max(amount, float(min_amount))
    # si min_cost connu, on s'assure de le respecter
    if info["min_cost"] and price * amount < info["min_cost"]:
        amount = info["min_cost"] / price
        amount = quantize_amount(sym, amount)
    return amount

def clamp_sell_amount(sym: str, free_base: float) -> float:
    info = market_info(sym)
    min_amt = info["limits"].get("amount", {}).get("min")
    amt = free_base
    if min_amt:
        if free_base < float(min_amt):
            return 0.0
        amt = max(free_base, float(min_amt))
    return quantize_amount(sym, amt)

def place_market_order(side: str, symbol: str, amount: float) -> Dict[str, Any]:
    log.info(f"create_market_order | side={side} symbol={symbol} amount={amount}")
    return retry_ccxt(EXCHANGE.create_market_order, symbol, side, amount)

# ───────────── Flask ─────────────
app = Flask(__name__)

@app.route("/health")
def health():
    return "ok", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        raw = request.get_data(as_text=True) or ""
        body = json.loads(raw)
    except Exception:
        log.error("Invalid JSON")
        return jsonify({"ok": False, "error": "bad json"}), 400

    # Auth
    if body.get("secret") != TV_SECRET:
        log.error("Bad secret")
        return jsonify({"ok": False, "error": "bad secret"}), 401

    if is_duplicate(body):
        log.info("dedup | dropped duplicate payload")
        return jsonify({"ok": True, "dedup": True}), 200

    # Champs utiles
    signal   = (body.get("signal") or "").upper()
    symbolTV = body.get("symbol") or ""
    force_close = bool(body.get("force_close", False))
    conf = int(body.get("confidence", 2))

    try:
        symbol = normalize_symbol_for_kraken(symbolTV)
    except Exception as e:
        log.error(f"symbol normalize failed: {e}")
        return jsonify({"ok": False, "error": str(e)}), 502

    # Récup prix et balances
    ticker = retry_ccxt(EXCHANGE.fetch_ticker, symbol)
    price  = float(ticker["last"] or ticker["close"] or 0.0)
    if price <= 0:
        return jsonify({"ok": False, "error": "no price"}), 502

    free = fetch_free_balance_cached()
    base_ccy, quote_ccy = symbol.split("/")
    free_base = float(free.get(base_ccy, 0.0))
    free_quote = float(free.get(quote_ccy, 0.0))

    log.info(f"prepare | signal={signal} sym={symbol} price={price} free_base={free_base} free_quote={free_quote} conf={conf} force_close={force_close}")

    # BUY
    if signal == "BUY":
        # sizing EUR -> amount base
        amount = compute_amount_for_buy(symbol, price, conf)
        # ne pas dépasser le cash dispo
        max_amount_by_cash = free_quote / price
        amount = min(amount, max_amount_by_cash)
        amount = quantize_amount(symbol, amount)

        if amount <= 0:
            log.info("buy | skip: amount<=0 (cash too small)")
            return jsonify({"ok": True, "skipped": "cash too small"}), 200

        # éviter les ordres poussière (coût total < 10€ approx si min_cost inconnu)
        info = market_info(symbol)
        min_cost = info["min_cost"] or 10.0
        if price * amount < min_cost:
            log.info(f"buy | skip: notional {price*amount:.2f} < min_cost {min_cost}")
            return jsonify({"ok": True, "skipped": "min cost"}), 200

        try:
            resp = place_market_order("buy", symbol, amount)
            log.info(f"buy | done | {resp}")
            return jsonify({"ok": True, "order": resp}), 200
        except Exception as e:
            log.error(f"buy error: {e}\n{traceback.format_exc()}")
            return jsonify({"ok": False, "error": str(e)}), 502

    # SELL
    if signal == "SELL":
        amount = clamp_sell_amount(symbol, free_base)

        if amount <= 0:
            msg = "no base free"
            if force_close:
                msg = "no base free (forced close requested)"
            log.info(f"sell | skip: {msg}")
            return jsonify({"ok": True, "skipped": msg}), 200

        # Même garde-fou min_cost
        info = market_info(symbol)
        min_cost = info["min_cost"] or 10.0
        if price * amount < min_cost:
            log.info(f"sell | skip: notional {price*amount:.2f} < min_cost {min_cost}")
            return jsonify({"ok": True, "skipped": "min cost"}), 200

        try:
            resp = place_market_order("sell", symbol, amount)
            log.info(f"sell | done | {resp}")
            return jsonify({"ok": True, "order": resp}), 200
        except Exception as e:
            log.error(f"sell error: {e}\n{traceback.format_exc()}")
            return jsonify({"ok": False, "error": str(e)}), 502

    return jsonify({"ok": False, "error": "unknown signal"}), 400


if __name__ == "__main__":
    # Pour exécution locale (Render lance via gunicorn généralement)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
