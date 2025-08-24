# app.py
import os
import time
import logging
from datetime import datetime, timezone
from flask import Flask, request, jsonify, g

# ===========
# Logging
# ===========
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logger = logging.getLogger("tv-kraken")
logger.setLevel(LOG_LEVEL)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(message)s"))
logger.handlers = [handler]
logger.propagate = False  # pas de double logs

app = Flask(__name__)

# ===========
# Helpers
# ===========
COOLDOWN_SEC = int(os.getenv("COOLDOWN_SEC", "600"))
_last_fire_ts = 0.0  # timestamp du dernier trade

def now_utc_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def log_alert_brief(payload: dict):
    """Affiche l'alerte TradingView en format court"""
    sig = payload.get("signal")
    sym = payload.get("symbol")
    tf  = payload.get("timeframe")
    px  = payload.get("price")
    logger.info(f"ALERT {sig} | {sym} {tf} | price={px}")

# ===========
# Hooks pour logs courts des requêtes
# ===========
@app.before_request
def _start_timer():
    g._t0 = time.perf_counter()

@app.after_request
def _log_response(resp):
    try:
        dt = (time.perf_counter() - g._t0) * 1000.0
    except Exception:
        dt = 0.0
    path = request.path
    qs = request.query_string.decode() or ""
    if qs:
        path = f"{path}?{qs}"

    line = (
        f"[{now_utc_iso()}] "
        f"{request.method} {path} {resp.status_code} "
        f"bytes={resp.calculate_content_length() or 0} "
        f"in={dt:.0f}ms"
    )
    if request.path == "/health":
        logger.info(f"PING {line}")
    else:
        logger.info(f"REQ  {line}")
    return resp

# ===========
# Routes
# ===========
@app.get("/health")
def health():
    return "ok", 200

@app.post("/webhook")
def webhook():
    global _last_fire_ts
    payload = request.get_json(silent=True) or {}

    # log compact de l'alerte
    log_alert_brief(payload)

    # anti-spam : cooldown
    now = time.time()
    if now - _last_fire_ts < COOLDOWN_SEC:
        left = int(COOLDOWN_SEC - (now - _last_fire_ts))
        logger.info(f"Cooldown actif -> alerte ignorée ({left}s restants)")
        return jsonify(ok=True, skipped="cooldown"), 200

    # ===> ICI tu appelles ta logique d'ordre réelle
    #      (Kraken, sizing, auto_topup, etc.)
    #      Laisse ce stub si tu ne veux pas modifier le reste maintenant.
    try:
        result = handle_order_stub(payload)
        _last_fire_ts = now
        return jsonify(ok=True, result=result), 200
    except Exception as e:
        logger.error(f"Order ERROR: {e}")
        return jsonify(ok=False, error=str(e)), 500

def handle_order_stub(payload: dict):
    """
    Stub temporaire qui simule une exécution.
    Remplace par ta fonction réelle (open_order(...) par ex.).
    """
    return {
        "received": {
            "signal": payload.get("signal"),
            "symbol": payload.get("symbol"),
            "timeframe": payload.get("timeframe"),
            "price": payload.get("price"),
        },
        "status": "simulated"
    }

# ===========
# Run local (pour tests)
# ===========
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
