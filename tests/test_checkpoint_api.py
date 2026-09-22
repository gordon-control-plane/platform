import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock
from gateway.unified_api import app

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
    # aput returns the config
    mock_checkpointer.aput.return_value = {"configurable": {"thread_id": "test-thread"}}
    
    payload = {
        "config": {"configurable": {"thread_id": "test-thread"}},
        "checkpoint": {"id": "cp1"},
        "metadata": {"step": 1},
        "new_versions": {}
    }
    
    with patch("gateway.unified_api.checkpointer", mock_checkpointer):
        response = client.post("/api/checkpoints/test-thread", json=payload, headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["config"]["configurable"]["thread_id"] == "test-thread"

def test_save_checkpoint_writes(auth_headers):
    mock_checkpointer = AsyncMock()
    mock_checkpointer.aput_writes.return_value = None
    
    payload = {
        "config": {"configurable": {"thread_id": "test-thread"}},
        "writes": [["task1", "data"]],
        "task_id": "task-1"
    }
    
    with patch("gateway.unified_api.checkpointer", mock_checkpointer):
        response = client.post("/api/checkpoints/test-thread/writes", json=payload, headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["status"] == "ok"