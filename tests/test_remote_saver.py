from unittest.mock import AsyncMock, patch

import httpx
import pytest

from orchestrator.remote_saver import AsyncHttpSaver


@pytest.fixture
def saver():
    return AsyncHttpSaver(base_url="http://test-unified-api:8000", token="test-token")


@pytest.mark.asyncio
async def test_aget_tuple_found(saver):
    config = {"configurable": {"thread_id": "thread-1"}}
    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    from unittest.mock import MagicMock

    mock_resp.json = MagicMock(
        return_value={
            "config": config,
            "checkpoint": {
                "v": 1,
                "ts": "2023-01-01T00:00:00Z",
                "id": "cp1",
                "channel_values": {},
            },
            "metadata": {"source": "test", "step": 1, "writes": {}, "parents": {}},
            "parent_config": None,
        }
    )

    with patch("httpx.AsyncClient.get", return_value=mock_resp):
        tup = await saver.aget_tuple(config)
        assert tup is not None
        assert tup.config == config
        assert tup.checkpoint["id"] == "cp1"


@pytest.mark.asyncio
async def test_aget_tuple_not_found(saver):
    config = {"configurable": {"thread_id": "thread-2"}}
    mock_resp = AsyncMock()
    mock_resp.status_code = 404

    with patch("httpx.AsyncClient.get", return_value=mock_resp):
        tup = await saver.aget_tuple(config)
        assert tup is None


@pytest.mark.asyncio
async def test_aput(saver):
    config = {"configurable": {"thread_id": "thread-1"}}
    checkpoint = {
        "v": 1,
        "ts": "2023-01-01T00:00:00Z",
        "id": "cp1",
        "channel_values": {},
    }
    metadata = {"source": "test", "step": 1, "writes": {}, "parents": {}}
    new_versions = {}

    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    from unittest.mock import MagicMock

    mock_resp.json = MagicMock(return_value={"config": config})

    with patch("httpx.AsyncClient.post", return_value=mock_resp):
        res = await saver.aput(config, checkpoint, metadata, new_versions)
        assert res == config


@pytest.mark.asyncio
async def test_aput_writes(saver):
    config = {"configurable": {"thread_id": "thread-1"}}
    writes = [("task1", "data")]
    task_id = "task-1"

    mock_resp = AsyncMock()
    mock_resp.status_code = 200

    with patch("httpx.AsyncClient.post", return_value=mock_resp):
        await saver.aput_writes(config, writes, task_id)
        # Assuming no exception means success


@pytest.mark.asyncio
async def test_aget_tuple_http_error(saver):
    config = {"configurable": {"thread_id": "thread-1"}}
    mock_resp = httpx.Response(
        500,
        request=httpx.Request(
            "GET", "http://test-unified-api:8000/api/checkpoints/thread-1"
        ),
    )
    with patch("httpx.AsyncClient.get", return_value=mock_resp):
        with pytest.raises(httpx.HTTPStatusError):
            await saver.aget_tuple(config)


@pytest.mark.asyncio
async def test_aget_tuple_network_error(saver):
    config = {"configurable": {"thread_id": "thread-1"}}
    with patch(
        "httpx.AsyncClient.get", side_effect=httpx.ConnectError("Connection refused")
    ), pytest.raises(httpx.RequestError):
        await saver.aget_tuple(config)


@pytest.mark.asyncio
async def test_aput_http_error(saver):
    config = {"configurable": {"thread_id": "thread-1"}}
    checkpoint = {
        "v": 1,
        "ts": "2023-01-01T00:00:00Z",
        "id": "cp1",
        "channel_values": {},
    }
    metadata = {"source": "test", "step": 1, "writes": {}, "parents": {}}
    new_versions = {}
    mock_resp = httpx.Response(
        500,
        request=httpx.Request(
            "POST", "http://test-unified-api:8000/api/checkpoints/thread-1"
        ),
    )
    with patch("httpx.AsyncClient.post", return_value=mock_resp):
        with pytest.raises(httpx.HTTPStatusError):
            await saver.aput(config, checkpoint, metadata, new_versions)


@pytest.mark.asyncio
async def test_aput_network_error(saver):
    config = {"configurable": {"thread_id": "thread-1"}}
    checkpoint = {
        "v": 1,
        "ts": "2023-01-01T00:00:00Z",
        "id": "cp1",
        "channel_values": {},
    }
    metadata = {"source": "test", "step": 1, "writes": {}, "parents": {}}
    new_versions = {}
    with patch(
        "httpx.AsyncClient.post", side_effect=httpx.ConnectError("Connection refused")
    ), pytest.raises(httpx.RequestError):
        await saver.aput(config, checkpoint, metadata, new_versions)


@pytest.mark.asyncio
async def test_aput_writes_http_error(saver):
    config = {"configurable": {"thread_id": "thread-1"}}
    writes = [("task1", "data")]
    task_id = "task-1"
    mock_resp = httpx.Response(
        500,
        request=httpx.Request(
            "POST", "http://test-unified-api:8000/api/checkpoints/thread-1/writes"
        ),
    )
    with patch("httpx.AsyncClient.post", return_value=mock_resp):
        with pytest.raises(httpx.HTTPStatusError):
            await saver.aput_writes(config, writes, task_id)


@pytest.mark.asyncio
async def test_aput_writes_network_error(saver):
    config = {"configurable": {"thread_id": "thread-1"}}
    writes = [("task1", "data")]
    task_id = "task-1"
    with patch(
        "httpx.AsyncClient.post", side_effect=httpx.ConnectError("Connection refused")
    ), pytest.raises(httpx.RequestError):
        await saver.aput_writes(config, writes, task_id)
