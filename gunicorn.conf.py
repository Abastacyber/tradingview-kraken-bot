# gunicorn.conf.py
# =================
# Logs concis + utiles pour Render

# On garde les access/error logs sur stdout/stderr (Render les capte)
accesslog = "-"
errorlog  = "-"
loglevel  = "info"

# Format court : [date] statut PATH durée_ms UA
#   %(t)s  -> date
#   %(s)s  -> HTTP status
#   %(U)s  -> chemin (sans query)
#   %(M)s  -> durée en ms
#   %(a)s  -> user-agent
access_log_format = "%(t)s | %(s)s | %(U)s | %(M)sms | UA:%(a)s”
