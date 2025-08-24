# app.py
import os
import time
import logging
from datetime import datetime, timezone
from flask import Flask, request, jsonify

# ========= Logging configuration =========
logger = logging.getLogger("tv-kraken")
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(h)

logger.setLevel(logging.getLevelName(os.getenv("LOG_LEVEL", "INFO").upper()))
logger.propagate = False  # pas de doublons Render

# ========= Flask app =========
app = Flask(__name__)

# ========= Healthcheck =========
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "time": datetime.now(timezone.utc).isoformat()}), 200

# ========= Tag requests pour lisibilité logs =========
@app.before_request
def _tag_incoming_request():
    try:
        ua = (request.headers.get("User-Agent") or "").lower()
        path = request.path
        tag = None

        if "uptimerobot" in ua:
            tag = "PING Uptime"
        elif "google-apps-script" in ua or "script.google.com" in ua or "beanserver" in ua:
            tag = "PING Google Script"
        elif path == "/health":
            tag = "HEALTH"
        elif path == "/webhook" and request.method == "POST":
            tag = "ALERTE TradingView"

        if tag:
            logger.info(tag)
    except Exception:
        pass

# ========= Webhook pour TradingView =========
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True, silent=False) or {}
        signal     = str(data.get("signal", "")).upper()
        symbol     = str(data.get("symbol", ""))
        timeframe  = str(data.get("timeframe", ""))
        price      = data.get("price")

        # Log clair
        logger.info(f"ALERT {signal} | {symbol} {timeframe} | price={price}")

        # Ici tu mets ta logique de trading Kraken :
        # - cooldown
        # - MAX_OPEN_POS
        # - sizing (fixed_eur ou auto_size)
        # - trailing SL/TP
        # - appel Kraken API (buy/sell)
        #
        # Exemple de log ordre
        # logger.info(f"ORDER {signal} {symbol} 50€ @~{price} (mode=fixed_eur)")
        #
        # Exemple succès Kraken
        # logger.info(f"KRAKEN OK | id=OABCDEF-... | filled=0.0009 fee=0.000002")

        return {"ok": True}, 200

    except Exception as e:
        logger.error(f"ERROR webhook: {type(e).__name__}: {e}")
        return {"ok": False, "error": str(e)}, 500


# ========= Main (local only, Render utilise gunicorn) =========
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
