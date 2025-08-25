# app.py — OKX Spot
import os
import time
import json
import hmac
import base64
import hashlib
import logging
from decimal import Decimal, ROUND_DOWN, InvalidOperation
from typing import Dict, Optional, Tuple

import requests
from flask import Flask, request, jsonify


# ========= Couleurs (ANSI) pour logs lisibles =========
RESET  = "\x1b[0m"
BOLD   = "\x1b[1m"
DIM    = "\x1b[2m"
GREEN  = "\x1b[32m"
YELLOW = "\x1b[33m"
CYAN   = "\x1b[36m"
RED    = "\x1b[31m"

def c(text: str, color: str) -> str:
    return f"{color}{text}{RESET}"


# ========= Logger =========
logger = logging.getLogger("tv-okx")
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(h)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
logger.propagate = False


# ========= Flask =========
app = Flask(__name__)


# ========= Config =========
EXCHANGE = os.getenv("EXCHANGE", "OKX").upper()

BASE  = os.getenv("BASE", "BTC").upper()
QUOTE = os.getenv("QUOTE", "USDT").upper()  # EUR possible si dispo

SIZE_MODE = os.getenv("SIZE_MODE", "fixed_quote").lower()  # fixed_quote | auto_size
FIXED_QUOTE_PER_TRADE = Decimal(os.getenv("FIXED_QUOTE_PER_TRADE", "50"))
MIN_QUOTE_PER_TRADE   = Decimal(os.getenv("MIN_QUOTE_PER_TRADE", "10"))
BASE_RESERVE          = Decimal(os.getenv("BASE_RESERVE", "0.00005"))
FEE_BUFFER_PCT        = Decimal(os.getenv("FEE_BUFFER_PCT", "0.002"))

COOLDOWN_SEC = int(os.getenv("COOLDOWN_SEC", "0"))
_last_fire_ts = 0.0

HTTP_RETRIES   = int(os.getenv("HTTP_RETRIES", "3"))
HTTP_BACKOFF_S = float(os.getenv("HTTP_BACKOFF_S", "0.7"))

DRY_RUN = os.getenv("DRY_RUN", "false").lower() in {"1","true","yes","on"}

# ========= OKX API creds =========
OKX_API_KEY       = os.getenv("OKX_API_KEY", "")
OKX_API_SECRET    = os.getenv("OKX_API_SECRET", "")
OKX_PASSPHRASE    = os.getenv("OKX_PASSPHRASE", "")

if not OKX_API_KEY or not OKX_API_SECRET or not OKX_PASSPHRASE:
    logger.warning(c("ATTENTION: OKX_API_KEY/SECRET/PASSPHRASE manquants — les ordres réels échoueront.", YELLOW))

OKX_BASE_URL = "https://www.okx.com"


# ========= Helpers généraux =========
def iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()

def log_tag_for_request() -> None:
    ua = (request.headers.get("User-Agent") or "").lower()
    path = request.path
    method = request.method
    tag = None
    if "uptimerobot" in ua:
        tag = c("PING Uptime", CYAN)
    elif "google-apps-script" in ua or "script.google.com" in ua:
        tag = c("PING Google", CYAN)
    elif path == "/webhook" and method == "POST":
        tag = c("ALERTE TradingView", BOLD)
    elif path == "/health":
        tag = c("HEALTH", DIM)
    if tag:
        logger.info(tag)


# ========= OKX client (REST v5) =========
def okx_ts() -> str:
    # format ISO8601 ms, ex: 2021-03-31T12:00:00.000Z
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())

def okx_sign(timestamp: str, method: str, path: str, body: str) -> str:
    # signature = Base64( HMAC_SHA256(secret, timestamp + method + path + body) )
    msg = f"{timestamp}{method}{path}{body}"
    mac = hmac.new(OKX_API_SECRET.encode(), msg.encode(), hashlib.sha256).digest()
    return base64.b64encode(mac).decode()

def okx_headers(timestamp: str, sign: str) -> Dict[str, str]:
    return {
        "OK-ACCESS-KEY": OKX_API_KEY,
        "OK-ACCESS-SIGN": sign,
        "OK-ACCESS-TIMESTAMP": timestamp,
        "OK-ACCESS-PASSPHRASE": OKX_PASSPHRASE,
        "Content-Type": "application/json",
        "x-simulated-trading": "0",  # 1 pour demo
    }

def okx_request(method: str, path: str, params: Optional[Dict] = None, body: Optional[Dict] = None, private: bool = False) -> Dict:
    url = OKX_BASE_URL + path
    query = ""
    if method.upper() == "GET" and params:
        # OKX veut la query dans la signature
        from urllib.parse import urlencode
        query = "?" + urlencode(params)
        url += query

    payload = ""
    if method.upper() in ("POST", "DELETE"):
        payload = json.dumps(body or {}, separators=(",", ":"))

    headers = {}
    if private:
        ts = okx_ts()
        sign = okx_sign(ts, method.upper(), path + (query or ""), payload)
        headers = okx_headers(ts, sign)

    for i in range(HTTP_RETRIES):
        try:
            resp = requests.request(method=method.upper(), url=url, headers=headers, data=payload if payload else None, timeout=15)
            data = resp.json()
            # OKX: code == "0" => OK
            if str(data.get("code")) == "0":
                return data
            # Retry sur rate limit
            if data.get("msg", "").lower().find("rate") >= 0:
                time.sleep(HTTP_BACKOFF_S * (i + 1))
                continue
            # sinon, on sort
            raise RuntimeError(f"OKX error {data.get('code')}: {data.get('msg')}")
        except Exception as e:
            if i == HTTP_RETRIES - 1:
                raise
            time.sleep(HTTP_BACKOFF_S * (i + 1))
    return {}

# ========= Résolution instrument & tailles =========
_instr_cache: Optional[Tuple[str, Decimal, Decimal]] = None
# -> (instId, lotSz, minSz) où lotSz = pas de quantité (ex: 0.0001), minSz = taille minimale

def resolve_instrument() -> Tuple[str, Decimal, Decimal]:
    """
    Récupère l'instrument SPOT OKX (instId ex: 'BTC-USDT' ou 'BTC-EUR'),
    et renvoie (instId, lotSz, minSz) pour arrondir/valider les quantités.
    """
    global _instr_cache
    if _instr_cache:
        return _instr_cache

    inst_id = f"{BASE}-{QUOTE}"
    data = okx_request("GET", "/api/v5/public/instruments", params={"instType": "SPOT", "instId": inst_id}, private=False)
    instruments = data.get("data", [])
    if not instruments:
        raise RuntimeError(f"Instrument {inst_id} introuvable sur OKX (vérifie QUOTE/BASE, ex: BTC-USDT).")

    meta = instruments[0]
    lotSz = Decimal(meta.get("lotSz", "0.0001"))  # pas quantité
    minSz = Decimal(meta.get("minSz", lotSz))     # minimum quantité
    _instr_cache = (inst_id, lotSz, minSz)
    return _instr_cache

def quantize_to_step(q: Decimal, step: Decimal) -> Decimal:
    # step ex: 0.0001 -> on quantize à ce pas
    if step == 0:
        return q
    # nombre de décimales du step
    step_str = format(step, 'f')
    decimals = 0
    if "." in step_str:
        decimals = len(step_str.split(".")[1])
    fmt = "0." + "0"*decimals if decimals > 0 else "0"
    return q.quantize(Decimal(fmt), rounding=ROUND_DOWN)

# ========= Balances =========
def get_balances() -> Tuple[Decimal, Decimal]:
    """
    Retourne (bal_quote_dispo, bal_base_dispo)
    """
    data = okx_request("GET", "/api/v5/account/balance", params={"ccy": f"{QUOTE},{BASE}"}, private=True)
    details = data.get("data", [])
    if not details:
        return Decimal("0"), Decimal("0")
    entry = details[0].get("details", [])
    bal_quote = Decimal("0")
    bal_base  = Decimal("0")
    for d in entry:
        ccy = d.get("ccy", "")
        avail = Decimal(str(d.get("availBal", "0")))
        if ccy.upper() == QUOTE:
            bal_quote = avail
        elif ccy.upper() == BASE:
            bal_base = avail
    return bal_quote, bal_base

# ========= Ordres marché =========
def place_market_order(side: str, sz_quote: Optional[Decimal] = None, sz_base: Optional[Decimal] = None) -> Dict:
    """
    BUY:  on envoie 'sz' = montant en QUOTE avec 'tgtCcy'='quote_ccy'
    SELL: on envoie 'sz' = quantité en BASE
    """
    inst_id, lotSz, minSz = resolve_instrument()
    body = {"instId": inst_id, "tdMode": "cash", "side": side, "ordType": "market"}

    if side == "buy":
        # on spend en QUOTE
        assert sz_quote is not None
        # OKX veut une string (pas besoin d'arrondir à lotSz pour QUOTE spend)
        body["tgtCcy"] = "quote_ccy"
        body["sz"] = str(sz_quote)
    else:
        # sell: quantité base arrondie au pas
        assert sz_base is not None
        vol = quantize_to_step(sz_base, lotSz)
        if vol < minSz:
            raise RuntimeError(f"BELOW_MIN_SIZE | vol={vol} < minSz={minSz}")
        body["sz"] = str(vol)

    if DRY_RUN:
        logger.info(c(f"DRY_RUN {side.upper()} {inst_id} | body={body}", YELLOW))
        return {"dry_run": True, "body": body}

    logger.info(c(f"ORDER {side.upper()} {inst_id} | body={body}", YELLOW))
    data = okx_request("POST", "/api/v5/trade/order", body=body, private=True)
    logger.info(c(f"OKX OK | {data.get('data')}", GREEN))
    return data.get("data", {})

# ========= Routes =========
@app.get("/health")
def health():
    log_tag_for_request()
    return jsonify({"status": "ok", "time": iso_now(), "exchange": EXCHANGE}), 200

@app.post("/webhook")
def webhook():
    global _last_fire_ts
    log_tag_for_request()

    try:
        payload = request.get_json(force=True, silent=True) or {}
        signal = str(payload.get("signal", "")).upper()
        symbol = str(payload.get("symbol", ""))       # info
        timeframe = str(payload.get("timeframe", "")) # info

        logger.info(c(f"ALERT {signal} | {symbol} {timeframe}", BOLD))

        # cooldown
        if COOLDOWN_SEC > 0:
            now = time.time()
            if now - _last_fire_ts < COOLDOWN_SEC:
                left = int(COOLDOWN_SEC - (now - _last_fire_ts))
                logger.info(c(f"Cooldown actif -> alerte ignorée ({left}s)", YELLOW))
                return jsonify({"ok": True, "skipped": "cooldown"}), 200

        inst_id, lotSz, minSz = resolve_instrument()
        bal_quote, bal_base = get_balances()

        if signal == "BUY":
            # sizing
            if SIZE_MODE == "fixed_quote":
                quote_to_spend = FIXED_QUOTE_PER_TRADE
            else:
                quote_to_spend = bal_quote

            if quote_to_spend < MIN_QUOTE_PER_TRADE:
                logger.error(c(f"REJECT MIN_QUOTE_PER_TRADE | want>={MIN_QUOTE_PER_TRADE} got={quote_to_spend}", RED))
                return jsonify({"ok": False, "reason": "MIN_QUOTE_PER_TRADE"}), 400
            if bal_quote <= 0:
                logger.error(c("REJECT NO_QUOTE_BALANCE", RED))
                return jsonify({"ok": False, "reason": "NO_QUOTE_BALANCE"}), 400

            # limite à ce qu'on a réellement + buffer frais
            spend = min(quote_to_spend, bal_quote) * (Decimal("1") - FEE_BUFFER_PCT)

            res = place_market_order("buy", sz_quote=spend)
            _last_fire_ts = time.time()
            return jsonify({"ok": True, "okx": res}), 200

        elif signal == "SELL":
            sellable = bal_base - BASE_RESERVE
            if sellable <= 0:
                logger.error(c(f"REJECT NO_BASE_TO_SELL | bal_base={bal_base} reserve={BASE_RESERVE}", RED))
                return jsonify({"ok": False, "reason": "NO_BASE_TO_SELL"}), 400
            if sellable < minSz:
                logger.error(c(f"REJECT BELOW_MIN_SIZE | sellable={sellable} < minSz={minSz}", RED))
                return jsonify({"ok": False, "reason": "BELOW_MIN_SIZE"}), 400

            res = place_market_order("sell", sz_base=sellable)
            _last_fire_ts = time.time()
            return jsonify({"ok": True, "okx": res}), 200

        else:
            logger.error(c(f"REJECT UNKNOWN_SIGNAL | {signal}", RED))
            return jsonify({"ok": False, "reason": "UNKNOWN_SIGNAL"}), 400

    except Exception as e:
        logger.error(c(f"ERROR webhook: {type(e).__name__}: {e}", RED))
        return jsonify({"ok": False, "error": str(e)}), 500
