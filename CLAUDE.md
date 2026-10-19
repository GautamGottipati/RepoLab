# RepoLab

RepoLab turns software repositories into **interactive, executable learning environments**.

Give RepoLab a repository URL and it autonomously investigates the codebase, determines how the software is intended to run, builds an isolated environment, detects the appropriate interaction surfaces, and creates a learning experience around the real application.

Depending on the repository, RepoLab may render:

- a Spark/data playground
- an interactive CLI
- a live web application
- an API playground
- a terminal
- a notebook
- multiple surfaces for hybrid repositories

RepoLab uses NVIDIA Nemotron models as reasoning agents. Agents can inspect code, search the repository, read documentation, build environments, run tests, diagnose failures, propose experiments, execute approved experiments, analyze evidence, and generate lessons.

## Core Principles

> **Don't just tell. Show.**

> **RepoLab doesn't generate an answer when it can generate an experiment.**

Answers should be grounded in repository code, actual execution, measured results, logs, and version-relevant official documentation.

Models may reason about measurements. **Models never manufacture measurements.**

Anything simulated must be clearly labeled as simulated.

---

# Hackathon Goal

Demonstrate that an AI agent can take an unfamiliar repository and autonomously turn it into an executable learning experience.

The primary demo is Apache Spark.

The ideal flow is:

```text
GitHub Repository
        ↓
Agent investigates repository
        ↓
Understands application
        ↓
Builds environment
        ↓
Runs smoke test
        ↓
Generates Spark playground
        ↓
User explores application
        ↓
User asks "Why does this use 200 partitions?"
        ↓
Agent investigates code
        ↓
Agent forms hypothesis
        ↓
Agent proposes experiment
        ↓
User approves
        ↓
Agent runs controlled variants
        ↓
RepoLab measures actual behavior
        ↓
Agent explains measured results
```

---

# Hackathon Scope

Build in this order.

**Do not start a later milestone until the previous milestone works end to end.**

## P0 — Spark Agentic Playground

Must ship.

Required flow:

```text
Repo URL
→ ingest
→ investigate
→ RepoManifest
→ build
→ smoke test
→ run Spark
→ render playground
→ ask "why?"
→ agent investigates
→ experiment proposed
→ user approves
→ variants run
→ measurements collected
→ evidence assembled
→ Nemotron explains results
```

## P1 — Cassandra CLI Playground

Should ship.

Required:

- detect Cassandra repository
- detect CLI interaction surface
- build or resolve runnable Cassandra
- start Cassandra
- verify with smoke test
- expose real `cqlsh`
- generate one guided lesson
- observe commands and results
- check lesson steps
- explain errors using evidence

## P2 — Build Fallback Pipeline

Implement enough of the fallback system to demonstrate graceful degradation.

Cassandra should support the complete pipeline.

## P3 — Hybrid Repository Detection

Stretch goal.

Use Airflow or a similar repository to demonstrate detection of multiple interaction surfaces.

Full Airflow playground is optional.

---

# Architecture

```text
web/
api/
worker/
repolab/
```

## `web/`

Next.js + TypeScript + Tailwind.

Use:

- xterm.js for terminals
- Monaco where code editing is required
- SSE for job/agent events
- WebSocket for interactive terminal sessions

Responsibilities:

- repository submission
- job progress
- architecture visualization
- learning experience
- experiment approval
- experiment visualization
- evidence display
- agent activity
- CLI terminal
- web preview
- data playground
- lesson UI

Screens should follow `docs/design/`.

**Do not build a complete IDE.**

## `api/`

Python 3.11 + FastAPI.

Responsibilities:

- create sessions
- start jobs
- expose repository manifests
- approve agent actions
- approve experiments
- stream events
- serve terminal WebSockets
- expose experiment results

Slow operations must never run inside request handlers.

## `worker/`

RQ + Redis background workers.

Responsibilities:

- repository ingestion
- repository investigation
- builds
- smoke tests
- sandbox operations
- experiment execution
- lesson generation
- research
- long-running agent tasks

---

# Shared Package

```text
repolab/
  agents/
  llm/
  ingest/
  classify/
  manifest/
  sandbox/
  builds/
  experiences/
  experiments/
  evidence/
  lessons/
  research/
  analytics/
  events/
```

---

# Agent Architecture

`repolab/agents/`

RepoLab uses an **agent orchestrator with specialized agents and deterministic tools**.

Do not implement agents as unrestricted autonomous loops.

Agents reason and choose actions.

Tools perform actions.

Deterministic application code validates state and enforces permissions.

```text
                    User
                      │
                      ▼
               Agent Orchestrator
                      │
       ┌──────────────┼───────────────┐
       │              │               │
       ▼              ▼               ▼
 Repository        Build           Learning
 Investigator      Agent            Agent
       │              │               │
       └──────────────┼───────────────┘
                      │
                      ▼
              Experiment Agent
                      │
                      ▼
                 Tool Layer
```

---

# Agent Orchestrator

The orchestrator manages the overall objective and agent state.

Maintain an explicit state machine:

```text
RECEIVED
   ↓
INVESTIGATING
   ↓
MANIFEST_READY
   ↓
BUILDING
   ↓
VERIFYING
   ↓
EXPERIENCE_READY
   ↓
LEARNING
   ↓
EXPERIMENT_PROPOSED
   ↓
AWAITING_APPROVAL
   ↓
EXPERIMENTING
   ↓
EXPLAINING
```

Agents must not silently skip states.

Persist agent state so sessions survive worker restarts.

---

# Repository Investigator Agent

Purpose:

> Understand what the repository contains and how it is intended to run.

Available tools:

```text
list_files
read_file
search_code
find_symbol
inspect_dependencies
inspect_config
inspect_git_metadata
detect_entrypoints
detect_frameworks
detect_services
inspect_tests
inspect_dockerfiles
search_official_docs
```

The agent should determine:

- project type
- frameworks
- entry points
- services
- build process
- execution process
- interaction surfaces
- important concepts
- external infrastructure requirements

Output:

`RepoManifest`

---

# RepoManifest

`RepoManifest` is the canonical description of a repository.

All downstream components consume it.

```python
class RepoManifest(BaseModel):
    repo_url: str
    commit_sha: str

    languages: list[str]
    frameworks: list[str]

    entrypoints: list[EntryPoint]
    services: list[Service]
    dependencies: list[Dependency]

    interaction_surfaces: list[InteractionSurface]

    build_strategy: BuildStrategy
    smoke_tests: list[SmokeTest]

    concepts: list[Concept]

    confidence: float
```

A repository may expose multiple interaction surfaces.

**Do not force the entire repository into one application type.**

Examples:

```text
React frontend
FastAPI backend
Python CLI
Spark analytics job
```

may result in:

```text
🌐 Web
🔌 API
>_ CLI
⚡ Data Pipeline
```

---

# Classification

`repolab/classify/`

Classification order:

1. deterministic rules
2. repository evidence
3. LLM reasoning only when signals conflict

Examples:

```text
package.json + Next dependency
→ likely web

pyproject + pyspark
→ likely Spark

setup.py + console_scripts
→ likely CLI
```

Super resolves ambiguous cases using repository evidence.

Classification produces one or more interaction surfaces and recommends a primary experience.

---

# Experience Adapters

`repolab/experiences/`

Initial adapters:

```text
SparkDataExperience
CLIExperience
WebExperience
TerminalExperience
```

Future:

```text
APIExperience
NotebookExperience
MLExperience
AirflowExperience
KafkaExperience
```

Frontend rendering should depend on the manifest rather than repository-specific code.

```text
interaction_surface.type = "cli"
→ CLIExperience

interaction_surface.type = "web"
→ WebExperience

interaction_surface.type = "data_playground"
→ SparkDataExperience
```

---

# Build Agent

The Build Agent determines how to create a runnable environment.

Tools:

```text
create_sandbox
run_command
install_dependencies
build_source
start_service
stop_service
run_smoke_test
inspect_build_logs
save_checkpoint
restore_checkpoint
```

The Build Agent may diagnose failures and attempt safe fixes.

Example:

```text
Build fails
    ↓
Agent reads log
    ↓
Finds incompatible Java version
    ↓
Checks repository config/docs
    ↓
Changes sandbox Java version
    ↓
Retries build
```

Every attempt must be recorded.

The agent must never modify the source repository itself.

---

# Build Resolution Pipeline

Cache lookup is separate from fidelity fallback.

```text
Repo + Commit
      │
      ▼
Exact cached artifact?
   │          │
  yes         no
   │          │
   ▼          ▼
 use       source build
 artifact       │
                X
                ▼
         official release
                │
                X
                ▼
          partial build
                │
                X
                ▼
            Mini Lab
```

Fidelity order:

| Version | Fidelity | Real Experiments |
|---|---|---|
| Exact source build | Exact | Yes |
| Official release | High | Yes, with version disclosure |
| Partial build | High for included components | Included components only |
| Mini Lab | Conceptual | Never |

Cached builds are cached instances of real versions, not a separate fidelity level.

---

# Build Rules

A build is `ready` only after its smoke test passes.

Examples:

```text
Spark
→ tiny Spark job completes

Cassandra
→ cqlsh connects and SELECT executes

Web
→ health endpoint returns expected response
```

Each build publishes:

```text
version
source
commit
fidelity
capabilities
limitations
smoke_test
```

If a build fails:

1. preserve raw logs
2. Nano summarizes the relevant log tail
3. Build Agent may diagnose and retry within limits
4. if still unsuccessful, fall through

Never hide build failures.

---

# NVIDIA Nemotron / Nebius Token Factory

Use the official OpenAI-compatible API.

Environment:

```text
NEBIUS_API_KEY
MODEL_NANO
MODEL_SUPER
MODEL_ULTRA
```

**Never hardcode model IDs.**

Check Token Factory for currently supported model IDs.

## Model Routing

### Nano

Use for:

- file summaries
- log parsing
- lesson-step checking
- short error explanations
- quick preliminary answers
- inexpensive classification tasks

### Super

Use for:

- repository architecture
- repository investigation
- classification tie-breaking
- lesson generation
- architecture explanations
- Mini Lab generation
- build diagnosis
- internal-behavior explanations

### Ultra

Use only for:

- experiment design
- difficult hypothesis generation
- experiment result interpretation

---

# Model Router

`repolab/llm/`

All model access passes through the router.

The router must:

- support reasoning-model responses
- handle empty `content`
- inspect reasoning/output fields where supported
- use Pydantic schemas
- request structured JSON
- validate output
- retry once on schema failure
- record latency
- record token usage
- estimate cost
- record purpose

Do not assume native model tool calling is supported.

Agent execution must also support structured action requests.

Example:

```json
{
  "action": "read_file",
  "arguments": {
    "path": "src/main.py"
  },
  "reason": "Need to locate SparkSession configuration."
}
```

Application code validates actions before execution.

---

# Agent Tool Layer

Agents never directly execute shell commands or infrastructure operations.

They request tools.

```python
class AgentTool(Protocol):
    name: str

    async def execute(
        self,
        arguments: dict
    ) -> ToolResult:
        ...
```

Tool categories:

```text
Repository tools
Research tools
Sandbox tools
Build tools
Execution tools
Experiment tools
Evidence tools
```

Every tool invocation emits an event.

---

# Approval Gates

Agentic does not mean unrestricted autonomy.

Require explicit approval for actions that:

- execute experiments
- significantly increase compute usage
- modify learner code
- intentionally introduce failures
- start expensive services
- perform potentially destructive actions

Read-only repository investigation does not require approval.

Example:

```text
RepoLab wants to run an experiment.

Question:
How does shuffle partition count affect runtime?

Runs:
4 configurations × 3 repeats

Estimated:
12 Spark jobs
~4 minutes

[Approve Experiment]
```

---

# Nebius Sandboxes

`repolab/sandbox/`

**Read the current official Nebius Token Factory Sandbox documentation before implementing the wrapper.**

Verify SDK methods against documentation.

Never guess API method names.

Pattern:

```text
Build
  ↓
Smoke Test
  ↓
Checkpoint
  ↓
        ┌─────────────┐
        │ checkpoint  │
        └──────┬──────┘
               │
     ┌─────────┼─────────┐
     ▼         ▼         ▼
   Run A     Run B      Run C
```

Each experiment variant starts from identical state.

Set explicit timeouts.

Run independent variants in parallel.

User code never executes on the API host.

---

# Long-Running Services

Some applications require persistent services or exposed ports.

Examples:

- Cassandra
- Airflow
- complex web applications

When Sandboxes cannot support the required lifecycle, run the service in Docker on a dedicated Nebius AI Cloud VM.

The execution abstraction should hide whether an environment is:

```text
SandboxEnvironment

or

VMEnvironment
```

---

# Experiment Agent

This is the core agentic feature.

When a user asks:

> Why does this Spark application use 200 shuffle partitions?

Do not immediately generate an explanation if the question can reasonably be tested.

The Experiment Agent:

```text
Question
   ↓
Investigate relevant code
   ↓
Gather documentation
   ↓
Form hypothesis
   ↓
Design controlled experiment
   ↓
Present plan
   ↓
WAIT FOR APPROVAL
   ↓
Execute variants
   ↓
Collect measurements
   ↓
Compute statistics
   ↓
Assemble evidence
   ↓
Explain observed result
```

Ultra generates an `ExperimentPlan`.

Example:

```json
{
  "question": "How does shuffle partition count affect this job?",
  "variable": "spark.sql.shuffle.partitions",
  "values": [10, 50, 200, 500],
  "fixed_settings": {
    "dataset": "sample-v1",
    "workers": 4
  },
  "metrics": [
    "runtime",
    "task_count",
    "shuffle_read",
    "shuffle_write"
  ],
  "repeats": 3
}
```

The user must approve before execution.

---

# Measurement Rules

Measurements must come from real execution.

Examples:

- Spark event logs
- process timing
- test output
- HTTP metrics
- CLI output
- application logs

Statistics are computed by deterministic Python code.

Models must never calculate authoritative metrics.

Compute where appropriate:

```text
median
min/max
spread
standard deviation
relative difference
```

The LLM receives the already-computed results.

---

# Experiment Reproducibility

Persist:

```text
repo
commit SHA
environment/checkpoint
application version
command
configuration
input dataset hash
resource configuration
repeat count
raw measurements
aggregated measurements
timestamps
```

Never persist secret values.

The UI should indicate whether an experiment is reproducible.

---

# Evidence System

`repolab/evidence/`

Every substantive explanation should be backed by an `EvidenceBundle`.

```python
class EvidenceBundle(BaseModel):
    code_references: list[CodeReference]
    measurements: list[Measurement]
    logs: list[LogReference]
    documentation: list[DocumentationReference]
    simulated: bool = False
```

Explanation input:

```text
Question
+
EvidenceBundle
      ↓
Nemotron
      ↓
Grounded Explanation
```

The model must not introduce unsupported factual claims.

The UI displays evidence next to explanations.

---

# Tavily Research

`repolab/research/`

Use Tavily for external documentation.

Restrict searches to official domains whenever possible.

Examples:

```text
spark.apache.org
cassandra.apache.org
airflow.apache.org
```

Use research for:

- version-specific behavior
- official semantics
- build troubleshooting
- experiment interpretation
- terminal error explanation
- lesson enrichment

Return URLs and relevant excerpts.

When external documentation is used, prefer version-matched official documentation and cite it.

Repository code remains primary evidence for repository-specific behavior.

---

# Learning Agent

`repolab/lessons/`

The Learning Agent creates guided exploration from the actual repository.

Prioritize interaction over reading.

Bad:

```text
Read this explanation of Cassandra.
```

Better:

```text
Run:

DESCRIBE KEYSPACES;

What do you observe?
```

Then:

```text
Create a table.
Insert data.
Query it.
Break the query.
Understand the error.
```

Lesson structure:

```text
concept
goal
context
action
expected observation
validation
hint
code references
documentation references
```

Use deterministic lesson validation whenever possible.

Nano may classify ambiguous results.

---

# Mini Lab

Mini Lab is the final fallback.

It is a model-generated conceptual simulation.

Rules:

- always labeled `SIMULATED`
- visually distinct
- link simulated behavior to real source code
- unsupported commands must state they require a real environment
- never invent output
- never produce performance measurements
- never present simulated results as experimental evidence

When a real environment becomes available:

1. replay supported learner actions
2. execute them against the real system
3. report differences

---

# Events and Progress

Every long-running action emits events.

```text
agent_started
agent_status
tool_requested
tool_started
tool_completed

step_started
log_line
progress
partial_result
step_done
failed
heartbeat

approval_required
approval_received

experiment_variant_started
experiment_variant_completed
```

**Never expose private model chain-of-thought.**

`agent_status` contains concise user-facing status only.

Examples:

```text
Inspecting Spark configuration

Finding application entry points

Comparing build strategies

Designing partition experiment
```

Persist events in Postgres.

Stream them through SSE.

Refreshing the browser must replay history.

---

# Fast + Deep Responses

RepoLab should feel responsive while deeper reasoning runs.

```text
User question
      │
      ├────→ Nano
      │       ↓
      │   Quick answer
      │
      └────→ Agent investigation
               ↓
           Code inspection
               ↓
           Documentation
               ↓
           Experiment option
```

Example:

```text
Quick answer

"This configuration controls Spark's shuffle
partition count."

Investigating your repository...

✓ Found configuration
✓ Found affected aggregation
✓ Found Spark version

I can test whether 200 is appropriate for this
workload.

[Design Experiment]
```

---

# Analytics

`repolab/analytics/`

Instrument product events from the beginning.

Capture:

```text
repo_submitted
repo_manifest_created
repo_ready
first_successful_run

lesson_started
lesson_completed
hint_requested

question_asked
quick_answer_generated
deep_answer_generated
evidence_opened

experiment_proposed
experiment_approved
experiment_started
experiment_completed

command_executed
code_modified

challenge_started
challenge_completed
challenge_failed

build_started
build_failed
build_recovered
```

Derived KPIs:

```text
Time to Repo Ready
Time to First Successful Run
Time to First Experiment

Lesson Completion Rate
Challenge Success Rate
Hint Usage

Experiments per User
Experiment Completion Rate

Questions → Experiments Conversion

Build Success Rate
Agent Recovery Rate
```

Long-term enterprise north-star metric:

> **Time to First Meaningful Contribution**

RepoLab should eventually help organizations understand:

- which repositories are hardest to learn
- which concepts cause the most difficulty
- where onboarding stalls
- how much developers experiment
- how quickly developers become productive

---

# Agent Evaluation Metrics

Capture:

```text
tool calls per task
successful tool calls
failed tool calls
agent retries
build recovery attempts
experiment-plan acceptance
schema-validation failures

model latency
model tokens
estimated model cost

sandbox compute time
```

Derived:

```text
Task Success Rate
Build Recovery Rate
Experiment Success Rate
Average Agent Steps
Cost per Successful Repo
Cost per Experiment
Time to Experience Ready
```

Do not optimize solely for fewer agent steps.

Correctness, reproducibility, and evidence quality matter more.

---

# Safety

User code runs only inside:

- Nebius Sandboxes
- dedicated isolated Nebius VM environments

Never on the API host.

Secrets come from environment variables.

Never expose or log secrets.

Restrict sandbox network access to what the build requires.

Agents cannot bypass approval gates.

Shell execution must pass through the sandbox tool interface.

Configure:

```text
execution timeout
build timeout
maximum agent iterations
maximum retries
compute limits
experiment limits
```

All agent actions must be auditable.

---

# Database

Postgres stores:

```text
users
sessions
repositories
repo_manifests

jobs
job_events

agent_runs
agent_steps
tool_calls

builds
environments

lessons
lesson_progress

experiments
experiment_variants
measurements

evidence

analytics_events
```

Redis is used for:

```text
RQ
temporary coordination
short-lived execution state
```

Postgres remains the source of truth.

---

# Engineering Conventions

## Python

- Python 3.11
- type hints
- Pydantic for API and LLM schemas
- `ruff`
- `pytest`

## TypeScript

- strict mode
- no `any`

## Architecture

- small modules
- clear interfaces
- dependency injection
- explicit errors
- structured logging

No placeholder logic in core demo paths.

---

# External Service Interfaces

Every external system sits behind an interface.

Examples:

```text
LLMProvider
ResearchProvider
SandboxProvider
ExecutionEnvironment
RepositoryProvider
```

Provide fake implementations.

Tests must run offline.

Example:

```python
class SandboxProvider(Protocol):

    async def create(...) -> Sandbox:
        ...

    async def execute(...) -> CommandResult:
        ...

    async def checkpoint(...) -> Checkpoint:
        ...
```

This prevents RepoLab from becoming tightly coupled to infrastructure implementations.

---

# How Claude Should Work in This Repository

For large changes:

1. inspect the existing implementation
2. understand the current milestone
3. propose a concise implementation plan
4. wait for approval
5. implement only the approved milestone
6. run relevant tests
7. fix failures
8. update documentation
9. update `docs/PROGRESS.md`

**Do not start the next hackathon milestone automatically.**

The current P0 milestone always takes priority over later features.

Before changing architecture, inspect the relevant existing code.

Do not rewrite working components unnecessarily.

Prefer incremental changes.

Do not introduce abstractions that are not needed by the current or immediately following milestone.

---

# External API Rule

When uncertain about Nebius, NVIDIA, Tavily, GitHub, or another external API:

> **Read the current official documentation. Never guess API behavior.**

In particular, before implementing Nebius Sandboxes:

1. read the current Token Factory Sandbox documentation
2. confirm the current Python SDK
3. verify method names
4. verify checkpoint behavior
5. verify execution timeout behavior
6. verify networking behavior

Do not generate SDK calls based purely on assumptions.

---

# Testing Rule

After every meaningful implementation change:

```text
Run relevant tests
      ↓
Tests fail?
   │       │
  yes      no
   │       │
   ▼       ▼
 Fix      Continue
```

Core paths require tests.

External providers require fake implementations so tests run offline.

Do not mark work complete while relevant tests are failing.

---

# Progress Tracking

Maintain:

```text
docs/PROGRESS.md
```

with:

```text
Completed
In Progress
Next
Blocked
Known Issues
Demo Status
```

Update it after every significant milestone.

---

# Product Definition

RepoLab is **not**:

- another repository chatbot
- another AI documentation generator
- another online IDE
- another RAG wrapper

RepoLab is an:

> **Agentic experimentation and learning environment for real software.**

The agent investigates before answering.

When possible, it tests hypotheses against the real application.

The architecture intentionally separates:

```text
Reasoning
    ↓
NVIDIA Nemotron

Actions
    ↓
Deterministic Tools

Execution
    ↓
Isolated Nebius Environments

Measurements
    ↓
Deterministic Instrumentation

Evidence
    ↓
Code + Logs + Metrics + Official Docs

Explanation
    ↓
Nemotron Grounded in Evidence

Control
    ↓
User Approval
```

# Final Principles

> **Don't just tell. Show.**

> **RepoLab doesn't generate an answer when it can generate an experiment.**

> **Models reason about measurements. Models never manufacture measurements.**

> **When RepoLab can test a claim against the real software, prefer evidence over speculation.**