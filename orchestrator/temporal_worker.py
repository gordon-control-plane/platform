import asyncio
import os
from temporalio.common import RetryPolicy
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from orchestrator.remote_saver import AsyncHttpSaver
from temporalio.exceptions import ApplicationError
from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.worker import Worker

from orchestrator.workflows.ci_pipeline import build_ci_pipeline
from orchestrator.workflows.pm_standup import build_pm_standup


@dataclass
class JobInput:
    job_id: str
    workflow_type: str


# Global variables for graphs and checkpointer
checkpointer: AsyncHttpSaver | None = None
ci_graph: Any = None
pm_graph: Any = None


async def init_worker_state():
    global checkpointer, ci_graph, pm_graph
    unified_api_url = os.environ.get("UNIFIED_API_URL", "http://unified-api:8000")
    token = os.environ.get("INTERNAL_TOKEN") or os.environ.get("CHECKPOINT_AUTH_TOKEN")
    if not token and os.environ.get("ENV") == "production":
        raise RuntimeError("INTERNAL_TOKEN environment variable is required in production")
    checkpointer = AsyncHttpSaver(unified_api_url, token=token)

    ci_graph = build_ci_pipeline().compile(checkpointer=checkpointer)
    pm_graph = build_pm_standup().compile(checkpointer=checkpointer)


async def cleanup_worker_state():
    if checkpointer and hasattr(checkpointer, "aclose"):
        await checkpointer.aclose()


@activity.defn
async def run_langgraph_workflow(job_input: JobInput) -> str:
    """
    We only accept `job_id` and do not serialize auth contexts or full state in Temporal.
    """
    if job_input.workflow_type == "ci_pipeline":
        compiled_graph = ci_graph
    elif job_input.workflow_type == "pm_standup":
        compiled_graph = pm_graph
    else:
        raise ApplicationError(f"Unknown workflow type: {job_input.workflow_type}", type="ValueError", non_retryable=True)

    config = {"configurable": {"thread_id": job_input.job_id}}
    initial_state = {"job_id": job_input.job_id}

    # Execute workflow
    result = None
    async for event in compiled_graph.astream(
        initial_state, config=config, stream_mode="values"
    ):
        activity.heartbeat("running")
        result = event

    return result.get("status", "completed") if result else "completed"


@workflow.defn(sandboxed=False)
class AgentWorkflow:
    def __init__(self) -> None:
        self.status = "started"

    @workflow.run
    async def run(self, job_input: JobInput) -> str:
        # Schedule the activity
        result = await workflow.execute_activity(
            run_langgraph_workflow,
            job_input,
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=RetryPolicy(non_retryable_error_types=["ValueError"]),
        )
        if self.status in ["started", "processing"]:
            self.status = result
        return self.status

    @workflow.signal
    def update_status(self, new_status: str) -> None:
        if new_status in ["started", "processing", "paused", "waiting_on_human"]:
            self.status = new_status


async def main():
    await init_worker_state()
    try:
        temporal_url = os.environ.get("TEMPORAL_URL", "localhost:7233")
        client = await Client.connect(temporal_url)
        worker = Worker(
            client,
            task_queue="agent-task-queue",
            workflows=[AgentWorkflow],
            activities=[run_langgraph_workflow],
        )
        print("Starting worker...")
        await worker.run()
    finally:
        await cleanup_worker_state()


if __name__ == "__main__":
    asyncio.run(main())
