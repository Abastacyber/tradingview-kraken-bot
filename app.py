# --- LOG CLEANUP (format court) ---------------------------------------------
import logging, time
from flask import request, g

# Pings/robots qu'on veut garder en "court"
PING_UA_HINTS = ("UptimeRobot", "Google-Apps-Script", "beanserver")

# Rendre werkzeug (server http) plus discret
logging.getLogger("werkzeug").setLevel(logging.WARNING)

@app.before_request
def _start_timer_and_tag():
    g._t0 = time.time()
    ua = request.headers.get("User-Agent", "")
    g._is_ping = (request.path == "/health") or any(h in ua for h in PING_UA_HINTS)
    g._ua = ua

@app.after_request
def _short_access_log(resp):
    dt_ms = int((time.time() - getattr(g, "_t0", time.time())) * 1000)
    method = request.method
    path = request.path
    status = resp.status_code
    ua = getattr(g, "_ua", "")
    # Une seule ligne compacte :
    if g._is_ping:
        # visible même en LOG_LEVEL=INFO pour vérifier les pings
        app.logger.info("PING %s %s %s ua=%s in=%dms", method, path, status, ua, dt_ms)
    else:
        app.logger.info("REQ  %s %s %s in=%dms", method, path, status, dt_ms)
    return resp

# Quand Kraken/TradingView coupe la connexion (reset 104), log en WARNING (pas de gros traceback)
import requests
from requests.exceptions import ConnectionError as RequestsConnectionError

@app.errorhandler(RequestsConnectionError)
def _handle_conn_reset(e):
    msg = str(e)
    if "104" in msg and "Connection reset by peer" in msg:
        app.logger.warning("réseau: connection reset by peer (104) – retry/backoff")
        return ("", 500)
    # autres erreurs réseau : laisser Flask gérer (trace utile)
    raise e
# ---------------------------------------------------------------------------
