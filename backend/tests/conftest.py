"""Shared pytest fixtures. pytest finds this file by name and offers its fixtures to every test."""

import pytest
from fastapi.testclient import TestClient

from mind_dump.main import app


@pytest.fixture
def client() -> TestClient:
    """Calls the app in-process: no server, no port, no network."""
    return TestClient(app)
