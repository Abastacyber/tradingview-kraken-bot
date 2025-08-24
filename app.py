# app.py
import os
import time
import json
import math
import logging
from threading import Thread

from flask import Flask, request
import requests
import krakenex

# ──────────────────────────────────────────────────────────────────────────────
# Config & clients
# ──────────────────────────────────────────────────────────────────────────────

app = Flask(__name__)
logging.basicConfig(level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper()))
log = app.logger

KRAKEN_API_KEY    = os.getenv("KRAKEN_API_KEY", "")
KRAKEN_API_SECRET = os.getenv("KRAKEN_API_SECRET", "")

# paires & mode
BASE   = os.getenv("BASE", "BTC").upper()          # ex: BTC
QUOTE  = os.getenv("QUOTE", "EUR").upper()         # ex: EUR
PAIR   = f"X{BASE}Z{QUOTE}" if QUOTE in ("EUR","USD") else f"{BASE}{QUOTE}"  # Kraken style
ORDER_TYPE = os.getenv("ORDER_TYPE", "market")     # "market" recommandé

# sizing & sécurité
SIZE_MODE          = os.getenv("SIZE_MODE", "fixed_eur")   # fixed_eur / percent_balance
FIXED_EUR_PER_TRADE = float(os.getenv("FIXED_EUR_PER_TRADE", "50"))
MIN_EUR_PER_TRADE   = float(os.getenv("MIN_EUR_PER_TRADE", "10"))
RISK_PCT            = float(os.getenv("RISK_PCT", "0.0005"))  # si percent_balance
AUTO_TOPUP          = os.getenv("AUTO_TOPUP", "true").lower() == "true"
TOPUP_EUR_LIMIT     = float(os.getenv("TOPUP_EUR_LIMIT", "30"))
FEE_BUFFER_PCT      = float(os.getenv("FEE_BUFFER_PCT", "0.002"))  # 0.2% pour frais/slippage

# “poussières” à laisser pour éviter erreurs “insufficient funds”
DUST_RESERVE_BASE   = float(os.getenv("DUST_RESERVE_BASE", "0.000001"))
DUST_RESERVE_QUOTE  = float(os.getenv("DUST_RESERVE_QUOTE", "1.0"))
BTC_RESERVE         = float(os.getenv("BTC_RESERVE", "0.00005"))  # min base à conserver

# tolérance / contrôle flux
ALERT_PRICE_TOL = float(os.getenv("ALERT_PRICE_TOL", "0.5"))  # 0.5% tolérance entre prix alerte et marché
COOLDOWN_SEC    = int(os.getenv("COOLDOWN_SEC", "600"))       # 10 minutes
MAX_OPEN_POS    = int(os.getenv("MAX_OPEN_POS", "1"))

# retries réseau
HTTP_RETRIES   = int(os.getenv("HTTP_RETRIES", "4"))
HTTP_BACKOFF_S = float(os.getenv("HTTP_BACKOFF_S", "1.0"))

# SL/TP “fallback” (si TradingView n’envoie rien)
FALLBACK_SL_PCT = float(os.getenv("FALLBACK_SL_PCT", "1.0"))  # -1.0%
FALLBACK_TP_PCT = float(os.getenv("FALLBACK_TP_PCT", "0.6"))  # +0.6%

# trailing config (si tu l’actives plus tard)
TRAIL_START_PCT = float(os.getenv("TRAIL_START_PCT", "0.6"))
TRAIL_STEP_PCT  = float(os.getenv("TRAIL_STEP_PCT", "0.3"))

# état local (cooldown)
_last_trade_ts = {"BUY": 0.0, "SELL": 0.0}

# client Kraken
api = krakenex.API(key=KRAKEN_API_KEY, secret=KRAKEN_API_SECRET)

# ──────────────────────────────────────────────────────────────────────────────
# Utils Kraken
# ──────────────────────────────────────────────────────────────────────────────

def query_public_ticker(pair: str) -> float:
    """Prix moyen actuel (mid)."""
    for attempt in range(HTTP_RETRIES):
        try:
            resp = api.query_public("Ticker", {"pair": pair})
            ask = float(resp["result"][list(resp["result"].keys())[0]]["a"][0])
            bid = float(resp["result"][list(resp["result"].keys())[0]]["b"][0])
            return (ask + bid) / 2.0
        except Exception as e:
            wait = HTTP_BACKOFF_S * (2 ** attempt)
            log.warning(f"ticker retry {attempt+1}/{HTTP_RETRIES} after error: {e} (wait {wait:.1f}s)")
            time.sleep(wait)
    raise RuntimeError("Failed to fetch ticker after retries")

def get_balances():
    """Retourne (base_qty, quote_qty)."""
    for attempt in range(HTTP_RETRIES):
        try:
            resp = api.query_private("Balance")
            res = resp.get("result", {})
            base_sym  = f"X{BASE}" if BASE in ("BTC", "XBT", "ETH") else BASE
            quote_sym = f"Z{QUOTE}" if QUOTE in ("EUR","USD") else QUOTE
            base_qty  = float(res.get(base_sym, "0"))
            quote_qty = float(res.get(quote_sym, "0"))
            return base_qty, quote_qty
        except Exception as e:
            wait = HTTP_BACKOFF_S * (2 ** attempt)
            log.warning(f"balance retry {attempt+1}/{HTTP_RETRIES} after error: {e} (wait {wait:.1f}s)")
            time.sleep(wait)
    raise RuntimeError("Failed to fetch balances after retries")

def place_order_with_retry(pair, side, volume, ordertype="market", price=None):
    """POST d’ordre robuste avec retries & backoff."""
    data = {
        "pair": pair,
        "type": side.lower(),   # "buy" / "sell"
        "ordertype": ordertype
    }
    if ordertype == "limit" and price is not None:
        data["price"] = str(price)
    data["volume"] = f"{volume:.8f}"

    for attempt in range(HTTP_RETRIES):
        try:
            resp = api.query_private("AddOrder", data)
            if resp.get("error"):
                raise RuntimeError(resp["error"])
            return resp
        except Exception as e:
            if attempt == HTTP_RETRIES - 1:
                log.error(f"giving up place_order: {e}")
                raise
            wait = HTTP_BACKOFF_S * (2 ** attempt)
            log.warning(f"place_order retry {attempt+1}/{HTTP_RETRIES} after error: {e} (wait {wait:.1f}s)")
            time.sleep(wait)

# ──────────────────────────────────────────────────────────────────────────────
# Sizing & top-up
# ──────────────────────────────────────────────────────────────────────────────

def compute_trade_sizes(side: str, alert_price: float):
    """Calcule quote à investir et base à trader, avec buffer & réserves."""
    base_bal, quote_bal = get_balances()

    # quantité € à engager
    if SIZE_MODE == "fixed_eur":
        eur_to_use = FIXED_EUR_PER_TRADE
    else:  # percent_balance
        eur_to_use = quote_bal * RISK_PCT

    eur_to_use = max(eur_to_use, MIN_EUR_PER_TRADE)

    # buffer frais/slippage
    eur_to_use *= (1.0 - FEE_BUFFER_PCT)

    # respect réserves
    if side == "BUY":
        eur_to_use = min(eur_to_use, max(0.0, quote_bal - DUST_RESERVE_QUOTE))
        if eur_to_use < MIN_EUR_PER_TRADE:
            if AUTO_TOPUP and quote_bal < TOPUP_EUR_LIMIT:
                log.info(f"Auto-topup actif: quote balance {quote_bal:.2f} < {TOPUP_EUR_LIMIT}, "
                         f"aucun BUY jusqu’à recharge (ou conversion manuelle).")
            else:
                log.info("BUY skipped: pas assez d’EUR disponibles.")
            return 0.0, 0.0
        base_amount = eur_to_use / alert_price
        return eur_to_use, base_amount

    else:  # SELL
        # ne pas toucher à la réserve
        base_sellable = max(0.0, base_bal - max(BTC_RESERVE, DUST_RESERVE_BASE))
        if base_sellable <= 0:
            log.info("SELL skipped: BTC insuffisants (après réserve).")
            return 0.0, 0.0
        # vendre au plus la contre-valeur de eur_to_use
        target_base = eur_to_use / alert_price
        base_amount = min(base_sellable, target_base)
        eur_equiv   = base_amount * alert_price
        if base_amount <= 0 or eur_equiv < MIN_EUR_PER_TRADE:
            log.info("SELL skipped: montant calculé < minimum.")
            return 0.0, 0.0
        return eur_equiv, base_amount

# ──────────────────────────────────────────────────────────────────────────────
# Web endpoints
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return "ok", 200

@app.post("/webhook")
def webhook():
    """Répond 200 immédiatement, traite l’alerte en arrière-plan."""
    try:
        data = request.get_json(force=True, silent=True) or {}
    except Exception:
        data = {}
    Thread(target=process_alert, args=(data,)).start()
    return "ok", 200

def process_alert(data: dict):
    """
    data attendu (TradingView):
      {"signal":"BUY"|"SELL","symbol":"BTCEUR","timeframe":"15","price":"97800.0"}
    """
    try:
        raw = json.dumps(data, ensure_ascii=False)
        log.info(f"INFO:tv-kraken:Raw alert: {raw}")

        side  = str(data.get("signal", "")).upper()
        price = float(data.get("price", "0"))
        if side not in ("BUY", "SELL"):
            log.info("Alerte ignorée: signal invalide.")
            return

        # cooldown
        now = time.time()
        if now - _last_trade_ts[side] < COOLDOWN_SEC:
            log.info("INFO:tv-kraken:Cooldown actif -> alerte ignorée")
            return

        # prix marché & tolérance
        mkt = query_public_ticker(PAIR)
        tol = ALERT_PRICE_TOL / 100.0  # % → fraction
        if not ( (1 - tol) * mkt <= price <= (1 + tol) * mkt ):
            log.info(f"Alert price={price:.2f} vs spot={mkt:.2f} (dev={100*(price/mkt-1):.2f}%) -> OUT of tolerance")
            # on peut continuer quand même, on prend mkt
        else:
            log.info(f"INFO:tv-kraken:Alert price={price:.2f} vs spot={mkt:.2f} (dev={100*(price/mkt-1):.2f}%)")

        use_price = mkt  # on trade au marché

        # sizing
        eur_amount, base_amount = compute_trade_sizes(side, use_price)
        if base_amount <= 0:
            return

        # passage d’ordre (robuste)
        log.info(f"==> ORDER {side} {base_amount:.8f} {BASE}{QUOTE} ~{eur_amount:.2f} {QUOTE} @ ~{use_price:.1f}")
        resp = place_order_with_retry(pair=PAIR, side=side, volume=base_amount, ordertype=ORDER_TYPE)
        log.info(f"Kraken response: {resp}")

        _last_trade_ts[side] = time.time()

    except requests.exceptions.ConnectionError as e:
        log.error(f"requests ConnectionError: {e}")
    except Exception as e:
        log.error(f"Webhook exception: {e}", exc_info=True)

# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # utile en local, sur Render tu tournes avec gunicorn
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
