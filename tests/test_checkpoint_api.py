import os
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from gateway.unified_api import app
from orchestrator.remote_saver import AsyncHttpSaver

client = TestClient(app)


@pytest.fixture
def auth_headers():
    return {
        "X-Requested-With": "XMLHttpRequest",
        "Tailscale-User-Login": "dev@example.com",
    }


def test_get_checkpoint_db_not_configured(auth_headers):
    with patch("gateway.unified_api.checkpointer", None):
        response = client.get("/api/checkpoints/test-thread", headers=auth_headers)
        assert response.status_code == 500
        assert response.json()["detail"] == "Database not configured"


def test_get_checkpoint_not_found(auth_headers):
    mock_checkpointer = AsyncMock()
    mock_checkpointer.aget_tuple.return_value = None

    with patch("gateway.unified_api.checkpointer", mock_checkpointer):
        response = client.get("/api/checkpoints/test-thread", headers=auth_headers)
        assert response.status_code == 404
        assert response.json()["detail"] == "Checkpoint not found"


def test_get_checkpoint_success(auth_headers):
    class MockTuple:
        config = {"configurable": {"thread_id": "test-thread"}}
        checkpoint = {"id": "cp1"}
        metadata = {"step": 1}
        parent_config = None

    mock_checkpointer = AsyncMock()
    mock_checkpointer.aget_tuple.return_value = MockTuple()

    with patch("gateway.unified_api.checkpointer", mock_checkpointer):
        response = client.get("/api/checkpoints/test-thread", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["config"]["configurable"]["thread_id"] == "test-thread"
        assert data["checkpoint"]["id"] == "cp1"


def test_save_checkpoint(auth_headers):
    mock_checkpointer = AsyncMock()
    mock_checkpointer.aput.return_value = {"configurable": {"thread_id": "test-thread"}}

    payload = {
        "config": {"configurable": {"thread_id": "test-thread"}},
        "checkpoint": {"id": "cp1"},
        "metadata": {"step": 1},
        "new_versions": {},
    }

    with patch("gateway.unified_api.checkpointer", mock_checkpointer):
        response = client.post(
            "/api/checkpoints/test-thread", json=payload, headers=auth_headers
        )
        assert response.status_code == 200
        assert response.json()["config"]["configurable"]["thread_id"] == "test-thread"


def test_save_checkpoint_writes(auth_headers):
    mock_checkpointer = AsyncMock()
    mock_checkpointer.aput_writes.return_value = None

    payload = {
        "config": {"configurable": {"thread_id": "test-thread"}},
        "writes": [["task1", "data"]],
        "task_id": "task-1",
    }

    with patch("gateway.unified_api.checkpointer", mock_checkpointer):
        response = client.post(
            "/api/checkpoints/test-thread/writes", json=payload, headers=auth_headers
        )
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


def test_checkpoint_api_rejects_missing_csrf_header():
    """Mutating checkpoint requests without X-Requested-With must fail with 403 Forbidden."""
    response = client.post(
        "/api/checkpoints/test-thread",
        json={"config": {}, "checkpoint": {}, "metadata": {}, "new_versions": {}},
        headers={"Tailscale-User-Login": "dev@example.com"},
    )
    assert response.status_code == 403
    assert "CSRF verification failed" in response.json()["detail"]


def test_checkpoint_api_rejects_missing_auth_header():
    """Checkpoint requests in non-dev environment without Tailscale-User-Login must fail with 401."""
    with patch.dict(os.environ, {"ENV": "production"}, clear=False):
        response = client.post(
            "/api/checkpoints/test-thread",
            json={"config": {}, "checkpoint": {}, "metadata": {}, "new_versions": {}},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert response.status_code == 401
        assert "Missing Tailscale-User-Login header" in response.json()["detail"]


@pytest.mark.asyncio
async def test_async_http_saver_contract_get_checkpoint():
    """Direct contract integration test: AsyncHttpSaver retrieves checkpoint through unified_api.app."""
    mock_checkpointer = AsyncMock()

    class MockTuple:
        config = {"configurable": {"thread_id": "test-thread"}}
        checkpoint = {"id": "cp1"}
        metadata = {"step": 1}
        parent_config = None

    mock_checkpointer.aget_tuple.return_value = MockTuple()

    transport = httpx.ASGITransport(app=app)
    saver = AsyncHttpSaver("http://test", transport=transport, user="dev@example.com")

    with patch("gateway.unified_api.checkpointer", mock_checkpointer):
        tup = await saver.aget_tuple({"configurable": {"thread_id": "test-thread"}})
        assert tup is not None
        assert tup.checkpoint["id"] == "cp1"
        assert tup.config["configurable"]["thread_id"] == "test-thread"


@pytest.mark.asyncio
async def test_async_http_saver_contract_save_checkpoint():
    """Direct contract integration test: AsyncHttpSaver saves checkpoint through unified_api.app."""
    mock_checkpointer = AsyncMock()
    mock_checkpointer.aput.return_value = {"configurable": {"thread_id": "test-thread"}}

    transport = httpx.ASGITransport(app=app)
    saver = AsyncHttpSaver("http://test", transport=transport, user="dev@example.com")

    with patch("gateway.unified_api.checkpointer", mock_checkpointer):
        res = await saver.aput(
            {"configurable": {"thread_id": "test-thread"}},
            {"id": "cp1"},
            {"step": 1},
            {},
        )
        assert res == {"configurable": {"thread_id": "test-thread"}}


@pytest.mark.asyncio
async def test_async_http_saver_contract_save_checkpoint_writes():
    """Direct contract integration test: AsyncHttpSaver saves writes through unified_api.app."""
    mock_checkpointer = AsyncMock()
    mock_checkpointer.aput_writes.return_value = None

    transport = httpx.ASGITransport(app=app)
    saver = AsyncHttpSaver("http://test", transport=transport, user="dev@example.com")

    with patch("gateway.unified_api.checkpointer", mock_checkpointer):
        await saver.aput_writes(
            {"configurable": {"thread_id": "test-thread"}},
            [("task1", "data")],
            "task-1",
        )
        mock_checkpointer.aput_writes.assert_awaited_once()
