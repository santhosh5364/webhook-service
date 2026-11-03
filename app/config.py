import os

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL", "")
PROCESSING_DELAY_SECONDS = float(os.environ.get("PROCESSING_DELAY_SECONDS", "30"))
WORKER_CONCURRENCY = int(os.environ.get("WORKER_CONCURRENCY", "50"))
# A claimed job that isn't finished within this window is retried (covers crashes/redeploys).
LEASE_SECONDS = int(os.environ.get("LEASE_SECONDS", "60"))
POLL_INTERVAL_SECONDS = float(os.environ.get("POLL_INTERVAL_SECONDS", "5"))

if LEASE_SECONDS <= PROCESSING_DELAY_SECONDS:
    raise ValueError("LEASE_SECONDS must be longer than PROCESSING_DELAY_SECONDS")
DB_POOL_MIN = int(os.environ.get("DB_POOL_MIN", "2"))
DB_POOL_MAX = int(os.environ.get("DB_POOL_MAX", "10"))
WORKER_DB_POOL_MAX = int(os.environ.get("WORKER_DB_POOL_MAX", "5"))
