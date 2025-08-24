import os
import time
import math
import json
from datetime import datetime, timezone
from flask import Flask, request, jsonify
import krakenex

app = Flask(__name__)

# ======== ENV ========
BASE = os.getenv("BASE", "BTC").upper()
QUOTE = os.getenv("QUOTE", "EUR").upper()
ORDER_TYPE = os.getenv("ORDER_TYPE", "market").lower()

AUTO_SIZE = os.getenv("AUTO_SIZE", "true").lower() == "true"
FIXED_EUR_PER_TRADE = float(os.getenv("FIXED_EUR_PER_TRADE", "50"))

MAX_EUR_PER_TRADE = float(os.getenv("MAX_EUR_PER_TRADE", "50"))
MIN_EUR_PER_TRADE = float(os.getenv("MIN_EUR_PER_TRADE", "10"))

QUOTE_RESERVE_EUR = float(os.getenv("QUOTE_RESERVE_EUR", "0"))
BASE_RESERVE_EUR = float(os.getenv("BASE_RESERVE_EUR", "0"))

COOLDOWN_SEC = int(os.getenv("COOLDOWN_SEC", "600"))

API_KEY = os.getenv("KRAKEN_API_KEY", "")
API_SECRET = os.getenv("KRAKEN_API_SECRET", "")

# ======== STATE ========
last_alert_ts = 0

# ======== KRAKEN ========
api = krakenex.API()
if API_KEY and API_SECRET:
    api.key = API_KEY
    api.secret = API_SECRET

# Kraken codes (actifs/pairs)
def asset_code(sym: str) -> str:
    s = sym.upper()
    if s == "BTC": return "XXBT"
    if s == "ETH": return "XETH"
    if s == "EUR": return "ZEUR"
    return s

def pair_code(base: str, quote: str) -> str:
    return f"{asset_code(base)}{asset_code(quote)}"  # ex: XXBTZEUR

PAIR = pair_code(BASE, QUOTE)      # ex: XXBTZEUR
PAIR_HUMAN = f"{BASE}{QUOTE}"      # ex: BTCEUR

# ======== HELPERS ========
def now_utc_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def get_pair_info(pair_code: str):
    """Retourne ordermin & lot_decimals pour le pair (depuis AssetPairs)."""
    r = api.query_public('AssetPairs', {'pair': pair_code})
    if 'result' not in r or not r['result']:
        raise RuntimeError(f"AssetPairs error: {r}")
    info = list(r['result'].values())[0]
    lot_decimals = info.get('lot_decimals', 8)
    ordmin = float(info.get('ordermin', '0.00005'))
    return lot_decimals, ordmin

def get_price(pair_code: str) -> float:
    r = api.query_public('Ticker', {'pair': pair_code})
    if 'result' not in r or not r['result']:
        raise RuntimeError(f"Ticker error: {r}")
    k = list(r['result'].keys())[0]
    price = float(r['result'][k]['c'][0])  # dernière transaction
    return price

def get_balances():
    r = api.query_private('Balance')
    if 'result' not in r:
        raise RuntimeError(f"Balance error: {r}")
    res = r['result']
    base_bal = float(res.get(asset_code(BASE), '0'))
    quote_bal = float(res.get(asset_code(QUOTE), '0'))
    return base_bal, quote_bal

def round_volume(vol: float, lot_decimals: int) -> float:
    if vol <= 0: return 0.0
    p = 10 ** lot_decimals
    return math.floor(vol * p) / p  # arrondi par défaut vers le bas

def eur_to_btc(eur: float, px: float) -> float:
    return 0.0 if px <= 0 else eur / px

def btc_to_eur(btc: float, px: float) -> float:
    return btc * px

def log(msg: str):
    app.logger.info(f"{now_utc_iso()} [INFO] {msg}")

def log_err(msg: str):
    app.logger.error(f"{now_utc_iso()} [ERROR] {msg}")

# ======== CORE ORDER LOGIC ========
def place_order(side: str, volume: float):
    """Envoie l’ordre market sur Kraken."""
    data = {
        'pair': PAIR,
        'type': side,                 # 'buy' or 'sell'
        'ordertype': ORDER_TYPE,      # 'market'
        'volume': f"{volume:.10f}",   # string
        # 'validate': True,           # utile pour debug à blanc
    }
    resp = api.query_private('AddOrder', data)
    return resp

def compute_buy_volume(px: float, lot_decimals: int, ordmin: float):
    # Solde EUR - réserve
    base_bal, quote_bal = get_balances()
    avail_eur = max(0.0, quote_bal - QUOTE_RESERVE_EUR)

    if AUTO_SIZE:
        budget_eur = min(avail_eur, MAX_EUR_PER_TRADE)
        if budget_eur < MIN_EUR_PER_TRADE:
            return 0.0, "BUY skipped: EUR dispo < MIN_EUR_PER_TRADE"
    else:
        budget_eur = min(avail_eur, FIXED_EUR_PER_TRADE)
        if budget_eur <= 0:
            return 0.0, "BUY skipped: pas d'EUR dispo"

    vol = eur_to_btc(budget_eur, px)
    if vol < ordmin:
        return 0.0, f"BUY skipped: volume {vol:.8f} < ordmin {ordmin:.8f}"

    vol = round_volume(vol, lot_decimals)
    if vol < ordmin:
        return 0.0, f"BUY skipped: volume arrondi {vol:.8f} < ordmin {ordmin:.8f}"

    return vol, f"BUY budget≈{budget_eur:.2f}€, vol≈{vol:.8f} BTC"

def compute_sell_volume(px: float, lot_decimals: int, ordmin: float):
    base_bal, quote_bal = get_balances()

    # Réserve base exprimée en BTC à partir d’une valeur EUR
    base_reserve_btc = eur_to_btc(BASE_RESERVE_EUR, px) if BASE_RESERVE_EUR > 0 else 0.0
    avail_btc = max(0.0, base_bal - base_reserve_btc)
    if avail_btc <= 0:
        return 0.0, "SELL skipped: pas de BTC dispo (réserve incluse)"

    vol = round_volume(avail_btc, lot_decimals)
    if vol < ordmin:
        return 0.0, f"SELL skipped: volume {vol:.8f} < ordmin {ordmin:.8f}"

    return vol, f"SELL vol≈{vol:.8f} BTC (reste réserve incluse)"

def open_order(signal: str):
    px = get_price(PAIR)
    lot_decimals, ordmin = get_pair_info(PAIR)

    if signal == "BUY":
        vol, note = compute_buy_volume(px, lot_decimals, ordmin)
        log(f"{PAIR_HUMAN}: {note} | px≈{px}")
        if vol > 0:
            resp = place_order('buy', vol)
            if resp.get('error'):
                log_err(f"Kraken BUY error: {resp['error']}")
            else:
                log(f"==> BUY sent: {vol:.8f} BTC @~{px}")
        return

    if signal == "SELL":
        vol, note = compute_sell_volume(px, lot_decimals, ordmin)
        log(f"{PAIR_HUMAN}: {note} | px≈{px}")
        if vol > 0:
            resp = place_order('sell', vol)
            if resp.get('error'):
                log_err(f"Kraken SELL error: {resp['error']}")
            else:
                log(f"==> SELL sent: {vol:.8f} BTC @~{px}")
        return

    log(f"Signal inconnu: {signal}")

# ======== WEBHOOK ========
@app.route("/webhook", methods=["POST"])
def webhook():
    global last_alert_ts
    try:
        data = request.get_json(force=True, silent=False)
        log(f"Raw alert: {json.dumps(data)}")

        signal = str(data.get("signal", "")).upper().strip()
        # filtrage symbole/timeframe si tu veux, ici on traite tout

        # Cooldown
        now = time.time()
        if now - last_alert_ts < COOLDOWN_SEC:
            log("Cooldown actif -> alerte ignorée")
            return jsonify({"ok": True, "skipped": "cooldown"}), 200

        # Ouvre l'ordre
        open_order(signal)

        last_alert_ts = now
        return jsonify({"ok": True}), 200

    except Exception as e:
        log_err(f"Webhook exception: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

# ======== HEALTH ========
@app.route("/health")
def health():
    return "OK", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
