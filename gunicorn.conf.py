accesslog = "-"
errorlog  = "-"
loglevel  = "debug"

# >>> important
worker_class = "gthread"   # active les threads
threads      = 2           # 2 threads par worker
timeout      = 120         # 120s avant kill d'un worker bloqué
