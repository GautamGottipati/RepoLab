# Design

Screen specifications for the RepoLab web experience. CLAUDE.md directs frontend
work here.

| spec | screen | route |
|---|---|---|
| [00-overview.md](00-overview.md) | shell, navigation, shared vocabulary | — |
| [01-import.md](01-import.md) | Import | `/` |
| [02-investigation.md](02-investigation.md) | Investigation | `/r/{jobId}` |
| [03-architecture.md](03-architecture.md) | Architecture | `/r/{jobId}/architecture` |
| [04-playground.md](04-playground.md) | Spark Playground | `/r/{jobId}/playground` |
| [05-ask.md](05-ask.md) | Ask RepoLab | `/r/{jobId}/ask` |
| [06-experiment-results.md](06-experiment-results.md) | Experiment Results | `/r/{jobId}/ask/{experimentId}` |
| [07-learn.md](07-learn.md) | Learn (terminal + lesson) | `/r/{jobId}/learn` |
| [08-experiences.md](08-experiences.md) | Available Experiences | `/r/{jobId}/experiences` |
| [09-metrics.md](09-metrics.md) | Metrics (KPI dashboard) | `/metrics` |

Each spec states what the screen is for, the endpoints it reads, its states, and
**what it must never show**. That last section is not decoration: the rules it
records — no private chain-of-thought, no unlabelled claim, no number without a
source — are the ones that distinguish RepoLab from a repository chatbot, and
they are easiest to break in a UI.
