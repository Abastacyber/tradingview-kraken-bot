# gunicorn.conf.py

# on garde l'access log (utile sur Render) + errors sur stdout
accesslog = '-'
errorlog = '-'
loglevel = 'info'  # cohérent avec LOG_LEVEL=INFO

# Format court : [date] VERBE PATH?query STATUT BYTES DUREE
access_log_format = "%(t)s %(m)s %(U)s%(q)s %(s)s %(B)s %(L)s"
# ex: [24/Aug/2025:10:30:04 +0000] POST /webhook 200 123 0.087
