# Architecture — `/r/{jobId}/architecture`

## Purpose

Answer "what is in this repository and how does it fit together?" — with every
claim traceable to a file. The screen's job is to make the manifest legible, not
to decorate it.

## Reads

- `GET /jobs/{jobId}/architecture` — nodes, edges, counts, `generated_by`.
- `GET /jobs/{jobId}/manifest` — languages, frameworks, entry points, services,
  interaction surfaces, **concepts**, confidence.

## Layout

Four sections, in the order the spec names them.

**Components** — one card per node, grouped by kind (entry point, service,
config, datastore, external). Each shows its name, its description, and the
files that justify it. A node with no file reference is not rendered: the
backend already drops unsupported nodes, and the screen must not reintroduce
them.

**Data flow** — an SVG graph of nodes and edges, laid out in columns by kind
(entry points → config → services → external), with edge labels from
`EdgeKind`. Hovering a node highlights its edges. Below it, the same
relationships as a readable list, so the information does not depend on being
able to interpret a diagram.

**Important files** — the file references gathered across nodes and concepts,
ranked by how many claims cite them. The repository's entry point and
configuration surface here naturally.

**Concepts** — from `manifest.concepts`: name, summary, and the code references
behind it. This is the one place the screen explains rather than lists, and
every concept still cites files.

A header strip carries: primary surface, confidence, languages, frameworks, and
whether the graph is `deterministic` or `super_enriched` — because a reader
should know whether a model touched it.

## States

- **Not ready** — the endpoint 404s until the investigation finishes; show the
  phase tracker's current position instead of an error.
- **Sparse** — a repository with few detected frameworks renders fewer cards and
  says the confidence is low, rather than padding.

## Must never show

- A component, edge or concept with no file reference.
- A confidence of 1.0. Deterministic evidence alone never claims certainty, and
  the backend caps it at 0.9.
- An inferred relationship presented identically to a detected one.
