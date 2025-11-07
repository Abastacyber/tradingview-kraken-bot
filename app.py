import os, hmac, json, time, math
from flask import Flask, request, jsonify
import ccxt

app = Flask(__name__)

# ---- Secrets / config -------------------------------------------------
SECRET = os.environ.get("WEBHOOK_SECRET") or os.environ.get("SECRET")
if not SECRET:
    raise RuntimeError("WEBHOOK_SECRET/SECRET non défini(e) dans les variables d'environnement")

API_KEY    = os.environ.get("KRAKEN_KEY", "")
API_SECRET = os.environ.get("KRAKEN_SECRET", "")

# ---- Kraken via CCXT --------------------------------------------------
kraken = ccxt.kraken({
    "apiKey": API_KEY,
    "secret": API_SECRET,
    "enableRateLimit": True,
    "timeout": 20000,
})

SYMBOL_MAP = {
    "BTC/EUR": "BTC/EUR",
    "XBT/EUR": "BTC/EUR",   # fallback si TV envoie XBT/EUR
}

MIN_NOTIONAL_EUR = float(os.environ.get("MIN_NOTIONAL_EUR", "20"))
BASE_ORDER_EUR   = float(os.environ.get("BASE_ORDER_EUR",   "25"))

# ---- Helpers ----------------------------------------------------------
def safe_symbol(sym: str) -> str:
    sym = (sym or "").upper().strip()
    return SYMBOL_MAP.get(sym, "BTC/EUR")

def check_secret(incoming: str) -> bool:
    if incoming is None:
        return False
    return hmac.compare_digest(str(incoming), str(SECRET))

def quote_available_eur():
    bal = kraken.fetch_balance()
    eur = bal.get("EUR", {})
    free = eur.get("free", 0) or 0
    return float(free)

def create_market_order(symbol: str, side: str, eur_amount: float):
    if eur_amount < MIN_NOTIONAL_EUR:
        return {"skipped": True, "reason": f"notional<{MIN_NOTIONAL_EUR}"}

    # convertir EUR -> quantité à trader
    ticker = kraken.fetch_ticker(symbol)
    price  = float(ticker["last"])
    amount = eur_amount / price

    # arrondis simples
    amount = float(kraken.amount_to_lots(symbol, amount)) if hasattr(kraken, "amount_to_lots") else round(amount, 8)

    order = kraken.create_order(symbol, "market", side, amount)
    return {"skipped": False, "order": order}

def decide_eur_amount(confidence: int) -> float:
    # ex: 2 -> base, 3 -> 1.5x
    mult = 1.5 if confidence >= 3 else 1.0
    return BASE_ORDER_EUR * mult

# ---- Routes -----------------------------------------------------------
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "ts": int(time.time())})

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        payload = request.get_json(force=True, silent=False) or {}
    except Exception:
        return ("invalid json", 400)

    incoming_secret = payload.get("secret") or request.headers.get("X-Webhook-Secret") or request.args.get("secret")
    if not check_secret(incoming_secret):
        # debug safe: ne logue pas le secret complet
        s = str(incoming_secret or "")
        app.logger.error(f"tv-kraken | Bad secret | got len={len(s)} head='{s[:2]}' tail='{s[-2:] if len(s)>=2 else ''}'")
        return ("Bad secret", 401)

    signal = (payload.get("signal") or "").upper()
    symbol = safe_symbol(payload.get("symbol") or "")
    confidence = int(payload.get("confidence") or 2)

    # BUY / SELL
    if signal not in ("BUY", "SELL"):
        return ("ignored", 200)

    # solde EUR
    try:
        eur_free = quote_available_eur()
    except Exception as e:
        app.logger.error(f"balance error: {e}")
        return ("balance error", 502)

    # montant
    eur_to_spend = decide_eur_amount(confidence)
    if signal == "SELL":
        # pour SELL, on vend une fraction de la position (ex: 100% si force_close)
        # ici simple: on vend le max disponible du base asset
        base = symbol.split("/")[0]
        bal = kraken.fetch_balance().get(base, {})
        amt = float(bal.get("free", 0) or 0)
        if amt <= 0:
            return ("no base to sell", 200)
        try:
            order = kraken.create_order(symbol, "market", "sell", amt)
            app.logger.info(f"sell done | {order.get('id','?')}")
            return jsonify({"ok": True, "side": "sell", "id": order.get("id")})
        except Exception as e:
            app.logger.error(f"sell error: {e}")
            return ("sell error", 502)

    # BUY
    # si pas assez d’EUR, on ajuste ou on skip
    if eur_to_spend > eur_free:
        eur_to_spend = eur_free
    if eur_to_spend < MIN_NOTIONAL_EUR:
        app.logger.info(f"skip buy | eur_to_spend={eur_to_spend} < {MIN_NOTIONAL_EUR}")
        return ("skip small", 200)

    try:
        res = create_market_order(symbol, "buy", eur_to_spend)
        if res.get("skipped"):
            return ("skip small", 200)
        order = res["order"]
        app.logger.info(f"buy done | {order.get('id','?')}")
        return jsonify({"ok": True, "side": "buy", "id": order.get("id")})
    except Exception as e:
        app.logger.error(f"buy error: {e}")
        return ("buy error", 502)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
