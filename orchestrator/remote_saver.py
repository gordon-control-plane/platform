import os
from typing import Any, AsyncIterator

import httpx
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_serializable_checkpoint_metadata,
)

class AsyncHttpSaver(BaseCheckpointSaver):
    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        user: str = "orchestrator@internal",
        headers: dict[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ):
        super().__init__()
        self.base_url = base_url.rstrip("/")
        self.headers: dict[str, str] = {
            "X-Requested-With": "XMLHttpRequest",
            "Tailscale-User-Login": os.environ.get("INTERNAL_SERVICE_USER", user),
        }
        if token:
            self.headers["Authorization"] = f"Bearer {token}"
        if headers:
            self.headers.update(headers)
        self.client = client if client is not None else httpx.AsyncClient(
            transport=transport, headers=self.headers
        )

    async def aclose(self):
        await self.client.aclose()
        
    async def aget_tuple(self, config: dict) -> CheckpointTuple | None:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = config["configurable"].get("checkpoint_id", "")
        
        resp = await self.client.get(
            f"{self.base_url}/api/checkpoints/{thread_id}",
            params={"checkpoint_ns": checkpoint_ns, "checkpoint_id": checkpoint_id}
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        return CheckpointTuple(
            config=data["config"],
            checkpoint=data["checkpoint"],
            metadata=data["metadata"],
            parent_config=data.get("parent_config")
        )
            
    async def aput(
        self,
        config: dict,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: dict,
    ) -> dict:
        thread_id = config["configurable"]["thread_id"]
        clean_configurable = {
            k: v
            for k, v in config.get("configurable", {}).items()
            if not k.startswith("__") and isinstance(v, (str, int, float, bool, list, dict))
        }
        clean_config = {"configurable": clean_configurable}
        clean_metadata = get_serializable_checkpoint_metadata(config, metadata)

        payload = {
            "config": clean_config,
            "checkpoint": checkpoint,
            "metadata": clean_metadata,
            "new_versions": new_versions,
        }
        resp = await self.client.post(
            f"{self.base_url}/api/checkpoints/{thread_id}",
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()["config"]
            
    async def aput_writes(self, config: dict, writes: list[tuple[str, Any]], task_id: str) -> None:
        thread_id = config["configurable"]["thread_id"]
        payload = {
            "config": config,
            "writes": writes,
            "task_id": task_id
        }
        resp = await self.client.post(
            f"{self.base_url}/api/checkpoints/{thread_id}/writes",
            json=payload
        )
        resp.raise_for_status()
    async def asearch(self, config: dict, **kwargs) -> AsyncIterator[CheckpointTuple]:
        async for item in self.alist(config, **kwargs):
            yield item