from unittest.mock import MagicMock, patch

import pytest
from temporalio.client import WorkflowFailureError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

import orchestrator.temporal_worker as worker_module
from orchestrator.temporal_worker import (
    AgentWorkflow,
    JobInput,
    cleanup_worker_state,
    run_langgraph_workflow,
)



@pytest.mark.asyncio
async def test_agent_workflow():
    mock_get = MagicMock()
    mock_get.status_code = 404
    mock_get.raise_for_status = MagicMock()
    
    mock_post = MagicMock()
    mock_post.status_code = 200
    mock_post.raise_for_status = MagicMock()
    mock_post.json = MagicMock(return_value={"config": {}})
    
    async def mock_get_coro(*args, **kwargs):
        return mock_get
        
    async def mock_post_coro(*args, **kwargs):
        return mock_post
            
    with patch("httpx.AsyncClient.get", side_effect=mock_get_coro), \
         patch("httpx.AsyncClient.post", side_effect=mock_post_coro):
        async with await WorkflowEnvironment.start_time_skipping() as env:
            await worker_module.init_worker_state()
            try:
                # Start worker
                worker = Worker(
                    env.client,
                    task_queue="test-task-queue",
                    workflows=[AgentWorkflow],
                    activities=[run_langgraph_workflow],
                )

                async with worker:
                    # Run ci_pipeline workflow
                    job_input = JobInput(
                        job_id="test-job-1", workflow_type="ci_pipeline"
                    )
                    result = await env.client.execute_workflow(
                        AgentWorkflow.run,
                        job_input,
                        id="test-workflow-1",
                        task_queue="test-task-queue",
                    )
                    assert result == "quality_analyzed"

                    job_input2 = JobInput(
                        job_id="test-job-2", workflow_type="pm_standup"
                    )

                    result2 = await env.client.execute_workflow(
                        AgentWorkflow.run,
                        job_input2,
                        id="test-workflow-2",
                        task_queue="test-task-queue",
                    )
                    assert result2 == "blockers_identified"

                    # Test ValueError for invalid workflow type
                    job_input3 = JobInput(
                        job_id="test-job-3", workflow_type="invalid_type"
                    )
                    with pytest.raises(WorkflowFailureError) as exc_info:
                        await env.client.execute_workflow(
                            AgentWorkflow.run,
                            job_input3,
                            id="test-workflow-3",
                            task_queue="test-task-queue",
                        )
                    assert "Unknown workflow type" in str(exc_info.value.cause.cause)
            finally:
                await cleanup_worker_state()
