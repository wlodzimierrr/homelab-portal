from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.logs_quickview import clear_rate_limit_state_for_tests
from app.main import app, clear_observability_caches_for_tests


@pytest.fixture(autouse=True)
def _clear_api_caches_between_tests() -> None:
    clear_rate_limit_state_for_tests()
    clear_observability_caches_for_tests()


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client
