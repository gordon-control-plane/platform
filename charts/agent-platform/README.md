# Agent Platform Helm Chart

This Helm chart (`charts/agent-platform`) defines the base infrastructure for the `gordon-control-plane`.

## Components Deployed
- **PostgreSQL:** Backing store for Temporal state, LiteLLM routing rules, LangGraph checkpoints, and MCP registry schemas.
- **Temporal Cluster:** The core engine for the Automation Plane.
- **LiteLLM Proxy:** The Gateway pods intercepting and routing LLM calls.
- **OpenShell Worker Nodes:** The isolated sandbox environment where agent tools and execution occur.

## Local Bring-Up
*(TBD: Full step-by-step for local deployment)*

Generally, deployment requires a local Kubernetes cluster (e.g., Docker Desktop, Minikube, kind):
```bash
helm upgrade --install agent-platform ./charts/agent-platform --namespace gordon --create-namespace
```

## Security Configurations
- The OpenShell worker nodes are configured with minimal capabilities.
- Network policies are defined to restrict the sandbox from accessing cluster-internal control plane services or cloud IMDS, except for explicitly allowlisted MCP endpoints.
