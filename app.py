# app.py
import os
import json
import logging
import time
import base64
import hmac
import hashlib
import requests
from urllib.parse import urlencode, quote_plus
from flask import Flask, request, jsonify

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# ==== ENV ============================================================
API_KEY     = os.getenv('KRAKEN_API_KEY', '')
API_SECRET  = os.getenv('KRAKEN_API_SECRET', '')
BASE        = os.getenv('BASE_SYMBOL', 'BTC').upper()
QUOTE       = os.getenv('QUOTE_SYMBOL', 'EUR').upper()
PAPER       = os.getenv('PAPER_MODE', '1') == '1'     # 1 = papier (pas d’ordres réels)
RISK_EUR    = float(os.getenv('RISK_EUR_PER_TRADE', '5'))
VALIDATE    = os.getenv('KRAKEN_VALIDATE', '1') == '1'  # 1 = dry-run côté Kraken

# ---- mapping simple TV -> Kraken
MAP = {
    "BTC": "XBT", "XBT": "XBT",
    "ETH": "ETH", "LTC": "LTC",
    "EUR": "EUR", "USD": "USD"
}

def normalize_symbol(symbol_tv: str) -> tuple[str, str, str]:
    """
    Accepte 'BTCEUR', 'BTC/EUR', 'XBTEUR', 'XBT:EUR' etc.
    Retourne (base, quote, pair_pub='BASE/QUOTE') en notation Kraken publique.
    """
    s = (symbol_tv or "").upper().replace(":", "/").replace("-", "/").strip()
    if not s:
        # fallback : BASE/QUOTE depuis ENV
        base, quote = MAP.get(BASE, BASE), MAP.get(QUOTE, QUOTE)
        return base, quote, f"{base}/{quote}"

    if "/" in s:
        b, q = [p.strip() for p in s.split("/", 1)]
    else:
        # coupe: 3 premières lettres en base (BTC/XBT/ETH/LTC), reste = quote
        if len(s) < 6:
            raise RuntimeError("invalid symbol")
        b, q = s[:3], s[3:]

    base = MAP.get(b, b)
    quote = MAP.get(q, q)
    return base, quote, f"{base}/{quote}"


# ==== ROUTES PING ====================================================
@app.route('/')
def root_ok():
    return {"status": "ok"}, 200

@app.get('/health')
def health():
    return {"status": "ok"}, 200


# ==== WEBHOOK TRADINGVIEW ===========================================
@app.post('/webhook')
def webhook():
    data = request.get_json(force=True, silent=False)
    app.logger.info(f"Webhook payload: {json.dumps(data, ensure_ascii=False)}")

    signal    = (data.get("signal") or "").upper().strip()
    symbol_tv = (data.get("symbol") or "").strip()
    timeframe = (data.get("timeframe") or data.get("time frame") or "").strip()

    if signal not in {"BUY", "SELL"}:
        return jsonify({"error": "invalid signal"}), 400

    try:
        # 1) normaliser le symbole pour Kraken
        base, quote, pair_pub = normalize_symbol(symbol_tv)  # ex: XBT/EUR

        # 2) prix via API publique (pair encodée car contient '/')
        price = fetch_price(pair_pub)

        # 3) taille de position : montant EUR / prix
        qty = calc_qty(price)

        if PAPER:
            app.logger.info(f"PAPER {signal} {pair_pub} qty={qty:.8f} price={price} tf={timeframe}")
            return jsonify({
                "paper": True,
                "signal": signal,
                "pair": pair_pub,
                "qty": qty,
                "price": price
            }), 200
        else:
            # 4) envoi ordre réel (validate=True => dry-run côté Kraken)
            res = place_order(signal, pair_pub, qty, validate=VALIDATE)
            app.logger.info(f"REAL {signal} {pair_pub} qty={qty:.8f} validate={VALIDATE} RESULT={res}")
            return jsonify({
                "paper": False,
                "validate": VALIDATE,
                "result": res
            }), 200
