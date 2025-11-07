import os, json, time, math, hmac, hashlib
from flask import Flask, request, jsonify
import ccxt

app = Flask(__name__)

# ---------- ENV VARS (rétro-compat) ----------
SECRET = (os.getenv("WEBHOOK_SECRET") or
          os.getenv("SECRET") or
          os.getenv("WEBHOOKSECRET") or "")

API_KEY    = (os.getenv("KRAKEN_KEY") or
              os.getenv("API_KEY") or
              os.getenv("KRAKEN_API_KEY") or "")
API_SECRET = (os.getenv("KRAKEN_SECRET") or
              os.getenv("API_SECRET") or
              os.getenv("KRAKEN_API_SECRET") or "")

BASE_ORDER_EUR   = float(os.getenv("BASE_ORDER_EUR", "25"))     # ticket d’achat
MIN_NOTIONAL_EUR = float(os.getenv("MIN_NOTIONAL_EUR", "15"))   # coût min
SYMBOL_FALLBACK  = os.getenv("SYMBOL_FALLBACK", "BTC/EUR")

def _mask(s: str) -> str:
    s = str(s or "")
    return (s[:2] + "…" + s[-2:]) if len(s) >= 6 else ("set" if s else "missing")

if not SECRET:
    # on fail fast pour repérer un secret manquant dès le boot
    raise RuntimeError("WEBHOOK_SECRET/SECRET manquant")

if not API_KEY or not API_SECRET:
    raise RuntimeError(f"KRAKEN_KEY/API_KEY ou KRAKEN_SECRET/API_SECRET manquants "
                       f"(key={_mask(API_KEY)}, sec={_mask(API_SECRET)})")

# ---------- CCXT KRAKEN ----------
kraken = ccxt.kraken({
    "apiKey": API_KEY,
    "secret": API_SECRET,
    "enableRateLimit": True,
    "timeout": 20000,
    "options": {"defaultType": "spot"},
})

# ---------- Helpers ----------
def normalize_symbol(sym: str) -> str:
    """Accepte XBT/EUR, BTCEUR, btc-eur… et renvoie BTC/EUR."""
    if not sym:
        return SYMBOL_FALLBACK
    s = sym.upper().replace("-", "/")
    s = s.replace("XBT", "BTC")      # unifie le nom Kraken/TV
    if "/" not in s and len(s) >= 6:
        # formats collés: BTCEUR, ETHEUR…
        s = s[:-3] + "/" + s[-3:]
    return s

def amount_from_eur(symbol: str, eur: float) -> float:
    """Convertit un budget EUR en quantité base, arrondie à la précision marché."""
    ticker = kraken.fetch_ticker(symbol)
    price  = float(ticker["last"] or ticker["close"] or ticker["bid"])
    mkt    = kraken.market(symbol)
    raw_amt = eur / price
    return float(kraken.amount_to_precision(symbol, raw_amt)), price, mkt

def min_amount(symbol: str) -> float:
    mkt = kraken.market(symbol)
    lim = (mkt.get("limits") or {}).get("amount") or {}
    return float(lim.get("min") or 0.00001)

def min_cost(symbol: str) -> float:
    mkt = kraken.market(symbol)
    lim = (mkt.get("limits") or {}).get("cost") or {}
    return float(lim.get("min") or MIN_NOTIONAL_EUR)

def free_base(symbol: str) -> float:
    base = symbol.split("/")[0]
    bal  = kraken.fetch_free_balance()
    return float(bal.get(base, 0) or 0)

# ---------- Endpoints ----------
@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "exchange": "kraken",
        "symbol_fallback": SYMBOL_FALLBACK,
        "env": {
            "WEBHOOK_SECRET": "set" if SECRET else "missing",
            "API_KEY": _mask(API_KEY),
            "API_SECRET": _mask(API_SECRET),
        },
        "ts": int(time.time()*1000),
    })

# Diag protégé: /diag?secret=xxxxx
@app.get("/diag")
def diag():
    if request.args.get("secret") != SECRET:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        markets = kraken.load_markets()
        bal = kraken.fetch_balance()
        return jsonify({
            "ok": True,
            "markets_loaded": len(markets),
            "eur_free": bal.get("EUR", {}).get("free", 0),
            "ts": int(time.time()*1000),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.post("/webhook")
def webhook():
    try:
        payload = request.get_json(force=True, silent=False) or {}
    except Exception:
        return jsonify({"ok": False, "error": "invalid-json"}), 400

    # --- Secret simple (égalité) ---
    if str(payload.get("secret", "")) != SECRET:
        app.logger.error("Bad secret")
        return jsonify({"ok": False, "error": "bad-secret"}), 401

    signal = str(payload.get("signal", "")).upper()  # BUY / SELL
    symbol_in = payload.get("symbol") or SYMBOL_FALLBACK
    symbol = normalize_symbol(symbol_in)

    try:
        kraken.load_markets()
    except Exception as e:
        app.logger.exception("load_markets failed")
        return jsonify({"ok": False, "error": f"markets: {e}"}), 502

    # --- BUY ---
    if signal == "BUY":
        try:
            min_cost_req = min_cost(symbol)
            budget = max(BASE_ORDER_EUR, min_cost_req)

            amount, price, mkt = amount_from_eur(symbol, budget)
            amount = max(amount, min_amount(symbol))
            if budget < min_cost_req:
                budget = min_cost_req  # sécurité si marché impose un cost plus élevé

            # Vérifie solde EUR
            bal = kraken.fetch_free_balance()
            eur_free = float(bal.get("EUR", 0) or 0)
            if eur_free + 1e-6 < budget:
                return jsonify({"ok": False, "skipped": "insufficient-eur",
                                "eur_free": eur_free, "need": budget}), 200

            order = kraken.create_order(symbol, "market", "buy", amount)
            return jsonify({"ok": True, "side": "buy", "symbol": symbol,
                            "amount": amount, "price": price, "order": order})
        except Exception as e:
            app.logger.exception("buy error")
            return jsonify({"ok": False, "error": str(e)}), 500

    # --- SELL ---
    if signal == "SELL":
        try:
            base_free = free_base(symbol)
            min_amt = min_amount(symbol)
            sell_amt = max(0.0, base_free - min_amt*0.1)  # garde un poil de dust
            if sell_amt < min_amt:
                return jsonify({"ok": False, "skipped": "insufficient-base",
                                "base_free": base_free, "min": min_amt}), 200
            order = kraken.create_order(symbol, "market", "sell", float(kraken.amount_to_precision(symbol, sell_amt)))
            return jsonify({"ok": True, "side": "sell", "symbol": symbol,
                            "amount": float(kraken.amount_to_precision(symbol, sell_amt)), "order": order})
        except Exception as e:
            app.logger.exception("sell error")
            return jsonify({"ok": False, "error": str(e)}), 500

    return jsonify({"ok": False, "error": f"unknown-signal:{signal}"}), 400

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
