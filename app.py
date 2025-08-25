# app.py
import os
import time
import logging
from typing import Optional, Dict, Tuple
from decimal import Decimal, ROUND_DOWN, InvalidOperation
from datetime import datetime, timezone

from flask import Flask, request, jsonify
import krakenex


# ========= Logging propre =========
logger = logging.getLogger("tv-kraken")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
logger.propagate = False  # évite les doublons dans Render


app = Flask(__name__)


# ========= Helpers =========
def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_tag_for_request() -> None:
    """
    Ajoute un petit tag lisible pour regrouper visuellement les logs.
    """
    ua = (request.headers.get("User-Agent") or "").lower()
    path = request.path
    method = request.method
    tag: Optional[str] = None

    if "uptimerobot" in ua:
        tag = "PING Uptime"
    elif "google-apps-script" in ua or "script.google.com" in ua or "beanserver" in ua:
        tag = "PING Google Script"
    elif path == "/health":
        tag = "HEALTH"
    elif path == "/webhook" and method == "POST":
        tag = "ALERTE TradingView"

    if tag:
        logger.info(tag)


# ========= Config depuis l'env =========
BASE = os.getenv("BASE", "BTC").upper()           # ex: BTC
QUOTE = os.getenv("QUOTE", "EUR").upper()         # ex: EUR
# alias Kraken: BTC => XBT
BASE_ALIASED = "XBT" if BASE == "BTC" else BASE

SIZE_MODE = os.getenv("SIZE_MODE", "fixed_eur").lower()  # fixed_eur | auto_size
FIXED_EUR_PER_TRADE = Decimal(os.getenv("FIXED_EUR_PER_TRADE", "50"))
MIN_EUR_PER_TRADE = Decimal(os.getenv("MIN_EUR_PER_TRADE", "10"))
BTC_RESERVE = Decimal(os.getenv("BTC_RESERVE", "0.00005"))
FEE_BUFFER_PCT = Decimal(os.getenv("FEE_BUFFER_PCT", "0.002"))  # 0.2%

HTTP_RETRIES = int(os.getenv("HTTP_RETRIES", "3"))
HTTP_BACKOFF_S = float(os.getenv("HTTP_BACKOFF_S", "0.7"))

COOLDOWN_SEC = int(os.getenv("COOLDOWN_SEC", "0"))
_last_fire_ts = 0.0

# Dry-run (validation côté Kraken sans exécuter) → pratique pour tester
DRY_RUN = os.getenv("DRY_RUN", "false").lower() in ("1", "true", "yes")

# ========= Kraken client =========
KRAKEN_API_KEY = os.getenv("KRAKEN_API_KEY", "")
KRAKEN_API_SECRET = os.getenv("KRAKEN_API_SECRET", "")
if not KRAKEN_API_KEY or not KRAKEN_API_SECRET:
    logger.warning("KRAKEN_API_KEY / KRAKEN_API_SECRET non définis : les ordres échoueront.")

k = krakenex.API(key=KRAKEN_API_KEY, secret=KRAKEN_API_SECRET)

# Cache pour la paire Kraken (clé, décimales de prix, décimales du lot, ordermin)
_pair_cache: Optional[Tuple[str, int, int, Decimal]] = None


# ========= Helpers Kraken avec retry/backoff =========
def _kraken_call(api_fn: str, data: Optional[Dict] = None, private: bool = False) -> Dict:
    """
    Appel API Kraken (privée ou publique) avec gestion des erreurs transitoires
    et backoff exponentiel soft.
    """
    last_err: Optional[str] = None
    for i in range(HTTP_RETRIES):
        try:
            if private:
                resp = k.query_private(api_fn, data or {})
            else:
                resp = k.query_public(api_fn, data or {})

            if resp.get("error"):
                # erreurs Kraken connues (fonds insuffisants, rate limit, etc.)
                last_err = "; ".join(resp.get("error") or [])
                # si rate limit, backoff puis retry
                if "EAPI:Rate limit" in last_err:
                    time.sleep(HTTP_BACKOFF_S * (i + 1))
                    continue
                # pour erreurs non transitoires, on sort tout de suite
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

    alt_wanted = f"{BASE_ALIASED}{QUOTE}"  # ex: XBTEUR
    resp = _kraken_call("AssetPairs", private=False)
    result = resp.get("result", {})
    chosen_key: Optional[str] = None
    price_decimals = 5
    lot_decimals = 5
    ordermin = Decimal("0.00001")

    for key, meta in result.items():
        altname = meta.get("altname")
        if altname == alt_wanted:
            chosen_key = key
            price_decimals = meta.get("pair_decimals", price_decimals)
            lot_decimals = meta.get("lot_decimals", lot_decimals)
            # ordermin peut ne pas exister selon version de l'API
            ordm = meta.get("ordermin")
            if ordm:
                try:
                    ordermin = Decimal(str(ordm))
                except InvalidOperation:
                    pass
            break

    if not chosen_key:
        # fallback: tenter l'altname directement ; souvent accepté par AddOrder
        chosen_key = alt_wanted

    _pair_cache = (chosen_key, int(price_decimals), int(lot_decimals), ordermin)
    return _pair_cache


def public_price_fallback(pair_key: str) -> Optional[Decimal]:
    """
    Récupère un prix indicatif si TradingView ne l’a pas envoyé.
    """
    try:
        resp = _kraken_call("Ticker", {"pair": pair_key}, private=False)
        result = resp.get("result", {})
        if not result:
            return None
        first = next(iter(result.values()))
        last = first.get("c", [None])[0]  # champ 'c' = last trade [price, lot]
        if last is None:
            return None
        return Decimal(str(last))
    except Exception:
        return None


def quantize_qty(qty: Decimal, lot_decimals: int) -> Decimal:
    fmt = "0." + "0" * lot_decimals
    return qty.quantize(Decimal(fmt), rounding=ROUND_DOWN)


def get_balances() -> Dict[str, Decimal]:
    resp = _kraken_call("Balance", private=True)
    return {k_: Decimal(v) for k_, v in resp.get("result", {}).items()}


def place_market_order(side: str, volume_base: Decimal) -> Dict:
    """
    Envoie un ordre marché sur Kraken.
    side : 'buy' ou 'sell'
    volume_base : quantité en BASE (BTC/XBT) – Kraken attend le volume base.
    """
    pair_key, _, lot_decimals, _ = resolve_pair()
    vol_str = str(quantize_qty(volume_base, lot_decimals))

    data = {
        "pair": pair_key,    # ex: XXBTZEUR
        "type": side,        # "buy" ou "sell"
        "ordertype": "market",
        "volume": vol_str,
    }
    # mode validation (dry-run) optionnel
    if DRY_RUN:
        data["validate"] = True

    logger.info(f"ORDER {side.upper()} {pair_key} vol={vol_str}")
    resp = _kraken_call("AddOrder", data, private=True)
    logger.info(f"KRAKEN OK | {resp.get('result')}")
    # en dry-run, Kraken renvoie aussi 'result', sans exécuter
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
        # -------- lecture payload --------
        data = request.get_json(force=True, silent=True) or {}
        signal = str(data.get("signal", "")).upper()     # "BUY" | "SELL"
        symbol = str(data.get("symbol", ""))             # ex: BTCEUR (info)
        timeframe = str(data.get("timeframe", ""))       # ex: "15"
        raw_price = data.get("price")                    # peut être str/float/None

        logger.info(f"ALERT {signal} | {symbol} {timeframe} | price={raw_price}")

        # -------- anti-spam cooldown --------
        if COOLDOWN_SEC > 0:
            now = time.time()
            if now - _last_fire_ts < COOLDOWN_SEC:
                left = int(COOLDOWN_SEC - (now - _last_fire_ts))
                logger.info(f"Cooldown actif -> alerte ignorée ({left}s restants)")
                return jsonify({"ok": True, "skipped": "cooldown"}), 200

        # -------- résolution paire & méta --------
        pair_key, price_decimals, lot_decimals, ordermin_base = resolve_pair()

        # -------- prix --------
        px: Optional[Decimal] = None
        if raw_price is not None:
            try:
                px = Decimal(str(raw_price))
            except InvalidOperation:
                px = None
        if px is None:
            px = public_price_fallback(pair_key)
        if px is None or px <= 0:
            logger.error("Prix introuvable : payload + fallback Ticker KO")
            return jsonify({"ok": False, "reason": "NO_PRICE"}), 400

        # -------- soldes --------
        bals = get_balances()
        bal_eur = bals.get("ZEUR", Decimal("0"))
        bal_btc = bals.get("XXBT", Decimal("0")) or bals.get("XBT", Decimal("0"))

        # -------- BUY : on dépense des EUR --------
        if signal == "BUY":
            # montant EUR à dépenser
            if SIZE_MODE == "fixed_eur":
                eur_to_spend = FIXED_EUR_PER_TRADE
            else:
                eur_to_spend = bal_eur

            # minimum
            if eur_to_spend < MIN_EUR_PER_TRADE:
                return jsonify({"ok": False, "reason": "MIN_EUR_PER_TRADE"}), 400

            if bal_eur <= 0:
                return jsonify({"ok": False, "reason": "NO_EUR_BALANCE"}), 400

            # limite à ce qu'on a réellement + petit buffer frais
            eur_net = min(eur_to_spend, bal_eur) * (Decimal("1") - FEE_BUFFER_PCT)
            # volume base calculé
            vol_base = eur_net / px

            # contraintes Kraken : décimales & ordermin
            vol_base = quantize_qty(vol_base, lot_decimals)
            if vol_base <= 0:
                return jsonify({"ok": False, "reason": "VOLUME_TOO_SMALL"}), 400
            if vol_base < ordermin_base:
                return jsonify({"ok": False, "reason": "BELOW_ORDERMIN"}), 400

            result = place_market_order("buy", vol_base)
            _last_fire_ts = time.time()
            return jsonify({"ok": True, "kraken": result}), 200

        # -------- SELL : on vend du BTC --------
        elif signal == "SELL":
            btc_sellable = bal_btc - BTC_RESERVE
            if btc_sellable <= 0:
                return jsonify({"ok": False, "reason": "NO_BTC_TO_SELL"}), 400

            vol_base = quantize_qty(btc_sellable, lot_decimals)
            if vol_base < ordermin_base:
                return jsonify({"ok": False, "reason": "BELOW_ORDERMIN"}), 400

            result = place_market_order("sell", vol_base)
            _last_fire_ts = time.time()
            return jsonify({"ok": True, "kraken": result}), 200

        else:
            return jsonify({"ok": False, "reason": "UNKNOWN_SIGNAL"}), 400

    except Exception as e:
        logger.error(f"ERROR webhook: {type(e).__name__}: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# ========= Main local (Render utilise gunicorn) =========
if __name__ == "__main__":
    # En local uniquement (Render injecte PORT et lance gunicorn)
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
