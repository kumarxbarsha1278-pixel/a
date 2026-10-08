# gunicorn.conf.py
import multiprocessing

bind = "0.0.0.0:5000"

# ✅ Safe config — no cache/rate-limit mismatch
workers = 2
worker_class = "gthread"
threads = 16          # 2 workers × 16 threads = 32 concurrent
timeout = 60
keepalive = 5
max_requests = 2000
max_requests_jitter = 200
preload_app = True

accesslog = "-"
errorlog = "-"
loglevel = "info"

# Worker temp dir
worker_tmp_dir = "/dev/shm"
