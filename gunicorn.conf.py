bind = "0.0.0.0:5000"
workers = 2
worker_class = "gthread"
threads = 16
timeout = 60
keepalive = 5
max_requests = 2000
max_requests_jitter = 200
preload_app = False          # ✅ IMPORTANT

accesslog = "-"
errorlog = "-"
loglevel = "info"

worker_tmp_dir = "/dev/shm"
