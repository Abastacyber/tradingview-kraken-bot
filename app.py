# app.py
import os
import logging
from datetime import datetime, timezone
from flask import Flask, request, jsonify

# ========= Logging propre =========
logger = logging.getLogger("tv-kraken")
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(h)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
logger.propagate = False  # évite les doublons dans Render

app = Flask(__name__)

# ========= Helpers =========
def iso_now():
    return datetime.now(timezone.utc).isoformat()

def log_tag_for_request():
    """
    Ajoute un petit tag lisible pour regrouper les entrées de log.
    On ne log rien si on n'a pas de tag pertinent.
    """
    ua = (request.headers.get("User-Agent") or "").lower()
    path = request.path
    method = request.method

    tag = None
    if "uptimerobot" in ua:
        tag = "PING Uptime"
    elif "google-apps-script" in ua or "script.google.com" in ua or "beanserver" in ua:
        tag = "PING Google Script"
    elif path == "/health":
        tag = "HEALTH"
    elif path == "/webhook" and method == "POST":
        tag = "ALERTE TradingView"

    if tag:
        logger.info(tag)

# ========= Routes =========
@app.get("/health")
def health():
    log_tag_for_request()
    return jsonify({"status": "ok", "time": iso_now()}), 200

@app.post("/webhook")
def webhook():
    try:
        log_tag_for_request()

        data = request.get_json(force=True, silent=True) or {}
        signal = str(data.get("signal", "")).upper()
        symbol = str(data.get("symbol", ""))
        timeframe = str(data.get("timeframe", ""))
        price = data.get("price")

        # Log compact et lisible
        logger.info(f"ALERT {signal} | {symbol} {timeframe} | price={price}")

        # Ici tu brancheras ta logique d'ordres Kraken (cooldown, sizing, API…)
        # Exemple de log qu'on pourrait émettre lorsqu'on exécutera vraiment :
        # logger.info(f"ORDER {signal} {symbol} 50€ @ {price} (mode=fixed_eur)")

        return jsonify({"ok": True}), 200

    except Exception as e:
        logger.error(f"ERROR webhook: {type(e).__name__}: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# ========= Main local (Render utilise gunicorn) =========
if __name__ == "__main__":
    # Pour tests en local uniquement
    app.run(host="0.0.0.0", port=5000, debug=True)
