import os, time, math, json, datetime as dt
from flask import Flask, request, jsonify

# ==== CONFIGS (EDITABLES) ======================================
PAIR = os.getenv("PAIR", "BTCEUR")            # Kraken pair sans suffixe "XBT" -> "XBT/EUR" côté bourse si besoin
BASE = os.getenv("BASE", "BTC")               # Actif
QUOTE = os.getenv("QUOTE", "EUR")             # Devise de cotation

RISK_PCT          = float(os.getenv("RISK_PCT", "0.01"))   # 1% du solde par trade
FALLBACK_SL_PCT   = float(os.getenv("FALLBACK_SL_PCT","0.6"))   # si TV n'envoie pas: SL 0.6%
FALLBACK_TP_PCT   = float(os.getenv("FALLBACK_TP_PCT","1.2"))   # si TV n'envoie pas: TP 1.2%
FEE_BUFFER_PCT    = float(os.getenv("FEE_BUFFER_PCT","0.15"))   # coussin (frais+slippage) en %
MAX_OPEN_POS      = int(os.getenv("MAX_OPEN_POS", "1"))         # 1 position max
COOLDOWN_SEC      = int(os.getenv("COOLDOWN_SEC", "120"))       # 2 min entre ordres
MAX_DAILY_LOSS_PCT= float(os.getenv("MAX_DAILY_LOSS_PCT","3"))  # stop journalier à -3%
ALERT_PRICE_TOL   = float(os.getenv("ALERT_PRICE_TOL","0.3"))   # tolérance vs prix alerte (0.3%)

# ==== ÉTAT EN MÉMOIRE (simple) =================================
last_trade_ts   = 0
open_position   = None     # {"side":"BUY/SELL","qty":..., "entry":..., "sl":..., "tp":...}
daily_pnl_eur   = 0.0
daily_date      = dt.date.today()

# ==== PLACEHOLDERS KRAKEN ======================================
# -> remplace les fonctions ci-dessous par tes appels réels Kraken si tu as déjà un module prêt.
def kraken_get_balance_eur() -> float:
    # TODO: requête API pour solde EUR + valeur disponible
    return 250.0

def kraken_get_ticker_price(pair: str) -> float:
    # TODO: requête API ticker
    # sécurité: si pas dispo, on utilisera le prix d'alerte reçu
    return None

def kraken_place_market(side: str, pair: str, volume: float) -> dict:
    # TODO: envoi ordre market; retourne {"price": fill_price, "id": order_id}
    return {"price": None, "id": f"DUMMY-{int(time.time())}"}

def kraken_place_oco_close(pair: str, side: str, volume: float, sl_price: float, tp_price: float):
    # TODO: créer OCO (ou 2 ordres liés) stop-loss + take-profit
    return True

def kraken_flatten_all(pair: str):
    # TODO: fermer la position si besoin
    return True

# ==== LOGIQUE RISQUE & CALCULS =================================
def compute_position_size(balance_eur: float, entry: float, sl_pct: float) -> float:
    # risque € = balance * RISK_PCT ; distance SL en €/BTC = entry * sl_pct/100
    risk_eur = balance_eur * RISK_PCT
    dist_eur_per_unit = entry * (sl_pct/100.0)
    if dist_eur_per_unit <= 0:
        return 0.0
    qty = risk_eur / (dist_eur_per_unit * (1 + FEE_BUFFER_PCT/100.0))
    # arrondi typique Kraken (8 décimales pour crypto)
    return max(0.0, math.floor(qty * 1e8) / 1e8)

def clamp_tolerance(alert_px: float, live_px: float) -> bool:
    if live_px is None:
        return True
    tol = ALERT_PRICE_TOL / 100.0
    return abs(live_px - alert_px) <= alert_px * tol

def reset_daily_if_needed():
    global daily_date, daily_pnl_eur
    today = dt.date.today()
    if today != daily_date:
        daily_date = today
        daily_pnl_eur = 0.0

def daily_loss_guard(balance_start: float) -> bool:
    # stop si pertes >= MAX_DAILY_LOSS_PCT du solde de départ jour
    max_loss = balance_start * (MAX_DAILY_LOSS_PCT/100.0)
    return daily_pnl_eur <= -max_loss

# ==== FLASK APP ================================================
app = Flask(__name__)

@app.route("/health")
def health():
    return "ok"

@app.route("/webhook", methods=["POST"])
def webhook():
    global last_trade_ts, open_position, daily_pnl_eur

    try:
        payload = request.get_json(force=True)
    except Exception:
        return jsonify({"error":"invalid json"}), 400

    print("INFO: Webhook payload:", payload, flush=True)

    signal   = str(payload.get("signal","")).upper()    # "BUY" | "SELL"
    symbol   = str(payload.get("symbol",""))
    tf       = str(payload.get("timeframe",""))
    price_tv = float(payload.get("price", 0))

    # SL/TP envoyés par Pine (%). Si absents -> fallback locaux
    sl_pct = float(payload.get("sl", FALLBACK_SL_PCT))
    tp_pct = float(payload.get("tp", FALLBACK_TP_PCT))

    # 1) reset jour + coupe si max perte atteint
    reset_daily_if_needed()
    start_balance = kraken_get_balance_eur()
    if daily_loss_guard(start_balance):
        print("WARN: Daily loss limit reached -> trading disabled for today", flush=True)
        return jsonify({"status":"skipped","reason":"daily_loss_limit"}), 200

    # 2) cooldown
    now = time.time()
    if now - last_trade_ts < COOLDOWN_SEC:
        return jsonify({"status":"skipped","reason":"cooldown"}), 200

    # 3) pas de pyramiding / sens unique
    if open_position is not None:
        return jsonify({"status":"skipped","reason":"position_already_open"}), 200

    # 4) prix live & tolérance
    px_live = kraken_get_ticker_price(PAIR)
    if not clamp_tolerance(price_tv, px_live if px_live else price_tv):
        return jsonify({"status":"skipped","reason":"price_out_of_tolerance"}), 200

    entry_price = px_live if px_live else price_tv
    side = "BUY" if signal=="BUY" else "SELL" if signal=="SELL" else None
    if side is None:
        return jsonify({"status":"ignored","reason":"unknown_signal"}), 200

    # 5) taille de position
    balance = start_balance
    volume  = compute_position_size(balance, entry_price, sl_pct)
    if volume <= 0:
        return jsonify({"status":"skipped","reason":"size_zero"}), 200

    # 6) niveaux SL/TP absolus
    if side == "BUY":
        sl_price = entry_price * (1 - sl_pct/100.0)
        tp_price = entry_price * (1 + tp_pct/100.0)
    else:
        sl_price = entry_price * (1 + sl_pct/100.0)
        tp_price = entry_price * (1 - tp_pct/100.0)

    # 7) envoi ordre + OCO
    res = kraken_place_market(side, PAIR, volume)
    fill_px = res.get("price") or entry_price
    oco_ok  = kraken_place_oco_close(PAIR, side, volume, sl_price, tp_price)

    open_position = {
        "side": side, "qty": volume, "entry": fill_px,
        "sl": sl_price, "tp": tp_price, "opened_at": now
    }
    last_trade_ts = now

    print(f"INFO: OPEN {side} {volume} {BASE} @ {fill_px} | SL {sl_price} | TP {tp_price} | OCO={oco_ok}", flush=True)
    return jsonify({"status":"sent","side":side,"qty":volume,"entry":fill_px,"sl":sl_price,"tp":tp_price}), 200

@app.route("/fill", methods=["POST"])
def on_fill_close():
    """Endpoint (optionnel) si tu fais remonter les fills via un worker/WS.
       Body: {"pnl_eur": +/-, "reason":"tp|sl|manual"}"""
    global open_position, daily_pnl_eur
    data = request.get_json(force=True)
    pnl  = float(data.get("pnl_eur",0))
    daily_pnl_eur += pnl
    print(f"INFO: CLOSE pos -> pnl={pnl}€ | daily_pnl={daily_pnl_eur}€", flush=True)
    open_position = None
    return jsonify({"ok":True}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
