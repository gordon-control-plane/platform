# HITL and Mutually Exclusive Orchestrator Actions Research Report

## Executive Summary
This report analyzes State-of-the-Art (SOTA) mechanisms for implementing Human-in-the-Loop (HITL) workflows and enforcing mutually exclusive actions in agentic orchestrators. Based on the `Gordon Control Plane` architecture—which utilizes Temporal and LangGraph—this research defines the boundary between actions that are structurally impossible (mutually exclusive due to upstream policy), actions that require human escalation (soft exclusivity), and actions that execute autonomously. The goal is to move beyond fragile prompt heuristics into deterministic, boundary-enforced access controls, strictly prioritizing **Developer UX & Speed** and **Architectural Simplicity**, while explicitly excluding third-party iPaaS and custom K8s controllers.

## Methodology
- **Sources consulted:** 40+ (Derived from 4 parallel deep-search sweeps across LangChain/LangGraph documentation, Temporal.io developer docs, GitHub/GitLab REST and GraphQL API specs, and CI/CD security vulnerability disclosures).
- **Date range:** 2023 - 2026 (Focusing on LangGraph v0.3 and current Temporal SDKs).
- **Key search queries used:** `LangGraph v0.3 HITL interrupt Command(resume=)`, `temporalio.contrib.langgraph`, `GitHub GraphQL API branchProtectionRules`, `GitLab push_access_levels REST API bypass`.
- **Hop depth achieved:** 5 hops (Topic → Foundational APIs (GraphQL/REST) → Orchestration primitive mapping (`interrupt` vs `Signal`) → Durable checkpointer analysis → CI/CD agent bypass vulnerability research).
- **Initial research (Phase 1.6):** 1 internal codebase investigation (via `scout` agent `CodebaseMapper`).
- **Clarify (Phase 1.75):** 1 round of questions asked (Criteria: UX & Speed, Arch Simplicity. Non-goals: Exclude Custom K8s Controllers, iPaaS, Alternative Runtimes).
- **Internal files investigated:** 8 (`gateway/unified_api.py`, `orchestrator/temporal_worker.py`, `scripts/local_worker.py`, workflows, `ROADMAP.md`).
- **Patterns identified:** 3 (Gateway workflow endpoints are stubs; Temporal workers run monolithic `run_langgraph_workflow` activities rather than isolated blocks; Tool boundaries are limited to a filesystem jail with no current HITL or exclusivity enforcement).

## Internal Investigation

### Current State
A `CodebaseMapper` agent investigated the `Gordon Control Plane`. The platform employs a disjointed hybrid architecture. The Gateway (`unified_api.py`) handles Tailscale identity and admin allowlists, but `/api/workflows` endpoints are hardcoded stubs. The Automation Plane (`temporal_worker.py`) executes `AgentWorkflow`, running entire LangGraph `StateGraphs` (like `ci_pipeline.py`) inside a single, monolithic Temporal activity (`run_langgraph_workflow`) checkpointed via `AsyncPostgresSaver`.

### Strengths
- **Boundary Identity**: The Tailscale header-based identity in the gateway provides a strong foundation for mapping approval signals to specific users.
- **Persistence Foundation**: The `AsyncPostgresSaver` is already wired in, meaning LangGraph state is durable.

### Gaps
- **Monolithic Activities**: Running the entire LangGraph graph inside a *single* Temporal activity breaks Temporal's durability model for long waits. An activity cannot pause indefinitely for human input without timing out or holding worker resources hostage.
- **Zero HITL Implementation**: There are no `interrupt()` calls in the mock workflows, and no Temporal `Signal` or `wait_condition` logic to handle human approvals.
- **Tool Jail Only**: The `local_worker.py` restricts paths but has no semantic understanding of Mutually Exclusive Git operations (e.g., it cannot prevent a force-push if the user requests one).

### Internal-External Bridge

| Internal Pattern | External Best Practice | Alignment | Adaptation Needed |
|-----------------|----------------------|-----------|------------------|
| Monolithic LangGraph Activity | `temporalio.contrib.langgraph` (Native Plugin) | Diverges | Must migrate from `run_langgraph_workflow` to running individual LangGraph nodes as Temporal Activities using the experimental plugin. |
| No Execution Pauses | LangGraph v0.3 `interrupt()` + `Command(resume=)` | Diverges | Inject `interrupt()` hooks before high-risk deterministic blocks; map state to Temporal Queries for UI rendering. |
| Local Worker assumed for Git | Platform Deterministic Push Block | Diverges | `local_worker.py` runs in a network-isolated sandbox and cannot execute Git pushes. Policy checks and Git egress must be handled by a distinct, credentials-aware Temporal Activity (the "Deterministic Push Block") running on the platform, not in the sandbox. |
## Detailed Findings

### 1. Mutually Exclusive Actions (Structural & Policy Blocking)
- **Finding 1 (GitHub Independence)**: In GitHub's GraphQL API, `allowsForcePushes` and `requiresApprovingReviews` (`requiresPullRequest` in UI) are distinct, independent booleans on the `BranchProtectionRule` object. They are not mutually exclusive in the API schema. (Source: GitHub GraphQL API Docs)
- **Finding 2 (Semantic Exclusivity)**: While the API fields are independent, the *actions* they govern are mutually exclusive for the agent. If a branch has `requiresApprovingReviews: true`, a direct `git push` by the agent will fail.
- **Finding 3 (GitLab Granularity)**: GitLab's `push_access_levels` and `merge_access_levels` evaluate using the "most permissive rule applies" logic, but code owner approvals evaluate using the "most restrictive" logic. (Source: GitLab REST API Docs)
- **Finding 4 (Convention & Wild West Repos)**: Many repositories do not structurally enforce branch protections, relying instead on social convention (e.g., "Don't push to main"). If the upstream API returns "Allowed" for everything, an orchestrator relying solely on GraphQL checks will autonomously execute destructive actions.
- **Consensus**: The orchestrator must support **Platform-Level Shadow Policies**. The Pre-Flight check must evaluate the intersection of (Upstream API Rules ∩ Platform Project Policies). If a repo lacks structural protection, the Platform enforces the convention structurally before the action is dispatched.
- **Debate / Security Risk**: Should agents be granted `unprotect_access_levels` or bypass rights? Security auditors strongly advise against this. In 2023, a GitLab vulnerability allowed bypasses via ambiguous Git tags (`git ambiguous ref name`). Giving AI agents bypass capabilities expands the CI/CD blast radius unacceptably. (Source: Cyberpress / GitLab Security Disclosures).

### 2. Mutually Exclusive Actions (Non-Git Domains)
- **Finding 1 (Cloud/IaC Policies)**: Cloud environments use Service Control Policies (AWS SCPs) or Azure Policies. An agent attempting to provision a public S3 bucket (`PublicAccessBlock=false`) or an unencrypted RDS instance might be mutually exclusive with the organization's SCP.
- **Finding 2 (Kubernetes Admission)**: An agent deploying a Helm chart or raw manifests might violate OPA Gatekeeper/Kyverno policies (e.g., `runAsRoot: true` or missing required labels).
- **Finding 3 (Database Operations)**: An agent connecting to a production database role to execute a `DROP TABLE` or `ALTER SCHEMA` may be mutually exclusive with the read-only or append-only grants of that credential.
- **Finding 4 (Budget & Quota)**: An agent attempting to spawn expensive cloud resources or run high-token background loops will hit the platform's Budget caps (ROADMAP 1.4). Intent vs. Budget = Mutually Exclusive.
- **Consensus**: For non-Git systems, the Pre-Flight check requires a **Dry-Run / Plan mechanism**. Actions like `terraform plan` or `kubectl apply --dry-run=server` act as the policy check. If the dry-run is rejected by the target system's admission controller, the action is Tier 2 (Structurally Blocked).

### 2. Human-in-the-Loop (HITL) Escalation (Soft Exclusivity)
- **Finding 1 (LangGraph v0.3)**: LangGraph handles HITL natively using `interrupt()`, which raises a resumable exception and checkpoints the state. Humans inject decisions (Approve, Edit, Reject, Respond) using `Command(resume=...)`, returning data directly into the paused node. (Source: LangChain HITL Docs)
- **Finding 2 (Temporal Wait Conditions)**: Temporal handles durable waits using `workflow.wait_condition()` paired with Asynchronous `Signal` messages or Synchronous `Update` messages. `Update` allows real-time validation feedback to the human UI (e.g., "Approval Rejected: You are not a code owner"). (Source: Temporal Message Passing Concepts)
- **Finding 3 (The Bridge)**: The `temporalio.contrib.langgraph` plugin translates a LangGraph `interrupt(draft)` into a paused Temporal Workflow state. The draft is exposed via a Temporal Query, and the workflow waits for a Temporal Signal to resume the LangGraph execution. (Source: Temporal.io / LangGraph Integrations)
- **Consensus**: High-blast-radius actions that are *technically allowed* by VCS policy (e.g., `--force-with-lease` on a feature branch, or merging a PR) must be escalated to a human. This is "Soft Exclusivity."

### 3. TOCTOU (Time-of-Check to Time-of-Use) and State Drift
- **Finding 1 (Durable Waits & Drift)**: Temporal can wait indefinitely for a signal. During this wait (e.g., 48 hours for human PR approval), the upstream `main` branch will advance.
- **Finding 2 (Rebase Mechanics)**: A naive resumption of a stale diff will fail or overwrite history.
- **Consensus**: The resumption of a paused workflow must trigger a "Rebase-and-Resolve" state loop. If the rebase applies cleanly, the push proceeds. If it conflicts, the execution must drop back to the LLM agent (equipped with conflict markers) to resolve it, and critically, **re-trigger the HITL approval** for the newly LLM-authored resolution to maintain the cryptographic chain of custody (Invariant I6).

## Multi-Perspective Analysis

| Stakeholder | What They Care About | Perspective on Exclusivity & HITL |
|---|---|---|
| **Maintainers / Power Users** | Developer UX, Speed, Automation | Hate alert fatigue. Want standard PRs to merge automatically if CI passes. HITL should only trigger for destructive actions (force pushes) or cross-repo synchronized deployments. |
| **Security Auditors** | Principle of Least Privilege, CI/CD Blast Radius | AI agents must never bypass Branch Protection (`allowsForcePushes=false`). Mutually exclusive actions must fail-closed. |
| **Platform Architects** | Architectural Simplicity | Avoid deploying separate Postgres queues or Redis instances just for human approvals. Rely entirely on Temporal's `wait_condition` and LangGraph's checkpointers. |

## Comparison Table: HITL Orchestration Mechanisms

| Criteria | LangGraph Native `interrupt()` | Temporal Signals & `wait_condition` | Combined (`temporalio.contrib.langgraph`) |
|----------|----------|----------|----------|
| **Durability (Worker Restarts)** | ⭐⭐⭐ (Relies entirely on external DB logic) | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| **Developer UX & Speed** | ⭐⭐⭐⭐⭐ (Native Python API) | ⭐⭐⭐ (Requires verbose Signal/Update handlers) | ⭐⭐⭐⭐ (Balances UX with Durability) |
| **Architectural Simplicity** | ⭐⭐⭐⭐ (Requires Checkpointer setup) | ⭐⭐⭐ (Requires custom state machines) | ⭐⭐⭐⭐⭐ (Removes custom state bridging) |
| **Sync vs Async Human Feedback** | Async only (`Command`) | Sync (`Update`) & Async (`Signal`) | Async (`Signal` mapping) |
| **Production Readiness** | Stable (v0.3) | Stable | **Experimental** (API subject to change) |
| **Last Commit** | 2026-09 (0mo gap) | 2026-09 (0mo gap) | 2026-09 (0mo gap) |
| **Last Release** | 2026-09 (0mo gap) | 2026-09 (0mo gap) | 2026-09-15 (v1.33.0, 0mo gap) |
| **License** | MIT | MIT | MIT |
| **Open CVEs** | 0 critical | 0 critical | 0 critical (0 open repo issues for plugin) |
| **Bus Factor** | High (LangChain HQ) | High (Temporal HQ) | High (Maintained in core Temporal Python SDK) |
| **Release Integrity** | CI-published | CI-published | CI-published |
| **AI Agent Compat** | Unbounded | Bounded | Bounded |
- **Risk**: The plugin is explicitly marked as experimental. Using it violates the "Stability" principle, but it perfectly satisfies the heavily weighted "Architectural Simplicity" criteria by eliminating the need to write custom code bridging LangGraph checkpointers to Temporal signals.
- **Mitigation**: Abstract the `interrupt()` and `resume()` wrappers so that if the Temporal plugin API changes, the core agent blocks remain unaffected.

### Common Pitfalls
- **The "Bypass" Trap**: Attempting to configure the agent's GitHub Service Account to bypass branch protections for speed. This violates security mandates.
- **Alert Fatigue**: Triggering a Temporal Signal for every minor file edit.

## Recommendations

### Primary Recommendation: Three-Tier Action Matrix
Implement a combined Temporal + LangGraph architecture, enforcing a strict 3-tier exclusivity matrix evaluated *before* the platform's Deterministic Git Push Block is invoked.

1. **Pre-Flight Policy Check**:
2. **Tier 0: Autonomous (Execute Immediately)**
   - Creating local branches, running tests.
   - *Fork vs Same-Repo*: Pushing to a fork (`origin`) is Tier 0.
   - *Non-Git*: Executing read-only queries (e.g., `SELECT`, `kubectl get`), local ephemeral builds.
3. **Tier 1: Soft Exclusivity / HITL Required (Interrupt & Signal)**
   - *Triggers*: `git push --force-with-lease` on non-main branches, marking PRs as "Ready for Review", cross-repo coordinated PR creation.
   - *Fork vs Same-Repo*: Opening a PR from a fork to upstream is Tier 1 (requires review of the merge intent). Direct pushes to non-protected same-repo branches (if allowed by policy) are Tier 1 (High blast radius).
   - *Non-Git Triggers*: Applying IaC changes (`terraform apply`), deploying to a staging/production Kubernetes namespace, executing `UPDATE`/`INSERT` queries on a live database, issuing emails/messages to external users.
   - *Mechanism*: LangGraph calls `interrupt(intent)`. The Temporal plugin translates this to a paused workflow. The Gateway's Tailscale-identified user sends a cryptographically signed Temporal `Signal` (Approve/Reject) via the UI.
   - *TOCTOU Loop*: Upon `Signal` receipt, the platform attempts a rebase/refresh. Clean rebase -> execute. Conflict -> route to LLM `ConflictResolver` -> require *new* HITL approval.
4. **Tier 2: Hard Exclusivity / Structurally Blocked (Fail Fast)**
   - *Triggers*: `git push --force` on `main`, pushing to a branch where `allowsForcePushes == false` (or blocked by Platform Shadow Policy), direct pushing where `requiresApprovingReviews == true`.
   - *Non-Git Triggers*: Exceeding ROADMAP 1.4 budget caps, OPA/Kyverno admission webhook denials (`dry-run` failures), AWS SCP rejections.
   - *Mechanism*: The pre-flight policy check evaluates to `BLOCKED`. The orchestrator returns an immediate `ToolError` to the LLM: "Action mutually exclusive with branch protection or system policy. Please re-plan."

### Alternative Recommendations
- **If `temporalio.contrib.langgraph` is too unstable:** Revert to the monolithic `run_langgraph_workflow` activity, but use LangGraph's `AsyncPostgresSaver`. The API Gateway must handle the `Command(resume=...)` injection directly to the Postgres checkpointer, bypassing Temporal entirely for the HITL step. (This reduces Architectural Simplicity but increases stability).

### Not Recommended
- **Git CLI Error Parsing**: Do not execute a command and parse `hook declined` or `protected branch hook declined`. This is fragile and breaks across different Git server implementations (GitHub vs GitLab vs Bitbucket).

## Next Steps
1. Refactor `orchestrator/temporal_worker.py` to test the `temporalio.contrib.langgraph` experimental plugin with a mock `interrupt()`.
2. Implement a `vcs_policy_check` Temporal Activity that queries GitHub GraphQL for `BranchProtectionRule`.
3. Integrate `temporalio.contrib.workflow_streams` (as mandated by ROADMAP 1.1a) to stream token deltas from the activity-isolated LangGraph nodes to the FastAPI gateway, ensuring UI responsiveness during durable waits.
4. Update the UI Dashboard to query Temporal for pending HITL drafts and emit approval Signals.

## Sources
1. [LangChain Blog: Building Human-in-the-Loop Agents with Interrupt](https://www.langchain.com/blog/making-it-easier-to-build-human-in-the-loop-agents-with-interrupt)
2. [LangGraph v0.3 Documentation: Interrupts and Commands](https://docs.langchain.com/oss/python/langgraph/interrupts)
3. [LangGraph Reference: Command(resume=) Object](https://reference.langchain.com/python/langgraph/types/Command)
4. [Temporal.io Blog: Temporal LangGraph Plugin Durable Execution](https://temporal.io/blog/temporal-langgraph-plugin-durable-execution)
5. [Temporal Python SDK: `temporalio.contrib.langgraph`](https://python.temporal.io/temporalio.contrib.langgraph.html)
6. [Temporal Docs: Human-in-the-loop Agent Orchestration (wait_condition)](https://docs.temporal.io/ai/cookbook/human-in-the-loop-python)
7. [Temporal Docs: Workflow Message Passing (Signals vs Updates)](https://docs.temporal.io/encyclopedia/workflow-message-passing)
8. [GitHub Docs: GraphQL API Reference - `BranchProtectionRule`](https://docs.github.com/en/graphql/reference/branches)
9. [GitHub Community: Branch Protection and Mutual Exclusivity](https://github.com/orgs/community/discussions/24640)
10. [GitLab Docs: REST API Protected Branches (`push_access_levels`)](https://docs.gitlab.com/api/protected_branches/)
11. [GitLab Docs: Code Owner Approval Vulnerability via Ambiguous Refs](https://gitlab.com/gitlab-org/gitlab/-/issues/410123)
12. [Cyberpress: GitLab Email Feature Bypass CI/CD Vulnerability](https://cyberpress.org/gitlab-email-feature-lets-attackers-push-code-to-protected-branches-and-execute-ci-cd-jobs/)
*(Sources 13-46 encompass API endpoint documentation, CI/CD security advisories from StepSecurity and Qovery, and developer discussions across StackOverflow and Reddit regarding LangGraph persistence and Temporal idempotency keys).*
