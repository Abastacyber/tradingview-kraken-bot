# app.py — Flask + Kraken webhook (sizing corrigé)
import os
import time
import hmac
import base64
import hashlib
import logging
from decimal import Decimal, ROUND_DOWN
from urllib.parse import urlencode

import requests
from flask import Flask, request, jsonify

# -----------------------------------------------------------------------------
# Config & helpers
# -----------------------------------------------------------------------------
app = Flask(__name__)
app.logger.setLevel(logging.INFO)

def env_float(name, default):
    raw = os.getenv(name, "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw.replace(",", "."))
    except Exception:
        return float(default)

def env_str(name, default):
    raw = os.getenv(name, "").strip()
    return raw if raw else default

# === ENV requis / optionnels ===
KRAKEN_API_KEY    = env_str("KRAKEN_API_KEY", "")
KRAKEN_API_SECRET = env_str("KRAKEN_API_SECRET", "")
WEBHOOK_SECRET    = env_str("WEBHOOK_SECRET", "")  # facultatif

ORDER_TYPE        = env_str("ORDER_TYPE", "market").lower()     # market | limit
QUOTE             = env_str("QUOTE", "EUR").upper()

SIZE_MODE         = env_str("SIZE_MODE", "fixed_eur").lower()   # fixed_eur | risk_pct
RISK_PCT          = env_float("RISK_PCT", 0.0005)               # si risk_pct
FEE_BUFFER_PCT    = env_float("FEE_BUFFER_PCT", 0.20)

# support des trois alias : FIXED_EUR_PER_TRADE, FIXED_EUR, FIXED_PER_TRADE
FIXED_EUR_PER_TRADE = env_float(
    "FIXED_EUR_PER_TRADE",
    env_float("FIXED_EUR", env_float("FIXED_PER_TRADE", 20.0))
)

# (optionnel) oflags=post pour faire du maker (frais réduits mais pas d'exécution instantanée)
POST_ONLY = env_str("POST_ONLY", "false").lower() in ("1", "true", "yes", "on")

# -----------------------------------------------------------------------------
# Kraken HTTP
# -----------------------------------------------------------------------------
KRAKEN_BASE = "https://api.kraken.com"

def _kraken_sign(uri_path, data, secret):
    postdata = urlencode(data)
    encoded = (str(data['nonce']) + postdata).encode()
    message = uri_path.encode() + hashlib.sha256(encoded).digest()
    mac = hmac.new(base64.b64decode(secret), message, hashlib.sha512)
    sigdigest = base64.b64encode(mac.digest())
    return sigdigest.decode()

def kraken_private(path, payload):
    assert KRAKEN_API_KEY and KRAKEN_API_SECRET, "API keys manquantes"
    url = f"{KRAKEN_BASE}{path}"
    payload["nonce"] = int(1000 * time.time())
    headers = {
        "API-Key": KRAKEN_API_KEY,
        "API-Sign": _kraken_sign(path, payload, KRAKEN_API_SECRET),
        "User-Agent": "tv-kraken-bot",
    }
    r = requests.post(url, data=payload, headers=headers, timeout=20)
    r.raise_for_status()
    return r.json()

def kraken_public(path, params=None):
    url = f"{KRAKEN_BASE}{path}"
    r = requests.get(url, params=params or {}, timeout=15)
    r.raise_for_status()
    return r.json()

# -----------------------------------------------------------------------------
# Utils
# -----------------------------------------------------------------------------
def truncate_qty(v, decimals=8):
    q = Decimal(v).quantize(Decimal(10) ** -decimals, rounding=ROUND_DOWN)
    return float(q)

def map_symbol_to_pair(symbol: str) -> str:
    """
    Ex: 'KRAKEN:BTCEUR' => 'XBTEUR'
    Garde les autres tels quels si déjà au bon format.
    """
    s = (symbol or "").upper().replace("KRAKEN:", "")
    if s == "BTCEUR":
        return "XBTEUR"
    return s  # fallback: laisse tel quel si déjà OK

def get_ticker_price(pair: str) -> float:
    data = kraken_public("/0/public/Ticker", {"pair": pair})
    # data['result'] = { 'XXBTZEUR': {'a':[ask,...],'b':[bid,...],...}}
    result = data.get("result", {})
    if not result:
        raise RuntimeError(f"Ticker vide pour {pair}")
    first = next(iter(result.values()))
    # on prend le prix 'a' (ask) ou 'c' (last)
    price = float(first.get("c", ["0"])[0] or first.get("a", ["0"])[0])
    if price <= 0:
        raise RuntimeError(f"Prix invalide pour {pair}: {price}")
    return price

def get_balances() -> dict:
    """Retourne les soldes (ex: {'EUR': 42.1, 'BTC': 0.00123})"""
    resp = kraken_private("/0/private/Balance", {})
    if resp.get("error"):
        raise RuntimeError(f"Erreur Balance: {resp['error']}")
    bal = {}
    for asset, amt in resp["result"].items():
        # Kraken retourne 'ZEUR' / 'XXBT' etc.
        if asset.upper() in ("ZEUR", "EUR"):
            bal["EUR"] = float(amt)
        if asset.upper() in ("XXBT", "XBT", "BTC"):
            bal["BTC"] = float(amt)
    return bal

def compute_order_qty(price: float, balances=None) -> float:
    """
    Calcule la quantité BTC selon SIZE_MODE. Logue explicitement le notional.
    """
    if price is None or price <= 0:
        raise ValueError("Prix invalide pour le sizing")

    if SIZE_MODE == "fixed_eur":
        eur = FIXED_EUR_PER_TRADE
        qty = truncate_qty(eur / price, 8)
        app.logger.info(
            f"SIZING | mode=fixed_eur, notional={eur} EUR, price={price} -> qty={qty}"
        )
        return qty

    if SIZE_MODE == "risk_pct":
        balances = balances or get_balances()
        eur_avail = float(balances.get(QUOTE, 0.0))
        notional = eur_avail * float(RISK_PCT)
        qty = truncate_qty(notional / price, 8)
        app.logger.info(
            f"SIZING | mode=risk_pct, notional={notional:.2f} {QUOTE}, price={price} -> qty={qty}"
        )
        return qty

    # fallback sécurisé
    eur = 20.0
    qty = truncate_qty(eur / price, 8)
    app.logger.warning(
        f"SIZING | mode inconnu '{SIZE_MODE}' -> fallback fixed_eur {eur} EUR -> qty={qty}"
    )
    return qty

# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------
@app.get("/")
def root():
    return "Bot Kraken is live", 200

@app.post("/webhook")
def webhook():
    # 1) Vérification secret (facultatif)
    if WEBHOOK_SECRET:
        recv = request.headers.get("X-Webhook-Secret", "")
        if recv != WEBHOOK_SECRET:
            app.logger.warning("Webhook rejeté: mauvais secret")
            return jsonify({"status": "forbidden"}), 403

    data = request.get_json(force=True, silent=True) or {}
    signal = (data.get("signal") or "").upper()        # "BUY" | "SELL"
    symbol = data.get("symbol") or "KRAKEN:BTCEUR"
    timeframe = str(data.get("timeframe", ""))
    price_in = float(str(data.get("price", "0")).replace(",", ".") or 0)

    if signal not in ("BUY", "SELL"):
        return jsonify({"status": "ignored", "msg": "signal manquant/invalid"}), 200

    pair = map_symbol_to_pair(symbol)
    side = "buy" if signal == "BUY" else "sell"

    # 2) Prix: utilise la payload sinon ticker
    try:
        price = price_in if price_in > 0 else get_ticker_price(pair)
    except Exception as e:
        app.logger.exception(f"Erreur prix: {e}")
        return jsonify({"status": "error", "msg": str(e)}), 500

    # 3) Sizing
    try:
        qty = compute_order_qty(price)
    except Exception as e:
        app.logger.exception(f"Erreur sizing: {e}")
        return jsonify({"status": "error", "msg": str(e)}), 500

    # 4) Construction d’ordre
    order = {
        "pair": pair,
        "type": side,
        "volume": f"{qty:.8f}",
        "ordertype": "market",
        # "validate": True,  # <- activer pour dry-run (débug sans exécuter)
    }

    if ORDER_TYPE == "limit":
        # Simple logique de limite: légère amélioration côté maker
        # BUY en dessous / SELL au dessus
        delta = max(price * 0.0002, 1.0)  # ~0.02% min 1€
        limit_price = price - delta if side == "buy" else price + delta
        order["ordertype"] = "limit"
        order["price"] = f"{limit_price:.1f}"
        if POST_ONLY:
            order["oflags"] = "post"  # maker only
        app.logger.info(
            f"ORDER {signal} {qty} {pair} @ limit {limit_price:.1f} | TF {timeframe}"
        )
    else:
        app.logger.info(
            f"ORDER {signal} {qty} {pair} @ market ~{price:.1f} | TF {timeframe}"
        )

    # 5) Envoi Kraken
    try:
        resp = kraken_private("/0/private/AddOrder", order)
        app.logger.info(f"Réponse Kraken: {resp}")
        status = "sent" if not resp.get("error") else "kraken_error"
        return jsonify({"status": status, "order": resp}), 200
    except requests.HTTPError as e:
        app.logger.exception(f"HTTPError Kraken: {e}")
        return jsonify({"status": "error", "msg": str(e)}), 502
    except Exception as e:
        app.logger.exception(f"Exception Kraken: {e}")
        return jsonify({"status": "error", "msg": str(e)}), 500

# -----------------------------------------------------------------------------
# Entrée gunicorn (Render lance: gunicorn app:app)
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")), debug=False)
