# app.py
import os
import logging
from flask import Flask, request, jsonify
import ccxt

# ----------------- LOGS -----------------
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("tv-phemex")

# ----------------- FLASK -----------------
app = Flask(_name_)

# ----------------- ENV -------------------
API_KEY    = os.getenv("PHEMEX_API_KEY", "")
API_SECRET = os.getenv("PHEMEX_API_SECRET", "")
# spot par défaut ; change en 'swap' si tu veux les perp/derivs
DEFAULT_TYPE = os.getenv("PHEMEX_DEFAULT_TYPE", "spot").lower()

if not API_KEY or not API_SECRET:
    log.warning("Clés API Phemex absentes. Renseigne PHEMEX_API_KEY et PHEMEX_API_SECRET dans Render.")

# ----------------- CCXT ------------------
# Initialise l’exchange au démarrage (réutilisé à chaque requête)
exchange = ccxt.phemex({
    "apiKey": API_KEY,
    "secret": API_SECRET,
    "enableRateLimit": True,
    "options": {
        "defaultType": DEFAULT_TYPE,   # 'spot' ou 'swap'
    },
})

def normalize_symbol(sym: str) -> str:
    """
    Accepte 'BTCUSDT' ou 'BTC/USDT' → 'BTC/USDT' (format ccxt).
    """
    if not sym:
        return ""
    s = sym.upper().strip()
    if "/" not in s and len(s) >= 6:
        # insère un slash avant le quote courant le plus probable
        # Ici on gère surtout USDT, USD, USDC, USDⓈM cases
        for quote in ("USDT", "USDC", "USD"):
            if s.endswith(quote):
                base = s[: -len(quote)]
                return f"{base}/{quote}"
        # fallback: si pas reconnu on laisse tel quel
        return s
    return s.replace(" ", "")

def parse_side(signal_value: str) -> str:
    """
    BUY/SELL flexible (buy, BUY, sell, SELL).
    Retourne 'buy' ou 'sell' ou '' si invalide.
    """
    if not signal_value:
        return ""
    s = signal_value.strip().lower()
    if s in ("buy", "long"):
        return "buy"
    if s in ("sell", "short"):
        return "sell"
    return ""

def parse_quantity(value, fallback=None) -> float:
    """
    Quantité flexible :
    - accepte float/int
    - accepte string "0.001" ou "0,001" (remplace la virgule)
    - si None → essaie fallback (ex: 'volume'), sinon 0.001
    """
    if value is None:
        value = fallback
    if value is None:
        return 0.001
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        v = value.strip().replace(",", ".")
        try:
            return float(v)
        except Exception:
            return 0.001
    # dernier recours
    return 0.001

# --------------- ROUTES ------------------
@app.get("/")
def root():
    return "OK", 200

@app.get("/health")
def health():
    return jsonify({"status": "ok"}), 200

@app.post("/webhook")
def webhook():
    try:
        data = request.get_json(force=True, silent=True) or {}
        log.info("Payload reçu: %s", data)

        # --- lecture/normalisation champs ---
        raw_symbol = data.get("symbol", "") or data.get("sym", "")
        symbol = normalize_symbol(raw_symbol)

        side = parse_side(data.get("signal") or data.get("action"))
        qty  = parse_quantity(data.get("quantity"), fallback=data.get("volume"))

        # validations
        if not symbol:
            return jsonify({"status": "error", "message": "Symbole manquant"}), 400
        if not side:
            return jsonify({"status": "error", "message": "Action/Signal invalide (attendu: BUY ou SELL)"}), 400
        if qty <= 0:
            return jsonify({"status": "error", "message": "Quantité doit être > 0"}), 400

        # Prépare ccxt
        if not exchange.markets:
            exchange.load_markets()

        # ccxt attend 'BASE/QUOTE' ; on s’assure du format
        if symbol not in exchange.markets:
            # Essai supplémentaire : s’il a envoyé 'BTCUSDT' et que normalize n’a pas trouvé,
            # on tente 'BTC/USDT'
            alt = normalize_symbol(symbol)
            if alt in exchange.markets:
                symbol = alt
            else:
                return jsonify({
                    "status": "error",
                    "message": f"Symbole inconnu pour Phemex: {symbol}"
                }), 400

        # --------- passage d’ordre ----------
        # Spot market order (amount = quantité en BASE)
        order = exchange.create_order(
            symbol=symbol,
            type="market",
            side=side,
            amount=qty
        )
        log.info("Ordre exécuté: %s", order)

        return jsonify({
            "status": "ok",
            "exchange": "phemex",
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "order_id": order.get("id"),
            "filled": order.get("filled"),
            "cost": order.get("cost"),
        }), 200

    except ccxt.InsufficientFunds as e:
        log.error("Fonds insuffisants: %s", e)
        return jsonify({"status": "error", "message": "Fonds insuffisants sur Phemex"}), 400
    except ccxt.BaseError as e:
        log.exception("Erreur CCXT/Phemex")
        return jsonify({"status": "error", "message": f"Erreur exchange: {str(e)}"}), 500
    except Exception as e:
        log.exception("Erreur inattendue")
        return jsonify({"status": "error", "message": f"Erreur serveur: {str(e)}"}), 500

# --------------- LANCEUR LOCAL (facultatif) ---------------
if __name__ == "__main__":
    # Render utilise gunicorn ; ce bloc ne sert que pour tests en local
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
