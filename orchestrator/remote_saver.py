import json
import httpx
from typing import Optional, Any, AsyncIterator, Tuple, Dict
from langgraph.checkpoint.base import BaseCheckpointSaver, Checkpoint, CheckpointMetadata, CheckpointTuple

class AsyncHttpSaver(BaseCheckpointSaver):
    def __init__(self, base_url: str, token: str):
        super().__init__()
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"}
        self.client = httpx.AsyncClient(headers=self.headers)

    async def aclose(self):
        await self.client.aclose()
        
    async def aget_tuple(self, config: dict) -> Optional[CheckpointTuple]:
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
            
    async def aput(self, config: dict, checkpoint: Checkpoint, metadata: CheckpointMetadata, new_versions: dict) -> dict:
        thread_id = config["configurable"]["thread_id"]
        
        payload = {
            "config": config,
            "checkpoint": checkpoint,
            "metadata": metadata,
            "new_versions": new_versions
        }
        resp = await self.client.post(
            f"{self.base_url}/api/checkpoints/{thread_id}",
            json=payload
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
        # Minimal implementation for search
        yield None