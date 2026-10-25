/**
 * An architecture view shaped like the API's, for component tests.
 *
 * A small data pipeline: entry point -> pipeline -> read -> join -> write -> S3.
 */

import type {
  ArchitectureEdge,
  ArchitectureNode,
  ArchitectureView,
  Evidence,
  NodeActions,
  RepoManifest,
  Subsystem,
  SystemConcept,
} from "@/lib/types";

const COMMIT = "f".repeat(40);

export function node(
  id: string,
  label: string,
  category: ArchitectureNode["category"],
  path: string,
  line: number,
  extra: Partial<ArchitectureNode> = {},
): ArchitectureNode {
  return {
    id,
    label,
    category,
    subtype: null,
    technology: null,
    description: null,
    runtime_significance: null,
    source_references: [
      { path, start_line: line, end_line: line, commit_sha: COMMIT, excerpt: `line ${line} of ${path}` },
    ],
    properties: {},
    confidence: 0.8,
    provenance: "static",
    detected_because: `${path}:${line} defines it`,
    inferred_fields: [],
    children: [],
    surface: null,
    ...extra,
  };
}

export function edge(source: string, target: string, type: ArchitectureEdge["type"], provenance: ArchitectureEdge["provenance"] = "static"): ArchitectureEdge {
  return { source, target, type, description: null, provenance, evidence: [], confidence: 0.8 };
}

const noExperiment = (reason: string): NodeActions => ({
  ask: true,
  experiment: { available: false, backend: null, reason, question: null },
  suggested_questions: ["What does it do, and why does it exist?"],
});

export function pipelineView(overrides: Partial<ArchitectureView["graph"]> = {}): ArchitectureView {
  const nodes = [
    node("entry:job", "job", "entry_point", "jobs/job.py", 20),
    node("pipeline:job", "Sales pipeline", "pipeline", "jobs/job.py", 4, {
      technology: "Spark",
      description: "Totals revenue per region.",
      inferred_fields: ["description"],
      properties: { settings: ["spark.sql.shuffle.partitions=200"] },
    }),
    node("step:read", "Read Orders", "input", "jobs/job.py", 3),
    node("step:join", "Join Customers", "transform", "jobs/job.py", 6),
    node("step:write", "Write Report", "output", "jobs/job.py", 12),
    node("store:s3", "Object storage (S3)", "storage", "jobs/job.py", 4),
  ];
  return {
    job_id: "job-1",
    repo_url: "https://github.com/example/shop",
    commit_sha: COMMIT,
    generated_by: "super_enriched",
    node_count: nodes.length,
    edge_count: 6,
    summary: "A Spark job that totals revenue per region.",
    graph: {
      schema_version: 2,
      nodes,
      edges: [
        edge("entry:job", "pipeline:job", "control_flow"),
        edge("pipeline:job", "step:read", "control_flow"),
        edge("step:read", "step:join", "data_flow"),
        edge("step:join", "step:write", "data_flow"),
        edge("step:write", "store:s3", "writes_to"),
        edge("step:join", "store:s3", "depends_on", "inferred"),
      ],
      entry_points: ["entry:job"],
      important_files: [
        { path: "jobs/job.py", role: "Entry point", node_ids: ["entry:job", "pipeline:job", "step:join"] },
        { path: "conf/app.json", role: "Configuration", node_ids: [] },
      ],
      interaction_surfaces: ["data_playground"],
      summary: "A Spark job that totals revenue per region.",
      summary_provenance: "inferred",
      primary_flows: [
        {
          name: "job → Write Report",
          node_ids: ["entry:job", "pipeline:job", "step:read", "step:join", "step:write"],
          description: null,
          provenance: "static",
        },
      ],
      default_view: "data_flow",
      completeness: "complete",
      gaps: [],
      generated_by: "super_enriched",
      dropped: {},
      ...overrides,
    },
    node_actions: {
      "entry:job": noExperiment("the spark experiment does not exercise a entry point"),
      "pipeline:job": {
        ask: true,
        experiment: {
          available: true,
          backend: "spark",
          reason: "RepoLab can run this Spark job",
          question: "Why does this use spark.sql.shuffle.partitions=200?",
        },
        suggested_questions: ["Why does this use spark.sql.shuffle.partitions=200?"],
      },
      "step:read": noExperiment("x"),
      "step:join": noExperiment("x"),
      "step:write": noExperiment("x"),
      "store:s3": noExperiment("x"),
    },
  };
}

export const manifest = {
  repo_url: "https://github.com/example/shop",
  commit_sha: COMMIT,
  languages: ["Python"],
  frameworks: ["Spark"],
  entrypoints: [],
  services: [],
  dependencies: [],
  interaction_surfaces: [
    { type: "data_playground", confidence: 0.9, reason: "spark", primary: true, evidence: [] },
  ],
  concepts: [],
  confidence: 0.8,
} as unknown as RepoManifest;

/**
 * The same pipeline inside a larger, multi-part repository, with every level:
 * what it is, three concepts, three parts, a walkthrough and a learning path.
 * Machine-learning wrappers live deep in one part.
 */
export function guidedView(): ArchitectureView {
  const base = pipelineView();
  const ev = (kind: Evidence["kind"], label: string, extra: Partial<Evidence> = {}): Evidence => ({
    kind,
    label,
    path: null,
    line: null,
    url: null,
    target_id: null,
    excerpt: null,
    ...extra,
  });
  const nodes: ArchitectureNode[] = [
    ...base.graph.nodes.map((n) => ({ ...n, subsystem: "sub:jobs", level: "component" as const, learning_importance: 1 })),
    {
      ...node("pipeline:kmeans", "Kmeans Wrapper pipeline", "pipeline", "mllib/src/kmeans_wrapper.py", 4),
      subsystem: "sub:mllib",
      learning_importance: 0.5,
    },
    {
      ...node("step:kmeans-read", "Read parquet (kmeans_wrapper.py)", "input", "mllib/src/kmeans_wrapper.py", 5),
      subsystem: "sub:mllib",
    },
    {
      ...node("service:master", "MasterDaemon", "service", "core/src/deploy/MasterDaemon.java", 3, { subtype: "daemon" }),
      subsystem: "sub:core",
      learning_importance: 3,
    },
  ];
  const subsystem = (id: string, label: string, path: string, components: string[], extra: Partial<Subsystem> = {}): Subsystem => ({
    id,
    label,
    description: null,
    paths: [path],
    declared_by: "Maven module",
    file_count: 10,
    languages: ["Scala"],
    learning_importance: 1,
    importance_signals: { "central name": 1 },
    component_ids: components,
    starting_points: [],
    outline: [],
    evidence: [ev("build", "pom.xml:4", { path: "pom.xml", line: 4 })],
    provenance: "static",
    inferred_fields: [],
    ...extra,
  });
  const subsystems = [
    subsystem("sub:core", "Core engine", "core", ["service:master"], {
      description: "Scheduling, execution and networking.",
      inferred_fields: ["label", "description"],
      file_count: 140,
      starting_points: [
        { path: "core/src", is_directory: true, reason: "Where this part's code starts.", learn_about: ["scheduler", "executor"] },
        { path: "core/src/deploy/MasterDaemon.java", is_directory: false, reason: "Implements MasterDaemon.", learn_about: [] },
      ],
    }),
    subsystem("sub:jobs", "Example jobs", "jobs", base.graph.nodes.map((n) => n.id)),
    subsystem("sub:mllib", "Machine learning (MLlib)", "mllib", ["pipeline:kmeans", "step:kmeans-read"]),
  ];
  const concept = (id: string, label: string, extra: Partial<SystemConcept>): SystemConcept => ({
    id,
    label,
    term: null,
    what_it_does: "",
    why_it_matters: "",
    role: "",
    subsystem_ids: [],
    member_ids: [],
    evidence: [],
    confidence: 0.6,
    provenance: "inferred",
    learning_importance: 1,
    ...extra,
  });
  const doc = "https://engine.example.org/docs/cluster-overview.html";
  const graph: ArchitectureView["graph"] = {
    ...base.graph,
    schema_version: 3,
    nodes,
    understanding: {
      name: "Example Engine",
      one_line_description: "An engine that processes large datasets across many machines.",
      repository_type: "distributed_system",
      type_provenance: "inferred",
      type_candidates: ["distributed_system", "data_platform"],
      type_reasons: ["it describes itself with “distributed” (README.md:5)"],
      beginner_explanation: "You write a program against its APIs. It splits the work into tasks that run on many machines.",
      primary_purpose: "Process large datasets in parallel.",
      primary_users: "Data engineers.",
      languages: ["Scala", "Python"],
      frameworks: [],
      main_interaction_surfaces: ["Python API", "CLI"],
      recommended_start: "How does a program become work on many machines?",
      confidence: 0.7,
      evidence: [
        ev("readme", "README.md:5", { path: "README.md", line: 5, excerpt: "Example Engine is a distributed engine" }),
        ev("documentation", "Cluster overview", { url: doc }),
      ],
      inferred_fields: ["one_line_description", "beginner_explanation"],
    },
    subsystems,
    system_model: {
      provenance: "inferred",
      concepts: [
        concept("concept:app", "Your application", {
          what_it_does: "The program a developer writes.",
          subsystem_ids: ["sub:jobs"],
          member_ids: ["pipeline:job"],
        }),
        concept("concept:scheduler", "Scheduler", {
          term: "DAGScheduler",
          what_it_does: "Decides which pieces of work run, and where.",
          why_it_matters: "Work must be split to run on many machines.",
          subsystem_ids: ["sub:core"],
          evidence: [ev("source", "core/src/scheduler/", { path: "core/src/scheduler" })],
        }),
        concept("concept:executors", "Executors", {
          what_it_does: "Processes on worker machines that run tasks.",
          subsystem_ids: ["sub:core"],
          member_ids: ["service:master"],
        }),
      ],
      links: [
        { source: "concept:app", target: "concept:scheduler", type: "calls", description: null, provenance: "inferred", evidence: [ev("documentation", "Cluster overview", { url: doc })] },
        { source: "concept:scheduler", target: "concept:executors", type: "control_flow", description: null, provenance: "inferred", evidence: [] },
      ],
    },
    walkthroughs: [
      {
        id: "walk:system",
        name: "Follow a job",
        kind: "job",
        level: "system",
        steps: [
          { target_id: "concept:app", label: "Your application", detail: "You call an action." },
          { target_id: "concept:scheduler", label: "Scheduler", detail: "It splits the job." },
          { target_id: "concept:executors", label: "Executors", detail: null },
        ],
        description: null,
        provenance: "inferred",
        subsystem: null,
      },
      {
        id: "walk:0",
        name: "Follow a job",
        kind: "job",
        level: "component",
        steps: [
          { target_id: "pipeline:kmeans", label: "Kmeans Wrapper pipeline", detail: null },
          { target_id: "step:kmeans-read", label: "Read parquet (kmeans_wrapper.py)", detail: null },
        ],
        description: null,
        provenance: "static",
        subsystem: "sub:mllib",
      },
    ],
    learning_path: [
      { title: "What problem does it solve?", why: "", target_kind: "question", target_id: null, question: "Why use it?", provenance: "inferred" },
      { title: "The scheduler", why: "", target_kind: "concept", target_id: "concept:scheduler", question: null, provenance: "inferred" },
      { title: "Explore machine learning", why: "", target_kind: "subsystem", target_id: "sub:mllib", question: null, provenance: "static" },
    ],
  };
  const later = (reason: string): NodeActions => ({
    ask: true,
    experiment: { available: false, backend: null, reason, question: null, needs_environment: true },
    suggested_questions: ["What does it do?"],
  });
  return {
    ...base,
    node_count: nodes.length,
    graph,
    node_actions: {
      ...base.node_actions,
      "pipeline:kmeans": later("x"),
      "step:kmeans-read": later("x"),
      "service:master": later("no environment has been built yet"),
    },
    concept_actions: {
      "concept:scheduler": { ...later("x"), suggested_questions: ["What does Scheduler do?", "Why does Example Engine need Scheduler?"] },
      "concept:app": base.node_actions["pipeline:job"],
      "concept:executors": later("x"),
    },
  };
}

/** A small web backend: server -> two APIs -> their models -> one database. */
export function webView(): ArchitectureView {
  const base = pipelineView();
  const nodes = [
    node("service:server", "Web server", "service", "backend/app.js", 3, { technology: "Express" }),
    node("api:orders", "Orders API", "api", "backend/routes/orders.js", 2, { properties: { routes: ["GET /api/orders"] } }),
    node("api:users", "Users API", "api", "backend/routes/users.js", 2),
    node("model:order", "Order model", "model", "backend/models/order.js", 1, { technology: "Mongoose" }),
    node("db:main", "Database", "database", "backend/app.js", 4, {
      technology: "MongoDB",
      description: "Where orders and users are stored.",
    }),
  ];
  return {
    ...base,
    node_count: nodes.length,
    edge_count: 4,
    graph: {
      ...base.graph,
      schema_version: 3,
      nodes,
      edges: [
        edge("service:server", "api:orders", "routes_to"),
        edge("service:server", "api:users", "routes_to"),
        edge("api:orders", "model:order", "calls"),
        edge("model:order", "db:main", "writes_to", "inferred"),
      ],
      primary_flows: [],
      understanding: null,
    },
    node_actions: {
      "service:server": noExperiment("x"),
      "api:orders": {
        ...noExperiment("x"),
        suggested_questions: ["What happens when a request reaches GET /api/orders?", "Which components does Orders API call?"],
      },
      "api:users": noExperiment("x"),
      "model:order": noExperiment("x"),
      "db:main": {
        ...noExperiment("x"),
        suggested_questions: ["Which components write to Database?", "How does data reach Database?"],
      },
    },
  };
}
