# gunicorn.conf.py

# Logs sur stdout/stderr (Render les capte)
accesslog = "-"
errorlog  = "-"
loglevel  = "info"   # ou "debug" si tu veux plus de verbosité

# Un seul worker + threads, stable pour le plan Free
worker_class = "gthread"
threads = 1

# Timeouts raisonnables pour webhooks lents
timeout = 120
graceful_timeout = 30
keepalive = 5

# Très important : ne pas pré-charger l’app
preload_app = False

# Pour bien récupérer les prints/logs d'import
capture_output = True
