import os, json, logging, time, base64, hashlib, hmac, urllib.parse
from urllib.parse import quote_plus, urlencode
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# ========= ENV =========
API_KEY     = os.getenv("KRAKEN_API_KEY", "")
API_SECRET  = os.getenv("KRAKEN_API_SECRET", "")
BASE        = os.getenv("BASE_SYMBOL", "BTC").upper()
QUOTE       = os.getenv("QUOTE_SYMBOL", "EUR").upper()
PAPER       = os.getenv("PAPER_MODE", "1") == "1"
VALIDATE    = os.getenv("KRAKEN_VALIDATE", "1") == "1"
RISK_EUR    = float(os.getenv("RISK_EUR_PER_TRADE", "5"))

# Filtre EMA & fenêtre horaire
EMA_ENABLE  = os.getenv("EMA_ENABLE", "1") == "1"
EMA_FAST    = int(os.getenv("EMA_FAST", "50"))
EMA_SLOW    = int(os.getenv("EMA_SLOW", "200"))
WINDOW_UTC  = os.getenv("TRADING_WINDOW_UTC", "12:00-18:00")  # HH:MM-HH:MM, UTC

# SL/TP en %
TP_PCT      = float(os.getenv("TP_PCT", "1.0"))   # ex: 1.0 = +1%
SL_PCT      = float(os.getenv("SL_PCT", "0.4"))   # ex: 0.4 = -0.4%

# Pas/mini sécurité
MIN_QTY     = float(os.getenv("MIN_QTY", "0.00002"))
QTY_STEP    = float(os.getenv("QTY_STEP", "0.00001"))
PRICE_DEC   = int(os.getenv("PRICE_DEC", "2"))    # décimales prix pour AddOrder

# Mapping TV -> Kraken (base)
MAP_BASE = {"BTC": "XBT", "XBT": "XBT", "ETH": "XETH", "XETH": "XETH", "LTC": "XLTC", "XLTC": "XLTC"}
KRAKEN_API_URL = "https://api.kraken.com"

# ========= Utils =========
def now_utc_hm():
    t = time.gmtime()
    return t.tm_hour, t.tm_min

def in_window_utc(win: str) -> bool:
    try:
        start, end = win.split("-")
        sh, sm = [int(x) for x in start.split(":")]
        eh, em = [int(x) for x in end.split(":")]
        ch, cm = now_utc_hm()
        start_min = sh*60 + sm
        end_min   = eh*60 + em
        cur_min   = ch*60 + cm
        if start_min <= end_min:
            return start_min <= cur_min <= end_min
        # fenêtre qui passe minuit (ex: 22:00-02:00)
        return cur_min >= start_min or cur_min <= end_min
    except Exception:
        return True  # si mauvais format, on ne bloque pas

def normalize_pair(symbol_tv: str|None, default_base: str, default_quote: str) -> tuple[str,str,str,str]:
    """
    Retourne:
      - pair_pub  : 'XBT/EUR' (OHLC/Ticker)
      - pair_priv : 'XBT/EUR' (AddOrder l'accepte)
      - pair_alt  : 'XBTEUR'  (Ticker accepte aussi)
      - base_quote: ('XBT','EUR')
    """
    if not symbol_tv or not str(symbol_tv).strip():
        symbol_tv = f"{default_base}/{default_quote}"
    s = str(symbol_tv).upper().replace(":", "/").replace("-", "/").strip()
    if "/" in s:
        b,q = [p.strip() for p in s.split("/",1)]
    else:
        # BTCEUR -> BTC + EUR (quote = env si ambigu)
        if s.endswith(default_quote):
            b = s[:-len(default_quote)]
            q = default_quote
        else:
            b, q = s[:3], s[3:] if len(s)>=6 else (default_base, default_quote)
    base = MAP_BASE.get(b, b)
    quote= q
    pair_pub  = f"{base}/{quote}"
    pair_priv = pair_pub
    pair_alt  = f"{base}{quote}"
    return pair_pub, pair_priv, pair_alt, (base, quote)

def round_qty(x: float) -> float:
    return float(f"{max(x, MIN_QTY):.8f}")

def round_step(x: float, step: float) -> float:
    # arrondi plancher au pas
    return float(int(x/step)*step)

def ema(values, length):
    if length <= 1 or len(values) == 0:
        return values[-1] if values else 0.0
    k = 2/(length+1)
    e = values[0]
    for v in values[1:]:
        e = v*k + e*(1-k)
    return e

# ========= Public API =========
def fetch_price(pair_pub: str) -> float:
    # Ticker accepte 'XBT/EUR' encodé
    url = f"{KRAKEN_API_URL}/0/public/Ticker?pair={quote_plus(pair_pub)}"
    r = requests.get(url, timeout=10); r.raise_for_status()
    js = r.json()
    if js.get("error"):
        raise RuntimeError(js["error"])
    data = js["result"]; k = next(iter(data.keys()))
    return float(data[k]["c"][0])

def fetch_ohlc(pair_pub: str, interval_min: int, limit: int = 300):
    url = f"{KRAKEN_API_URL}/0/public/OHLC?pair={quote_plus(pair_pub)}&interval={interval_min}"
    r = requests.get(url, timeout=10); r.raise_for_status()
    js = r.json()
    if js.get("error"):
        raise RuntimeError(js["error"])
    data = js["result"]; k = next(iter(data.keys()))
    rows = data[k]  # [time, open, high, low, close, vwap, volume, count]
    if limit and len(rows) > limit:
        rows = rows[-limit:]
    closes = [float(x[4]) for x in rows]
    return closes

def tf_to_interval(tf: str) -> int:
    s = tf.lower().replace(" ", "")
    if s.endswith("m"): return int(s[:-1])
    if s.endswith("h"): return int(s[:-1])*60
    if s.endswith("d"): return int(s[:-1])*1440
    # défaut: 5 minutes
    try:
        return int(s)
    except:
        return 5

# ========= Private API (HMAC) =========
def _kraken_sign(uri_path: str, data: dict, secret: str) -> str:
    postdata = urllib.parse.urlencode(data)
    encoded  = (str(data['nonce']) + postdata).encode()
    message  = uri_path.encode() + hashlib.sha256(encoded).digest()
    mac      = hmac.new(base64.b64decode(secret), message, hashlib.sha512)
    return base64.b64encode(mac.digest()).decode()

def kraken_private(endpoint: str, data: dict) -> dict:
    if "nonce" not in data:
        data["nonce"] = int(time.time()*1000)
    uri_path = f"/0/private/{endpoint}"
    headers = {
        "API-Key": API_KEY,
        "API-Sign": _kraken_sign(uri_path, data, API_SECRET),
        "Content-Type": "application/x-www-form-urlencoded",
    }
    r = requests.post(KRAKEN_API_URL+uri_path, headers=headers, data=urllib.parse.urlencode(data), timeout=15)
    r.raise_for_status()
    js = r.json()
    if js.get("error"):
        raise RuntimeError(js["error"])
    return js["result"]

def place_order_market(side: str, pair_priv: str, qty: float, validate: bool):
    data = {
        "pair": pair_priv,
        "type": side,                      # 'buy' / 'sell'
        "ordertype": "market",
        "volume": f"{qty:.8f}",
        "validate": validate,
    }
    return kraken_private("AddOrder", data)

def place_tp_or_sl(side: str, pair_priv: str, qty: float, ordertype: str, price: float, validate: bool):
    """
    ordertype: 'take-profit' ou 'stop-loss'
    side: pour fermer la position, c'est l'inverse du trade initial
    """
    data = {
        "pair": pair_priv,
        "type": side,
        "ordertype": ordertype,            # 'take-profit' | 'stop-loss'
        "price": f"{price:.{PRICE_DEC}f}",
        "volume": f"{qty:.8f}",
        "validate": validate,
    }
    return kraken_private("AddOrder", data)

# ========= Core helpers =========
def calc_qty(price: float) -> float:
    raw = RISK_EUR / max(price, 1e-9)
    raw = round_qty(raw)
    # applique un pas volume si défini
    if QTY_STEP > 0:
        raw = round_step(raw, QTY_STEP)
    return raw

def compute_tp_sl_prices(side_signal: str, entry_price: float):
    if side_signal == "BUY":
        tp = entry_price * (1 + TP_PCT/100.0)
        sl = entry_price * (1 - SL_PCT/100.0)
    else:
        tp = entry_price * (1 - TP_PCT/100.0)
        sl = entry_price * (1 + SL_PCT/100.0)
    return tp, sl

# ========= Routes =========
@app.get("/")
def root_ok():
    return jsonify({"status":"ok"})

@app.get("/health")
def health():
    return jsonify({"status":"ok"})

@app.post("/webhook")
def webhook():
    data = request.get_json(force=True, silent=False)
    app.logger.info(f"Webhook payload: {json.dumps(data, ensure_ascii=False)}")

    signal = (data.get("signal") or "").upper().strip()
    symbol = (data.get("symbol") or f"{BASE}/{QUOTE}").upper().strip()
    tf     = (data.get("timeframe") or data.get("time frame") or "5m").strip()

    if signal not in {"BUY","SELL"}:
        return jsonify({"error":"invalid signal"}), 400

    # Fenêtre horaire (UTC)
    if not in_window_utc(WINDOW_UTC):
        app.logger.info(f"SKIP (outside window {WINDOW_UTC}) {signal} {symbol}")
        return jsonify({"skipped":"outside_window","window":WINDOW_UTC}), 200

    # Normalisation paire
    pair_pub, pair_priv, pair_alt, (base, quote) = normalize_pair(symbol, BASE, QUOTE)

    # Prix & filtres
    try:
        price = fetch_price(pair_pub)
    except Exception:
        # si 'XBT/EUR' rate, tenter la forme sans slash
        price = fetch_price(pair_alt)

    # Filtre EMA
    if EMA_ENABLE:
        interval = tf_to_interval(tf)  # minutes
        closes = fetch_ohlc(pair_pub, interval, limit=max(EMA_SLOW+5, 250))
        if len(closes) < max(EMA_FAST, EMA_SLOW):
            app.logger.info(f"SKIP (not enough data) {signal} {pair_pub}")
            return jsonify({"skipped":"not_enough_data"}), 200
        # on prend la dernière bougie close (pas de repaint)
        ema_fast = ema(closes, EMA_FAST)
        ema_slow = ema(closes, EMA_SLOW)
        last_close = closes[-1]

        # Règle de tendance
        allow_buy  = (last_close > ema_fast) and (ema_fast > ema_slow)
        allow_sell = (last_close < ema_fast) and (ema_fast < ema_slow)

        if (signal == "BUY"  and not allow_buy) or (signal == "SELL" and not allow_sell):
            app.logger.info(f"SKIP (EMA filter) {signal} {pair_pub} close={last_close:.2f} ema{EMA_FAST}={ema_fast:.2f} ema{EMA_SLOW}={ema_slow:.2f}")
            return jsonify({"skipped":"ema_filter","close":last_close,"ema_fast":ema_fast,"ema_slow":ema_slow}), 200

    # Quantité & exécution
    qty = calc_qty(price)

    if PAPER:
        app.logger.info(f"PAPER {signal} {pair_priv} qty={qty} price≈{price} tf={tf}")
        # montrer aussi le TP/SL calculés
        tp, sl = compute_tp_sl_prices(signal, price)
        return jsonify({"paper":True,"signal":signal,"pair":pair_priv,"qty":qty,"price":price,"tp":tp,"sl":sl}), 200

    # ---- Réel (market) ----
    side = "buy" if signal == "BUY" else "sell"
    try:
        entry_res = place_order_market(side, pair_priv, qty, validate=VALIDATE)
        app.logger.info(f"REAL ENTRY {signal} {pair_priv} qty={qty} validate={VALIDATE} RESULT={entry_res}")
    except Exception as e:
        app.logger.exception("Entry order error")
        return jsonify({"error": str(e)}), 500

    # Si on est en dry-run Kraken, s'arrêter là
    if VALIDATE:
        return jsonify({"paper":False,"validate":True,"entry_result":entry_res}), 200

    # ---- Placer TP & SL comme ordres conditionnels séparés ----
    try:
        tp_price, sl_price = compute_tp_sl_prices(signal, price)
        # Opposite side pour sortir
        exit_side = "sell" if signal=="BUY" else "buy"

        tp_res = place_tp_or_sl(exit_side, pair_priv, qty, "take-profit", tp_price, validate=False)
        sl_res = place_tp_or_sl(exit_side, pair_priv, qty, "stop-loss",  sl_price, validate=False)

        app.logger.info(f"REAL EXIT OCO {signal} {pair_priv} TP={tp_price:.{PRICE_DEC}f} SL={sl_price:.{PRICE_DEC}f} TP_RES={tp_res} SL_RES={sl_res}")
        return jsonify({"paper":False,"validate":False,"entry":entry_res,"tp":tp_res,"sl":sl_res}), 200
    except Exception as e:
        app.logger.exception("TP/SL order error")
        return jsonify({"error": str(e), "warning":"entry placed; tp/sl failed"}), 500

# ========= Main =========
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
