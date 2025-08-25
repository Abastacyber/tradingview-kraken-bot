# app.py
import os
import time
import logging
from typing import Dict, Optional, Tuple
from decimal import Decimal, ROUND_DOWN, InvalidOperation

from flask import Flask, request, jsonify

import krakenex

# ========= Couleurs (ANSI) =========
C = {
    "reset": "\033[0m",
    "blue": "\033[34m",
    "cyan": "\033[36m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "red": "\033[31m",
}

def paint(text: str, color: str) -> str:
    return f"{C.get(color,'')}{text}{C['reset']}"

# ========= Logging lisible sur Render =========
logger = logging.getLogger("tv-kraken")
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(h)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
# évite les doublons dans Render
logger.propagate = False

# ========= Flask =========
app = Flask(__name__)

# ========= Config depuis l'env =========
BASE = os.getenv("BASE", "BTC").upper()        # ex: BTC
QUOTE = os.getenv("QUOTE", "EUR").upper()      # ex: EUR
# Kraken utilise XBT pour BTC
BASE_ALIASED = "XBT" if BASE == "BTC" else BASE

SIZE_MODE = os.getenv("SIZE_MODE", "fixed_eur").lower()  # fixed_eur | auto_size
FIXED_EUR_PER_TRADE = Decimal(os.getenv("FIXED_EUR_PER_TRADE", "50"))
MIN_EUR_PER_TRADE = Decimal(os.getenv("MIN_EUR_PER_TRADE", "10"))
BTC_RESERVE = Decimal(os.getenv("BTC_RESERVE", "0.00005"))       # on ne vide pas tout
FEE_BUFFER_PCT = Decimal(os.getenv("FEE_BUFFER_PCT", "0.002"))    # 0.2% pour couvrir les frais

HTTP_RETRIES = int(os.getenv("HTTP_RETRIES", "3"))
HTTP_BACKOFF_S = float(os.getenv("HTTP_BACKOFF_S", "0.7"))

COOLDOWN_SEC = int(os.getenv("COOLDOWN_SEC", "0"))
_last_fire_ts = 0.0

DRY_RUN = os.getenv("DRY_RUN", "false").lower() in ("1", "true", "yes")

# ========= Kraken client =========
KRAKEN_API_KEY = os.getenv("KRAKEN_API_KEY", "")
KRAKEN_API_SECRET = os.getenv("KRAKEN_API_SECRET", "")

if not KRAKEN_API_KEY or not KRAKEN_API_SECRET:
    logger.warning(paint("ATTENTION: KRAKEN_API_KEY / KRAKEN_API_SECRET non définis — les ordres échoueront.", "yellow"))

k = krakenex.API(key=KRAKEN_API_KEY, secret=KRAKEN_API_SECRET)

# ========= Cache pour la paire Kraken =========
_pair_cache: Optional[Tuple[str, int, int, Decimal]] = None
# -> (pair_key, price_decimals, lot_decimals, ordermin_base)

def iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

# ========= Tag lisible (et coloré) pour chaque requête =========
def log_tag_for_request() -> None:
    ua = (request.headers.get("User-Agent") or "").lower()
    path = request.path
    method = request.method

    tag = None
    if "uptimerobot" in ua:
        tag = paint("PING Uptime", "cyan")
    elif "google-apps-script" in ua or "script.google.com" in ua or "beanserver" in ua:
        tag = paint("PING Google", "cyan")
    elif path == "/health":
        tag = paint("HEALTH", "green")
    elif path == "/webhook" and method == "POST":
        tag = paint("ALERTE TradingView", "blue")

    if tag:
        logger.info(tag)

# ========= Helpers Kraken avec retry/backoff =========
def _kraken_call(api_fn: str, data: Optional[Dict] = None, private: bool = False) -> Dict:
    last_err = None
    for i in range(HTTP_RETRIES):
        try:
            resp = k.query_private(api_fn, data or {}) if private else k.query_public(api_fn, data or {})
            if resp.get("error"):
                # erreurs Kraken connues (fonds insuffisants, rate limit, etc.)
                last_err = resp["error"]
                # si rate limit, backoff + retry
                if any("Rate limit" in e or "EAPI:Rate limit" in e for e in resp["error"]):
                    time.sleep(HTTP_BACKOFF_S * (i + 1))
                    continue
                # autres erreurs non transitoires -> on sort
                break
            return resp
        except Exception as e:
            last_err = str(e)
            time.sleep(HTTP_BACKOFF_S * (i + 1))
    raise RuntimeError(f"Kraken {api_fn} error after retries: {last_err}")

def resolve_pair() -> Tuple[str, int, int, Decimal]:
    """
    Résout la paire Kraken réelle (clé ex: 'XXBTZEUR') à partir de BASE/QUOTE.
    Retourne (pair_key, price_decimals, lot_decimals, ordermin_base).
    """
    global _pair_cache
    if _pair_cache:
        return _pair_cache

    alt_wanted = f"{BASE_ALIASED}{QUOTE}"   # ex: XBTEUR

    resp = _kraken_call("AssetPairs", private=False)
    result = resp.get("result", {})
    chosen_key = None
    price_decimals = 5
    lot_decimals = 5
    ordermin = Decimal("0.00001")

    # on privilégie la correspondance sur altname
    for key, meta in result.items():
        altname = meta.get("altname")
        if altname == alt_wanted:
            chosen_key = key
            price_decimals = meta.get("pair_decimals", price_decimals)
            lot_decimals = meta.get("lot_decimals", lot_decimals)
            ordm = meta.get("ordermin")
            if ordm:
                try:
                    ordermin = Decimal(str(ordm))
                except InvalidOperation:
                    pass
            break

    if not chosen_key:
        # fallback : certains endpoints acceptent directement l'altname
        chosen_key = alt_wanted

    _pair_cache = (chosen_key, int(price_decimals), int(lot_decimals), ordermin)
    return _pair_cache

def get_balances() -> Dict[str, Decimal]:
    resp = _kraken_call("Balance", private=True)
    return {k_: Decimal(v) for k_, v in resp.get("result", {}).items()}

def public_price_fallback(pair_key: str) -> Optional[Decimal]:
    """Récupère un prix indicatif si TradingView ne l’a pas envoyé."""
    try:
        resp = _kraken_call("Ticker", {"pair": pair_key}, private=False)
        result = resp.get("result", {})
        if not result:
            return None
        first = next(iter(result.values()))
        last = first.get("c", [None])[0]
        if last is None:
            return None
        return Decimal(str(last))
    except Exception:
        return None

def quantize_qty(qty: Decimal, lot_decimals: int) -> Decimal:
    fmt = "0." + "0" * lot_decimals
    return qty.quantize(Decimal(fmt), rounding=ROUND_DOWN)

def place_market_order(side: str, volume_base: Decimal) -> Dict:
    """
    side: 'buy' ou 'sell'
    volume_base: quantité en BASE (Kraken attend l'unité de base)
    """
    pair_key, _, lot_decimals, _ = resolve_pair()
    vol_str = str(quantize_qty(volume_base, lot_decimals))

    data = {
        "pair": pair_key,
        "type": side,          # "buy" | "sell"
        "ordertype": "market",
        "volume": vol_str,
    }

    # mode test sans exécuter
    if DRY_RUN:
        data["validate"] = True

    logger.info(paint(f"ORDER {side.upper()} {pair_key} vol={vol_str}", "green"))
    resp = _kraken_call("AddOrder", data, private=True)
    logger.info(paint(f"KRAKEN OK | {resp.get('result')}", "green"))
    return resp.get("result", {})

# ========= Routes =========
@app.get("/health")
def health():
    log_tag_for_request()
    return jsonify({"status": "ok", "time": iso_now()}), 200

@app.post("/webhook")
def webhook():
    global _last_fire_ts
    log_tag_for_request()

    try:
        data = request.get_json(force=True, silent=True) or {}
        signal = str(data.get("signal", "")).upper()
        symbol = str(data.get("symbol", ""))     # ex: BTCEUR (informative)
        timeframe = str(data.get("timeframe", ""))
        raw_price = data.get("price")            # peut être str/float/None

        logger.info(paint(f"ALERT {signal} | {symbol} {timeframe} | price={raw_price}", "blue"))

        # === Cooldown anti-spam ===
        if COOLDOWN_SEC > 0:
            now = time.time()
            if now - _last_fire_ts < COOLDOWN_SEC:
                left = int(COOLDOWN_SEC - (now - _last_fire_ts))
                logger.warning(paint(f"Cooldown actif -> alerte ignorée ({left}s restants)", "yellow"))
                return jsonify({"ok": True, "skipped": "cooldown", "left_s": left}), 200

        # ---- Résolution paire & métadonnées ----
        pair_key, price_decimals, lot_decimals, ordermin_base = resolve_pair()

        # ---- Prix ----
        px: Optional[Decimal] = None
        if raw_price is not None:
            try:
                px = Decimal(str(raw_price))
            except InvalidOperation:
                px = None

        if px is None:
            px = public_price_fallback(pair_key)
        if px is None or px <= 0:
            msg = f"NO_PRICE | payload={data} + fallback Ticker KO"
            logger.error(paint(msg, "red"))
            return jsonify({"ok": False, "reason": "NO_PRICE"}), 400

        # ---- Balances ----
        bals = get_balances()
        bal_eur = bals.get("ZEUR", Decimal("0"))
        bal_btc = bals.get("XXBT", Decimal("0")) or bals.get("XBT", Decimal("0"))
        # note : certaines API renvoient 'XXBT' (standard)

        # ---- BUY : on dépense des EUR ----
        if signal == "BUY":
            # montant à dépenser
            if SIZE_MODE == "fixed_eur":
                eur_to_spend = FIXED_EUR_PER_TRADE
            else:
                eur_to_spend = bal_eur

            if eur_to_spend < MIN_EUR_PER_TRADE:
                logger.error(paint(f"REJECT MIN_EUR_PER_TRADE | need>={MIN_EUR_PER_TRADE} | got={eur_to_spend}", "red"))
                return jsonify({"ok": False, "reason": "MIN_EUR_PER_TRADE"}), 400

            if bal_eur <= 0:
                logger.error(paint(f"REJECT NO_EUR_BALANCE | bal_eur={bal_eur}", "red"))
                return jsonify({"ok": False, "reason": "NO_EUR_BALANCE"}), 400

            # limite à ce qu'on a
            if eur_to_spend > bal_eur:
                eur_to_spend = bal_eur

            eur_net = eur_to_spend * (Decimal("1") - FEE_BUFFER_PCT)
            vol_base = eur_net / px

            # contraintes Kraken : lot_decimals / ordermin
            vol_base = quantize_qty(vol_base, lot_decimals)
            if vol_base <= 0:
                logger.error(paint(f"REJECT VOLUME_TOO_SMALL | vol_base={vol_base}", "red"))
                return jsonify({"ok": False, "reason": "VOLUME_TOO_SMALL"}), 400
            if vol_base < ordermin_base:
                logger.error(paint(f"REJECT BELOW_ORDERMIN | vol_base={vol_base} < ordermin={ordermin_base}", "red"))
                return jsonify({"ok": False, "reason": "BELOW_ORDERMIN"}), 400

            result = place_market_order("buy", vol_base)
            _last_fire_ts = time.time()
            return jsonify({"ok": True, "kraken": result}), 200

        # ---- SELL : on vend du BTC ----
        elif signal == "SELL":
            btc_sellable = bal_btc - BTC_RESERVE
            if btc_sellable <= 0:
                logger.error(paint(f"REJECT NO_BTC_TO_SELL | bal_btc={bal_btc} | reserve={BTC_RESERVE}", "red"))
                return jsonify({"ok": False, "reason": "NO_BTC_TO_SELL"}), 400

            vol_base = quantize_qty(btc_sellable, lot_decimals)
            if vol_base < ordermin_base:
                logger.error(paint(f"REJECT BELOW_ORDERMIN | vol_base={vol_base} < ordermin={ordermin_base}", "red"))
                return jsonify({"ok": False, "reason": "BELOW_ORDERMIN"}), 400

            result = place_market_order("sell", vol_base)
            _last_fire_ts = time.time()
            return jsonify({"ok": True, "kraken": result}), 200

        else:
            logger.error(paint(f"REJECT UNKNOWN_SIGNAL | signal={signal}", "red"))
            return jsonify({"ok": False, "reason": "UNKNOWN_SIGNAL"}), 400

    except Exception as e:
        logger.error(paint(f"ERROR webhook: {type(e).__name__}: {e}", "red"))
        return jsonify({"ok": False, "error": str(e)}), 500
