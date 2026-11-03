import os

import pytest

# Must be set before the app (and its config) is imported.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/webhooks_test"
)
os.environ["PROCESSING_DELAY_SECONDS"] = "1"
os.environ["WORKER_CONCURRENCY"] = "5"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c
