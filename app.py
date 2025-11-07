import os, time
from flask import Flask, request, jsonify
import ccxt

app = Flask(__name__)

# ───────────── ENV vars (rétro-compat) ─────────────
SECRET = (
    os.getenv("WEBHOOK_SECRET")
    or os.getenv("SECRET")
    or os.getenv("WEBHOOKSECRET")
    or ""
)

API_KEY = (
    os.getenv("KRAKEN_KEY")
    or os.getenv("API_KEY")
    or os.getenv("KRAKEN_API_KEY")
    or ""
)

API_SECRET = (
    os.getenv("KRAKEN_SECRET")
    or os.getenv("API_SECRET")
    or os.getenv("KRAKEN_API_SECRET")
    or ""
)

BASE_ORDER_EUR   = float(os.getenv("BASE_ORDER_EUR",   "25"))
MIN_NOTIONAL_EUR = float(os.getenv("MIN_NOTIONAL_EUR", "15"))
SYMBOL_FALLBACK  = os.getenv("SYMBOL_FALLBACK", "BTC/EUR")

def _mask(s: str) -> str:
    s = str(s or "")
    return (s[:2] + "…" + s[-2:]) if len(s) >= 6 else ("set" if s else "missing")

# ───────────── CCXT Kraken ─────────────
kraken = ccxt.kraken({
    "apiKey": API_KEY,
    "secret": API_SECRET,
    "enableRateLimit": True,
    "timeout": 20000,
    "options": {"defaultType": "spot"},
})

def creds_ok() -> bool:
    try:
        kraken.check_required_credentials()  # lève si absent
        return True
    except Exception:
        return False

# ───────────── Helpers ─────────────
def normalize_symbol(sym: str) -> str:
    """Accepte XBT/EUR, BTCEUR, btc-eur… et renvoie BTC/EUR."""
    if not sym:
        return SYMBOL_FALLBACK
    s = sym.upper().replace("-", "/")
    s = s.replace("XBT", "BTC")
    if "/" not in s and len(s) >= 6:
        s = s[:-3] + "/" + s[-3:]
    return s

def amount_from_eur(symbol: str, eur: float):
    ticker = kraken.fetch_ticker(symbol)
    price  = float(ticker.get("last") or ticker.get("close") or ticker.get("bid"))
    mkt    = kraken.market(symbol)
    raw_amt = eur / price
    amt = float(kraken.amount_to_precision(symbol, raw_amt))
    return amt, price, mkt

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

# ───────────── Routes ─────────────
@app.get("/health")
def health():
    ok_creds = creds_ok()
    return jsonify({
        "ok": True,
        "exchange": "kraken",
        "creds_ok": ok_creds,
        "env": {
            "WEBHOOK_SECRET": "set" if SECRET else "missing",
            "API_KEY": _mask(API_KEY),
            "API_SECRET": _mask(API_SECRET),
        },
        "ts": int(time.time() * 1000),
    })

# Diag protégé : /diag?secret=XXXX
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
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.post("/webhook")
def webhook():
    # 1) JSON
    try:
        payload = request.get_json(force=True, silent=False) or {}
    except Exception:
        return jsonify({"ok": False, "error": "invalid-json"}), 400

    # 2) Secret
    if str(payload.get("secret", "")) != SECRET:
        app.logger.error("Bad secret")
        return jsonify({"ok": False, "error": "bad-secret"}), 401

    # 3) Crédentials
    if not creds_ok():
        return jsonify({
            "ok": False,
            "error": "missing-credentials",
            "hint": "Définis KRAKEN_KEY/API_KEY et KRAKEN_SECRET/API_SECRET dans Render."
        }), 500

    # 4) Normalisation symbol
    signal = str(payload.get("signal", "")).upper()
    symbol = normalize_symbol(payload.get("symbol") or SYMBOL_FALLBACK)

    # 5) Marchés
    try:
        kraken.load_markets()
    except Exception as e:
        return jsonify({"ok": False, "error": f"markets: {e}"}), 502

    # BUY
    if signal == "BUY":
        try:
            need_cost = max(min_cost(symbol), BASE_ORDER_EUR)
            amt, price, _ = amount_from_eur(symbol, need_cost)
            amt = max(amt, min_amount(symbol))

            bal = kraken.fetch_free_balance()
            eur_free = float(bal.get("EUR", 0) or 0)
            if eur_free + 1e-6 < need_cost:
                return jsonify({"ok": False, "skipped": "insufficient-eur",
                                "eur_free": eur_free, "need": need_cost}), 200

            order = kraken.create_order(symbol, "market", "buy", amt)
            return jsonify({"ok": True, "side": "buy", "symbol": symbol,
                            "amount": amt, "price": price, "order": order})
        except Exception as e:
            app.logger.exception("buy error")
            return jsonify({"ok": False, "error": str(e)}), 500

    # SELL
    if signal == "SELL":
        try:
            base_free = free_base(symbol)
            min_amt = min_amount(symbol)
            sell_amt = max(0.0, base_free - min_amt * 0.1)
            if sell_amt < min_amt:
                return jsonify({"ok": False, "skipped": "insufficient-base",
                                "base_free": base_free, "min": min_amt}), 200
            sell_amt = float(kraken.amount_to_precision(symbol, sell_amt))
            order = kraken.create_order(symbol, "market", "sell", sell_amt)
            return jsonify({"ok": True, "side": "sell", "symbol": symbol,
                            "amount": sell_amt, "order": order})
        except Exception as e:
            app.logger.exception("sell error")
            return jsonify({"ok": False, "error": str(e)}), 500

    return jsonify({"ok": False, "error": f"unknown-signal:{signal}"}), 400

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
