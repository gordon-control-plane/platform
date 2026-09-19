import os
import yaml
import httpx
from gateway.shunt_middleware import apply_shunt_middleware
from fastapi import FastAPI, Depends, HTTPException, Request, Header, BackgroundTasks
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, Any, Optional

app = FastAPI(title="Unified API BFF")

# CORS and CSRF Middleware
allowed_origins = os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ALLOWLIST_PATH = os.environ.get("ADMIN_ALLOWLIST_PATH", "/etc/unified-api/allowlist.yaml" if os.path.exists("/etc/unified-api/allowlist.yaml") else "admin-allowlist.yaml")
CONFIG_PATH = os.environ.get("LITELLM_CONFIG_PATH", "/app/gateway/litellm_config.yaml" if os.path.exists("/app/gateway/litellm_config.yaml") else "gateway/litellm_config.yaml")
LITELLM_URL = os.environ.get("LITELLM_URL", "http://litellm:4000")

@app.middleware("http")
async def verify_csrf_header(request: Request, call_next):
    # Require custom header for state-changing requests to mitigate CSRF
    if request.method in ["POST", "PUT", "PATCH", "DELETE"]:
        if request.headers.get("X-Requested-With") != "XMLHttpRequest":
            return JSONResponse(
                status_code=403,
                content={"detail": "CSRF verification failed: Missing X-Requested-With header"}
            )
    return await call_next(request)

def get_current_user(tailscale_user_login: Optional[str] = Header(None)) -> str:
    if not tailscale_user_login:
        if os.environ.get("ENV") == "dev":
            return "dev@example.com"
        raise HTTPException(status_code=401, detail="Missing Tailscale-User-Login header")
    return tailscale_user_login

def verify_admin(user: str = Depends(get_current_user)) -> str:
    try:
        with open(ALLOWLIST_PATH, "r") as f:
            allowlist = yaml.safe_load(f)
            # Handle Kubernetes ConfigMap wrapper if present
            if "data" in allowlist and "allowlist.yaml" in allowlist["data"]:
                allowlist = yaml.safe_load(allowlist["data"]["allowlist.yaml"])

            admins = allowlist.get("admins", [])
            if user not in admins:
                raise HTTPException(status_code=403, detail="Admin privileges required")
    except FileNotFoundError:
        # Default fallback or raise
        raise HTTPException(status_code=500, detail="Admin allowlist not found")
    return user

# Chat Proxy
@app.post("/v1/chat/completions")
async def chat_proxy(request: Request, user: str = Depends(get_current_user)):
    raw_body = await request.body()
    body = await apply_shunt_middleware(raw_body)
    headers = dict(request.headers)
    for h in ["host", "content-length", "x-requested-with"]:
        headers.pop(h, None)

    client = httpx.AsyncClient(timeout=30.0)
    req = client.build_request(
        method="POST",
        url=f"{LITELLM_URL}/v1/chat/completions",
        content=body,
        headers=headers
    )

    response = await client.send(req, stream=True)

    async def stream_generator():
        try:
            async for chunk in response.aiter_raw():
                yield chunk
        finally:
            if hasattr(response, 'aclose'): await response.aclose()
            if hasattr(client, 'aclose'): await client.aclose()
    return StreamingResponse(
        stream_generator(),
        status_code=response.status_code,
        headers={k: v for k, v in response.headers.items() if k.lower() not in ["content-encoding", "content-length", "transfer-encoding"]}
    )

# Workflows
class WorkflowRequest(BaseModel):
    name: str
    args: Dict[str, Any]

@app.post("/api/workflows")
async def create_workflow(req: WorkflowRequest, user: str = Depends(get_current_user)):
    # Submit Temporal workflow (Stub)
    return {"id": "wf_12345", "status": "started", "user": user}

@app.get("/api/workflows/{workflow_id}/state")
async def get_workflow_state(workflow_id: str, user: str = Depends(get_current_user)):
    # Retrieve state (Stub)
    return {"id": workflow_id, "state": "running"}

@app.post("/api/workflows/{workflow_id}/signal")
async def signal_workflow(workflow_id: str, payload: dict, user: str = Depends(get_current_user)):
    # Dispatch signal (Stub)
    return {"id": workflow_id, "signaled": True}

# Config
@app.get("/api/config")
async def get_config(user: str = Depends(verify_admin)):
    # Read litellm config and redact secrets
    try:
        with open(CONFIG_PATH, "r") as f:
            config = yaml.safe_load(f) or {}

            # Redact secrets
            if "litellm_settings" in config:
                for key in list(config["litellm_settings"].keys()):
                    if any(term in key.lower() for term in ["key", "secret", "token"]):
                        config["litellm_settings"][key] = "*****"

            if "model_list" in config:
                for model in config["model_list"]:
                    if "litellm_params" in model:
                        for key in list(model["litellm_params"].keys()):
                            if any(term in key.lower() for term in ["key", "secret", "token"]):
                                model["litellm_params"][key] = "*****"

            return config
    except FileNotFoundError:
        return {"model_list": []}

class ConfigUpdateRequest(BaseModel):
    config_yaml: str

@app.post("/api/config")
async def update_config(req: ConfigUpdateRequest, user: str = Depends(verify_admin)):
    # Submit PR to GitHub
    github_token = os.environ.get("GITHUB_TOKEN")
    if not github_token:
        raise HTTPException(status_code=500, detail="GITHUB_TOKEN not configured")

    repo = os.environ.get("GITHUB_REPO", "gordon-control-plane/platform")

    async with httpx.AsyncClient() as client:
        # Check for open config PRs
        headers = {
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/vnd.github.v3+json"
        }
        prs_url = f"https://api.github.com/repos/{repo}/pulls?state=open"
        resp = await client.get(prs_url, headers=headers)

        if resp.status_code == 200:
            prs = resp.json()
            config_prs = [pr for pr in prs if "config" in pr.get("title", "").lower()]
            if config_prs:
                raise HTTPException(status_code=409, detail="A configuration PR is already open")

        # Stub for creating PR (in a real scenario, this would commit to a branch and open a PR)
        return {"status": "pr_created", "url": f"https://github.com/{repo}/pulls/999"}

@app.get("/health")
async def health():
    return {"status": "ok"}
