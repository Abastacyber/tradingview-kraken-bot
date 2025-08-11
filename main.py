import os
from flask import Flask, request, jsonify
import krakenex

app = Flask(__name__)

# === Variables d'environnement Render ===
API_KEY = os.getenv("KRAKEN_API_KEY", "")
API_SECRET = os.getenv("KRAKEN_API_SECRET", "")
PAPER_MODE = os.getenv("PAPER_MODE", "1") == "1"   # "1" = paper, "0" = réel

BASE = os.getenv("BASE_SYMBOL", "BTC")
QUOTE = os.getenv("QUOTE_SYMBOL", "EUR")
RISK_EUR_PER_TRADE = float(os.getenv("RISK_EUR_PER_TRADE", "25"))

# Client Kraken (sera utilisé si PAPER_MODE = False)
api = krakenex.API(API_KEY, API_SECRET)

# Mapping simple des paires Kraken
def kraken_pair_code(base: str, quote: str) -> str:
    mapping = {
        ("BTC", "EUR"): "XXBTZEUR",
        ("ETH", "EUR"): "XETHZEUR",
        ("BTC", "USD"): "XXBTZUSD",
        ("ETH", "USD"): "XETHZUSD",
    }
    return mapping.get((base.upper(), quote.upper()), f"X{base.upper()}Z{quote.upper()}")

PAIR = kraken_pair_code(BASE, QUOTE)

# --- Health / infos ---
@app.get("/")
def health():
    return "ok", 200

@app.get("/whoami")
def whoami():
    return jsonify({
        "pair": PAIR,
        "base": BASE,
        "quote": QUOTE,
        "paper_mode": PAPER_MODE,
        "risk_eur_per_trade": RISK_EUR_PER_TRADE
    }), 200

# --- Webhook test GET (pratique sur mobile) ---
@app.get("/webhook_test")
def webhook_test():
    sig = request.args.get("signal", "BUY").upper()
    data = {"signal": sig, "symbol": f"{BASE}{QUOTE}", "price": None}
    return handle_signal(data)

# --- Webhook officiel (POST depuis TradingView) ---
@app.post("/webhook")
def webhook():
    data = request.get_json(force=True, silent=True) or {}
    print(">>> Webhook reçu:", data)
    return handle_signal(data)

# --- Traitement commun ---
def handle_signal(data: dict):
    signal = (data.get("signal") or "").upper()
    price_raw = data.get("price")
    price = None

    # Normalise le prix (Android/FR peut envoyer des virgules)
    if isinstance(price_raw, (int, float)):
        price = float(price_raw)
    elif isinstance(price_raw, str) and price_raw.strip():
        price = float(price_raw.replace(",", "."))

    # On n'accepte que ces signaux
    if signal not in {"BUY", "SELL", "BUY_CONFIRM", "SELL_CONFIRM"}:
        return jsonify({"status": "ignored", "reason": "unknown signal"}), 200

    side = "buy" if signal.startswith("BUY") else "sell"

    # Taille de position (approximative) : risk_eur / price
    if price and price > 0:
        volume = round(RISK_EUR_PER_TRADE / price, 6)
    else:
        volume = 0.00025  # micro-volume par défaut si pas de prix

    # Mode papier : on n'envoie pas à Kraken
    if PAPER_MODE:
        print(f"[PAPER] {side.upper()} {PAIR} volume={volume} price={price}")
        return jsonify({
            "status": "paper_ok",
            "pair": PAIR,
            "side": side,
            "volume": volume,
            "price": price
        }), 200

    # Réel : ordre market
    try:
        payload = {
            "pair": PAIR,
            "type": side,
            "ordertype": "market",
            "volume": str(volume),
        }
        resp = api.query_private("AddOrder", payload)
        print("Kraken AddOrder resp:", resp)
        return jsonify({"status": "ok", "kraken": resp}), 200
    except Exception as e:
        print("kraken error:", e)
        return jsonify({"status": "error", "message": str(e)}), 500

# --- Lancement local (Render utilise gunicorn) ---
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))            raise RuntimeError(f"AssetPairs error: {resp}")

        wanted_alt = f"{base}{quote}"          # ex: BTCEUR
        wanted_ws = f"{base}/{quote}"          # ex: BTC/EUR

        for pair_code, meta in resp["result"].items():
            alt = str(meta.get("altname", "")).upper()
            ws = str(meta.get("wsname", "")).upper()
            if alt == wanted_alt or ws == wanted_ws:
                _PAIR_CACHE = pair_code
                print(f"[PAIR] Resolved {base}-{quote} -> {pair_code} (alt={alt}, ws={ws})")
                return pair_code

        # Fallback minimal (peu probable d'être nécessaire)
        raise RuntimeError(f"No Kraken pair found for {wanted_alt}/{wanted_ws}")

    except Exception as e
