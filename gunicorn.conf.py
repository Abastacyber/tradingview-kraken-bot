# gunicorn.conf.py
workers = 1
threads = 2
timeout = 60
accesslog = "-"
errorlog = "-"
loglevel = "info"
