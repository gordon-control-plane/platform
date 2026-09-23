# Architecture: One-Plane Agent Control Plane

The `gordon-control-plane` is designed around a **One-Plane** architecture where all components, including agent execution environments (Agent Substrate), run in the same Kubernetes cluster plane.

## 1. Execution Model

### The Automation Plane
Powered by **Temporal** and **LangGraph**, the Automation Plane is responsible for durable, long-running agent workflows. These include asynchronous tasks such as CI pipelines, background code reviews, and periodic project management standups.
- **Components:** Temporal Workers (`orchestrator`).
- **Characteristics:** Highly retriable, state-checkpointed, determinant workflow execution with LLM interactions isolated inside Temporal Activities.

### The Interactive Plane
Supports real-time, low-latency interaction between end-users and agents.
- **Components:** `interactive_plane` interfaces.
- **Characteristics:** Fast feedback loops, direct state mutation, and streaming UI integrations.

## 2. Core Components
### Gateway (Unified API + LiteLLM)
The central intelligence router for the platform.
- **LiteLLM Base:** Provides vendor-agnostic LLM routing. We use a **GitOps + Kubernetes Secrets** split model for configuration:
  - **Routing (GitOps):** `litellm_config.yaml` is tracked in version control. It defines fallbacks, rate limits, and model identifiers (e.g. mapping `claude-3` to Vertex AI).
  - **Authentication (Vault/Secrets):** API keys (`ANTHROPIC_API_KEY`, GCP Service Accounts for Vertex) are injected into the Gateway pods exclusively as environment variables (via Kubernetes Secrets or ExternalSecrets to Vault). `litellm_config.yaml` natively resolves these via `os.environ/` syntax, completely isolating auth from routing logic without requiring the LiteLLM UI.

### Agent Substrate Sandbox
Provides the secure, isolated execution environment where agent-generated code and tool actions run. It uses RWO hardlinked clones for fast initialization.
- Limits network access (e.g., blocking cluster-internal routing and cloud IMDS, allowing only explicitly permitted MCP/Gateway endpoints).
- Drops capabilities (e.g., `CAP_SYS_ADMIN`) and defaults to read-only root filesystems.
- Uses explicit isolation to prevent cross-sandbox tampering of hardlinked files.

The base deployment layer defined by the `charts/agent-platform` Helm chart manages:
- PostgreSQL (via Zalando Operator for Temporal state, LangGraph checkpoints).
- Temporal Cluster.
- Unified API and LiteLLM Gateway pods.
- Agent Substrate Worker nodes.

## 3. Security & Boundary Constraints

To operate agents safely at scale, the architecture enforces multiple layers of security:

1. **Sandbox Escapes:** Agent Substrate strictly limits path traversal and capabilities (read-only root, no CAP_SYS_ADMIN).
2. **Network Isolation:** Agent sandboxes cannot reach internal services (Temporal, Postgres) or cloud metadata endpoints.
3. **API Authentication:** Temporal gRPC/REST APIs use mTLS/JWT authentication.
4. **Cross-Tenant Data Isolation:** Hardlinked clones must be strictly isolated to prevent one compromised sandbox from tampering with shared files.

## 4. Key Global Risks & Mitigations

- **Temporal Workflow Non-Determinism:** LLM calls are inherently non-deterministic. They must be isolated into Temporal Activities.
- **State Synchronization:** Moving state securely and consistently requires careful checkpointing via LangGraph and Temporal.
