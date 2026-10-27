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
| Code model (`code_model.py`) | Reads the non-test source (budgeted: 8,000 files / 60 MB), indexes every definition with its line range (`repolab/sources/symbols.py`), ranks top-level types by how many other files use them (plus a generic runtime-role vocabulary), and records **relations** -- one type's own code calling, creating, extending or naming another -- each at a verified line, strings and comments excluded | yes |
| Runtime model | Components made of verified types; links only where a relation exists between their members; flows along calls/constructions; a reading path | yes |
| Super, runtime pass (`runtime_synthesis.py`) | Groups verified types into 5–8 named components, writes descriptions and responsibilities tied to a member, labels what passes along existing links, names flows along existing links. Cannot add a member, link or step | model |
| Modules (`modules.py`) | Subsystem dependencies from build files: Maven `<dependency>` on a sibling artifact (test scope excluded), Gradle `project(':x')`, npm workspace packages, Cargo `path =` -- each at its verified build-file line | yes |
| Verification (`verify.py`) | Every reference re-read at the investigated commit; anything unconfirmed is marked unverified or removed (runtime parts); references from another commit are never re-resolved | yes |

### Source references

A reference is `{path, start_line, end_line, commit_sha, symbol, strength,
verified, verification}`. **Line numbers are never taken from a model:**

- the model may name a file and a symbol (`path#Name`); RepoLab's resolver
  (`repolab/sources/resolve.py`) finds the definition, computes its range, re-reads
  it and checks the name is there;
- a `path:line` is accepted only if it is exactly one RepoLab showed the model;
  any other line is reduced to the file;
- without a symbol, a line counts only if its expected text is there (±3 lines,
  recorded as "corrected from line N");
- a reference made at another commit is refused, not re-resolved.

`strength` says how directly the file shows the claim: source, build file,
configuration, test, repository docs, inferred from several files.

Cited files are copied into `source_files` (repository, commit, path, sha256)
before the clone is deleted, and served by `GET /jobs/{id}/source` and
`GET /sources?repo_url&commit_sha&path` -- never another version. Ask RepoLab now
checks out the investigated commit (`fetch_commit`) and records the commit its
citations came from.

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

## Perspectives

Overview · Runtime · Flows · Modules · Source map (plus Learning path and
Related experiments). The header says "Architecture analyzed at commit …".

- **Overview:** what this is, then **Core ideas** -- the runtime components,
  numbered -- and the runtime diagram with labelled arrows; then the concept
  model, parts and walkthrough as before.
- **Runtime:** the diagram and the components. Selecting one opens its panel:
  description, responsibilities (each with a `SourceRef`), source, connections
  with the line that shows each, related flows, Ask RepoLab with suggested
  questions, an experiment only where one exists, and **Why RepoLab thinks
  this** (member definitions, usage counts, every connection's line and its
  evidence strength).
- **Flows:** steps (component, action, input/output, the hand-over line); a
  **sequence view only when every hand-over is a verified call or
  construction** -- otherwise "Order not verified" and no sequence.
- **Modules:** purpose, key areas, depends on / used by with the build-file line.
- **Source map:** the reading path, then the repository map with the runtime
  components living in each part.

Every citation is a `SourceRef` chip; clicking it opens the **source viewer**:
repository, commit, file, highlighted range, "RepoLab re-read these lines", and a
link to the same commit on GitHub. An unverified reference says "Source location
could not be verified" and has no link.

## Layout (older investigations, and the Overview's lower half)

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
