import os
import json
import logging
from decimal import Decimal, ROUND_DOWN
from flask import Flask, request, jsonify
import krakenex

# === CONFIG LOGGING ===
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === CONFIG ENV ===
KRAKEN_API_KEY = os.getenv("KRAKEN_API_KEY")
KRAKEN_API_SECRET = os.getenv("KRAKEN_API_SECRET")

SIZE_MODE = os.getenv("SIZE_MODE", "fixed_eur").lower()
FIXED_EUR_PER_TRADE = Decimal(os.getenv("FIXED_EUR_PER_TRADE", "5"))
MAX_EUR_PER_TRADE   = Decimal(os.getenv("MAX_EUR_PER_TRADE", "5"))
QUOTE = os.getenv("QUOTE", "EUR").upper()

ALERT_PRICE_TOL = float(os.getenv("ALERT_PRICE_TOL", "0.3"))
COOLDOWN_SEC = int(os.getenv("COOLDOWN_SEC", "120"))

FALLBACK_SL_PCT = Decimal(os.getenv("FALLBACK_SL_PCT", "0.6"))
FALLBACK_TP_PCT = Decimal(os.getenv("FALLBACK_TP_PCT", "1.2"))
FEE_BUFFER_PCT  = Decimal(os.getenv("FEE_BUFFER_PCT", "0.15"))

# === KRAKEN API ===
kraken = krakenex.API(key=KRAKEN_API_KEY, secret=KRAKEN_API_SECRET)

# === FLASK ===
app = Flask(__name__)

# === UTILS ===
def truncate_qty(qty: Decimal, decimals: int = 8) -> Decimal:
    """Tronque sans arrondir (exigence Kraken)."""
    q = Decimal(10) ** -decimals
    return (qty // q) * q

def compute_order_qty(entry_price: Decimal) -> Decimal:
    """Calcule la quantité BASE à acheter/vendre selon SIZE_MODE."""
    # Montant € visé
    if SIZE_MODE == "fixed_eur":
        notional_eur = FIXED_EUR_PER_TRADE
    else:
        raise ValueError("Seul fixed_eur est implémenté pour l’instant.")

    # Coupe-circuit
    if MAX_EUR_PER_TRADE > 0 and notional_eur > MAX_EUR_PER_TRADE:
        notional_eur = MAX_EUR_PER_TRADE

    qty = truncate_qty(notional_eur / entry_price, decimals=8)

    if qty <= Decimal("0"):
        raise ValueError("Qty calculée <= 0, vérifie FIXED_EUR_PER_TRADE et price.")

    logger.info(f"SIZING | mode={SIZE_MODE}, notional≈{notional_eur} {QUOTE}, "
                f"price={entry_price} → qty={qty}")
    return qty

# === ENDPOINT ===
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True)
    logger.info(f"Webhook reçu: {data}")

    try:
        signal = data.get("signal")
        symbol = data.get("symbol")     # ex. "KRAKEN:BTCEUR"
        price  = Decimal(str(data.get("price")))
    except Exception as e:
        logger.error(f"Erreur parsing data: {e}")
        return jsonify({"status": "error", "msg": "invalid payload"}), 400

    try:
        qty = compute_order_qty(entry_price=price)
    except Exception as e:
        logger.error(f"Erreur sizing: {e}")
        return jsonify({"status": "error", "msg": str(e)}), 400

    # SL / TP par défaut
    sl_price = (price * (1 - FALLBACK_SL_PCT/100)).quantize(Decimal("0.1"))
    tp_price = (price * (1 + FALLBACK_TP_PCT/100)).quantize(Decimal("0.1"))

    side = "buy" if signal.upper() == "BUY" else "sell"

    logger.info(f"ORDER {side.upper()} {qty} {symbol} @ {price} "
                f"| SL {sl_price} | TP {tp_price}")

    # === Exemple appel Kraken (à adapter si tu utilises krakenex ou python-kraken-sdk) ===
    try:
        order = kraken.query_private('AddOrder', {
            'pair': symbol.replace("KRAKEN:", ""),
            'type': side,
            'ordertype': 'limit',
            'price': str(price),
            'volume': str(qty),
            'oflags': 'fciq',  # optional
        })
        logger.info(f"Réponse Kraken: {order}")
        return jsonify({"status": "sent", "order": order})
    except Exception as e:
        logger.error(f"Erreur envoi ordre Kraken: {e}")
        return jsonify({"status": "error", "msg": str(e)}), 500

@app.route("/")
def home():
    return "Bot Kraken is live"

# === MAIN ===
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
