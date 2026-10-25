# Architecture — `/r/{jobId}/architecture`

## Purpose

Take someone who has never seen this repository from *"what is this?"* to the
lines that implement it — for any repository, with every claim traceable.

Within about 30 seconds a beginner should be able to say what the software is,
what problem it solves, roughly how it works, what its major pieces are, how
they communicate, and where to start. An expert reaches components, source
evidence and runtime measurements by drilling in. There is no mode switch.

```
repository   what it is · type · plain explanation · how we know
    ↓
system       3–8 concepts of how it works, and how they connect
    ↓
subsystems   the repository's major parts            → Explore
    ↓
components   one part's runtime components (graph)
    ↓
source       inspector references at the investigated commit
```

## Reads

- `GET /jobs/{jobId}/architecture`: the graph (schema v3), `node_actions` per
  component, and `concept_actions` per concept and subsystem.
  - **Ask** is always available.
  - **Experiment** is offered only where RepoLab can really run something that
    exercises the component, or a member of the concept or subsystem.
    `needs_environment` marks the case where only a build is missing.
  - Suggested questions are filled from the subject's own values.
- `GET /jobs/{jobId}/architecture/runtime`: measured values from real runs.
  - They are placed on components (`measurements`) and repeated on the concept
    and subsystem that contain them (`by_group`). Values are never combined.
  - Empty until something has run.
- `GET /jobs/{jobId}/manifest`: languages, surfaces, concepts.

## How it is built (`repolab/architecture/`)

| Layer | What it does | Deterministic? |
|---|---|---|
| Subsystems (`subsystems.py`) | From declared modules (Maven, Gradle, sbt, npm/pnpm/lerna, Cargo, go.work), else source directories. Paths decide membership. ≤12, the rest folded and listed. `learning_importance` with signals recorded; starting points | yes |
| Analyzers + adapters | Runtime components and evidenced edges (unchanged from Phase 15); adapters enrich only | yes |
| Assembly | Puts each component in a subsystem, then groups to ≤20 per subsystem, never across subsystems | yes |
| Super, component pass | Names, descriptions, cited relationships, flows, summary | model |
| Orientation, deterministic (`understanding.py`) | README and package fact sheet, repository-type scoring, baseline concepts (top subsystems, or components in a small repository), walkthroughs from flows, learning path | yes |
| Super, orientation pass | Plain explanation, a type chosen among tied candidates, subsystem names, 3–8 concepts, cited links, walkthrough, learning path | model |
| Validation | Drops anything without a real file, line, directory, subsystem, component or *retrieved* documentation URL; also unknown ids, links or walkthrough steps that do not exist, and measurement-shaped numbers. Each refusal is counted | yes |

**Repository type is not an interaction surface.** A framework with a launcher
script is not a "CLI application". The type is:

- **decided by rules** when one kind clearly wins;
- **chosen by Super** among the tied candidates otherwise;
- **"hybrid"** only when the code shows two kinds of application.

**Generic by construction.**

- No repository names appear anywhere.
- Kinds come from structure and the project's own words.
- Parts come from build files and directories.
- Names and explanations are Super's, constrained to evidence.

## Layout

```
┌ breadcrumb: Name › Subsystem › Selected ─────────────────────────────┐
│ NAME                                       (big)                     │
│ [Repository type] Languages                                          │
│ one-line description                                                 │
│ ┌ What is this repository? ──────────────────────────────────────┐   │
│ │ plain explanation · what problem it solves · who uses it       │   │
│ │ ways to use it (surfaces) · How do you know? (evidence, why)   │   │
│ │ [first question →]                                             │   │
│ └────────────────────────────────────────────────────────────────┘   │
│ How does it work?   concept graph (Internal name: …)                 │
│ Explore the repository   cards: name · path/ · files · Explore →     │
│ Follow a request / job / command   stepped list                      │
│ New to this project?   numbered path (+ experiment when available)   │
│ Where should I start?   per part: 📁 dir — learn about …, 📄 file    │
│     ▸ Advanced: important implementation files                      │
│ ▸ ✓ Repository analyzed · N evidence · N components … (collapsed)   │
└──────────────────────────────────────────────────────────────────────┘
                                  ┌ Selected (when something is) ─┐
                                  │ What is this?                 │
                                  │ Why does it matter here?      │
                                  │ Role in this system           │
                                  │ How does it connect?          │
                                  │ Where is it implemented?      │
                                  │ — How RepoLab knows —         │
                                  │ why detected · source evidence│
                                  │ Static · Inferred · Measured  │
                                  │ Questions                     │
                                  │ [Open code] [Ask RepoLab]     │
                                  │ [Run experiment]*             │
                                  └───────────────────────────────┘
```

**Explore** on a card (or a concept's "Explore part") opens that subsystem:

- its description and how RepoLab knows it is a part;
- its starting points;
- its component graph (layered SVG, inferred edges dashed) and flows;
- its own walkthroughs;
- its important files.

The state lives in `?subsystem=…&node=…`.

\* Experiment only when available. When the only missing thing is a built
environment, one muted line says so; otherwise nothing is shown.

**The agent-activity sidebar** shows the live feed while the job runs. Once the
job completes, it folds into "✓ Repository analyzed · N pieces of evidence ·
N components · N flows" with **View analysis details**.

## Ask integration

Every concept, subsystem and component links to Ask RepoLab with
`?node=<id>&q=<question>`. The Ask page sends `context`:

- label and category;
- `level` (system, subsystem or component);
- the subsystem's label;
- ≤8 file references and ≤6 directories;
- ≤10 relationships.

The answer agent reads those files first. There is no separate chatbot.

## States

- **Not ready:** the endpoint 404s until the investigation finishes.
- **Partial:** a quiet note under the orientation. The gaps are listed in the
  analysis details. An unknown repository shows no concepts rather than
  invented ones.
- **Older investigation (schema ≤ 2):** the component view, with a note to
  investigate again.

## Must never show

- A concept, component or relationship without evidence a reader can open.
- Implementation detail from one part (e.g. ML wrappers) on the repository
  overview.
- A measured value that did not come from a stored run, or one shown without
  its run.
- An inferred value styled as static or measured.
- An Experiment button where no RepoLab backend exercises the subject.
