import os
import time
import sys
import requests

# Simple readiness check for backend
BACKEND_HEALTH_URL = os.environ.get("BACKEND_HEALTH_URL", "http://backend:8001/api/status")

print("[bootstrap] Waiting for backend...")
for i in range(60):
    try:
        r = requests.get(BACKEND_HEALTH_URL, timeout=3)
        if r.status_code == 200:
            print("[bootstrap] Backend ready")
            break
    except Exception:
        pass
    time.sleep(2)
else:
    print("[bootstrap] Backend not ready after timeout, proceeding anyway...")

print("[bootstrap] Starting one-off training (run_once)")
try:
    import ml_trainer
    result = ml_trainer.run_once()
    print("[bootstrap] Train result:", result)
    print("[bootstrap] Done")
    sys.exit(0)
except Exception as e:
    print(f"[bootstrap] ERROR: {e}")
    sys.exit(1)