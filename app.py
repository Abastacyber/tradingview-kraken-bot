# app.py — Webhook TradingView -> Kraken (avec auto-réduction et auto-topup)
import os, math, json, logging, time
from flask import Flask, request, jsonify
import krakenex

# ------------ LOGGING ------------
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("tv-kraken")

# ------------ CONFIG ENV ------------
KRAKEN_API_KEY    = os.getenv("KRAKEN_API_KEY", "")
KRAKEN_API_SECRET = os.getenv("KRAKEN_API_SECRET", "")

# Pair & assets (Kraken: BTC s'écrit XBT)
PAIR  = os.getenv("PAIR", "XBTEUR")   # marché visé
BASE  = os.getenv("BASE", "XXBT")     # code solde BTC chez Kraken
QUOTE = os.getenv("QUOTE", "ZEUR")    # code solde EUR chez Kraken

# Sizing & sécurité
FIXED_EUR_PER_TRADE = float(os.getenv("FIXED_EUR_PER_TRADE", "50"))  # ticket cible en €
FEE_BUFFER_PCT      = float(os.getenv("FEE_BUFFER_PCT", "0.002"))    # 0.2% marge frais
MAX_OPEN_POS        = int(os.getenv("MAX_OPEN_POS", "1"))            # pour extension si besoin

# Auto-top-up (combler BTC manquant avant un SELL)
AUTO_TOPUP      = os.getenv("AUTO_TOPUP", "true").lower() == "true"
TOPUP_EUR_LIMIT = float(os.getenv("TOPUP_EUR_LIMIT", "30"))          # € max pour combler
BTC_RESERVE     = float(os.getenv("BTC_RESERVE", "0.00005"))         # petit coussin BTC à garder

# Cooldown entre alertes (évite spam)
COOLDOWN_SEC = int(os.getenv("COOLDOWN_SEC", "900"))                 # 15 min
last_alert_ts = 0

# ------------ KRAKEN API ------------
api = krakenex.API(key=KRAKEN_API_KEY, secret=KRAKEN_API_SECRET)

def get_balances():
    """Retourne (EUR, BTC) disponibles sur le compte."""
    r = api.query_private("Balance")
    if r.get("error"):
        log.error(f"Kraken Balance ERROR: {r['error']}")
        return 0.0, 0.0
    b = r["result"]
    eur = float(b.get(QUOTE, 0))
    btc = float(b.get(BASE, 0))
    return eur, btc

def get_pair_info():
    """Récupère lot_decimals (pas de volume) et ordermin de la paire."""
    r = api.query_public("AssetPairs", {"pair": PAIR})
    if r.get("error"):
        log.error(f"Kraken AssetPairs ERROR: {r['error']}")
        # valeurs de secours raisonnables pour XBTEUR
        return 8, 0.00002
    p = next(iter(r["result"].values()))
    lot_dec = p.get("lot_decimals", 8)
    ordermin = float(p.get("ordermin", "0.00002"))
    return lot_dec, ordermin

LOT_DEC, ORDERMIN = get_pair_info()

def round_qty(q):
    """Arrondi vers le bas au pas d'incrément imposé par la paire."""
    step = 10 ** (-LOT_DEC)
    return max(0.0, math.floor(q / step) * step)

def open_buy(price: float):
    """Ouvre un BUY en fonction du cash dispo, en respectant ordermin et les frais."""
    eur, btc = get_balances()
    spendable = min(FIXED_EUR_PER_TRADE, eur * (1.0 - FEE_BUFFER_PCT))
    if spendable < 5:
        log.info(f"BUY skipped: EUR insuffisants ({eur:.2f}€)")
        return

    qty = round_qty(spendable / price)
    if qty < ORDERMIN:
        log.info(f"BUY réduit mais < ordermin ({qty} < {ORDERMIN}) -> skipped")
        return

    log.info(f"==> ORDER BUY {qty:.8f} {PAIR} (~{spendable:.2f}€) @ ~{price}")
    r = api.query_private("AddOrder", {
        "pair": PAIR, "type": "buy", "ordertype": "market", "volume": f"{qty:.8f}"
    })
    if r.get("error"):
        log.error(f"Kraken ERROR (BUY): {r['error']}")

def open_sell(price: float):
    """Ouvre un SELL; si BTC insuffisants, top-up auto dans la limite TOPUP_EUR_LIMIT."""
    eur, btc = get_balances()
    sellable = max(0.0, btc - BTC_RESERVE)

    # Si on n'atteint pas le minimum, tenter de combler
    if sellable < ORDERMIN:
        missing = (ORDERMIN + BTC_RESERVE) - btc
        if AUTO_TOPUP and missing > 0:
            need_eur = missing * price / (1.0 - FEE_BUFFER_PCT)
            buy_eur = min(need_eur, TOPUP_EUR_LIMIT, eur * (1.0 - FEE_BUFFER_PCT))
            if buy_eur >= 5:
                qty_buy = round_qty(buy_eur / price)
                if qty_buy >= ORDERMIN:
                    log.info(f"AUTO-TOPUP: achat {qty_buy:.8f} BTC (~{buy_eur:.2f}€) pour combler avant SELL")
                    r = api.query_private("AddOrder", {
                        "pair": PAIR, "type": "buy", "ordertype": "market", "volume": f"{qty_buy:.8f}"
                    })
                    if r.get("error"):
                        log.error(f"Kraken ERROR (TOPUP): {r['error']}")
                    # refresh balances
                    eur, btc = get_balances()
                    sellable = max(0.0, btc - BTC_RESERVE)

    qty = round_qty(sellable)
    if qty < ORDERMIN:
        log.info(f"SELL skipped: BTC insuffisants (vendable={qty} < ordermin={ORDERMIN})")
        return

    log.info(f"==> ORDER SELL {qty:.8f} {PAIR} @ ~{price}")
    r = api.query_private("AddOrder", {
        "pair": PAIR, "type": "sell", "ordertype": "market", "volume": f"{qty:.8f}"
    })
    if r.get("error"):
        log.error(f"Kraken ERROR (SELL): {r['error']}")

def handle_signal(signal: str, price: float):
    """Dispatch principal pour BUY/SELL."""
    if signal == "BUY":
        open_buy(price)
    elif signal == "SELL":
        open_sell(price)
    else:
        log.info(f"Signal ignoré: {signal}")

# ------------ FLASK ------------
app = Flask(__name__)

@app.get("/health")
def health():
    return "ok", 200

@app.post("/webhook")
def webhook():
    global last_alert_ts
    data = request.get_json(silent=True) or {}
    log.info(f"Raw alert: {json.dumps(data)}")

    # Cooldown
    now = time.time()
    if now - last_alert_ts < COOLDOWN_SEC:
        log.info("Cooldown actif -> alerte ignorée")
        return jsonify({"status": "cooldown"}), 200

    # Lecture alertes TradingView (format JSON envoyé par Pine)
    signal = str(data.get("signal", "")).upper()
    price  = float(data.get("price", 0) or 0)

    # Tolérance: si pas de prix fourni, on tente de récupérer le ticker
    if price <= 0:
        # récupération rapide côté public
        r = api.query_public("Ticker", {"pair": PAIR})
        try:
            price = float(next(iter(r["result"].values()))["c"][0])
        except Exception:
            log.error("Impossible d’obtenir le prix – alerte ignorée")
            return jsonify({"status": "no_price"}), 200

    log.info(f"Alert price={price:.2f} | PAIR={PAIR} | ordermin={ORDERMIN}, lot_dec={LOT_DEC}")

    # Exécution
    handle_signal(signal, price)

    last_alert_ts = now
    return jsonify({"ok": True}), 200

# Render lancera via gunicorn, pas besoin de app.run()
