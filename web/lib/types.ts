/**
 * Types mirroring the RepoLab API.
 *
 * Hand-written rather than generated, so each one carries why a field exists.
 * They are the browser's contract with the backend; `npm run build` type-checks
 * every screen against them, which is what catches a field that was renamed on
 * the other side.
 */

export type JobStatus = "queued" | "running" | "completed" | "failed";

export type EventType =
  | "agent_started"
  | "agent_status"
  | "tool_requested"
  | "tool_started"
  | "tool_completed"
  | "step_started"
  | "progress"
  | "log_line"
  | "partial_result"
  | "step_done"
  | "failed"
  | "heartbeat"
  | "approval_required"
  | "approval_received"
  | "experiment_variant_started"
  | "experiment_variant_completed"
  | "upgrade_available"
  | "upgrade_applied"
  | "artifact_queued"
  | "artifact_started"
  | "artifact_verifying"
  | "artifact_ready"
  | "artifact_updated"
  | "artifact_failed"
  | "artifact_blocked";

/**
 * One event from a job's stream.
 *
 * `message` is a concise, user-facing status. `data` holds structured facts.
 * Neither ever contains private model reasoning — the backend does not persist
 * any — and the UI reads only the keys it names.
 */
export type JobEvent = {
  job_id: string;
  seq: number;
  type: EventType;
  message: string;
  step: string | null;
  data: Record<string, unknown>;
  created_at: string;
};

export type TaskHealth = "waiting" | "working" | "interrupted";

/** A stage and a machine key (`architecture.components`, `image`…), set where the work begins. */
export type TaskActivity = { stage: string; key: string; started_at: string | null };

export type JobState = {
  job_id: string;
  status: JobStatus;
  repo_url: string;
  attempts: number;
  last_seq: number;
  event_count: number;
  elapsed_seconds: number | null;
  /** From the worker's heartbeat, never from event silence. `null` once finished. */
  health: TaskHealth | null;
  /** What the worker is doing now. The screen owns the words (`ACTIVITY_COPY`). */
  activity: TaskActivity | null;
  heartbeat_at: string | null;
  /** Which worker holds the job. Diagnostics only. */
  worker: string | null;
  /** When the last milestone event was recorded. Diagnostics only. */
  last_progress_at: string | null;
  error: string | null;
  ingestion_id: number | null;
};

export type JobCreated = {
  job_id: string;
  status: JobStatus;
  repo_url: string;
  events_url: string;
};

// -- manifest ---------------------------------------------------------------

export type EvidenceStrength =
  | "direct_source"
  | "build_metadata"
  | "configuration"
  | "test"
  | "in_repository_documentation"
  | "inferred_from_multiple_files";

export type CodeReference = {
  path: string;
  start_line: number | null;
  end_line: number | null;
  commit_sha: string | null;
  excerpt: string | null;
  /** The definition the range covers, when there is one. */
  symbol?: string | null;
  strength?: EvidenceStrength | null;
  /** Set only by RepoLab's resolver, which computed and re-read the range. */
  verified?: boolean;
  /** Why it is (un)verified, or what was corrected ("corrected from line 12"). */
  verification?: string | null;
};

export type InteractionSurface = {
  type: string;
  confidence: number;
  reason: string;
  primary: boolean;
  evidence: CodeReference[];
};

export type Concept = {
  name: string;
  summary: string;
  code_references: CodeReference[];
};

export type EntryPoint = { name: string; command: string; path: string | null; kind: string };
export type Service = { name: string; image: string | null; ports: number[] };
export type Dependency = { name: string; version_spec: string | null; ecosystem: string };

export type RepoManifest = {
  repo_url: string;
  commit_sha: string;
  languages: string[];
  frameworks: string[];
  entrypoints: EntryPoint[];
  services: Service[];
  dependencies: Dependency[];
  interaction_surfaces: InteractionSurface[];
  concepts: Concept[];
  confidence: number;
};

// -- architecture -----------------------------------------------------------

/** Broad architectural roles. The generic graph never depends on `subtype`. */
export type NodeCategory =
  | "entry_point"
  | "input"
  | "output"
  | "frontend"
  | "api"
  | "service"
  | "worker"
  | "database"
  | "cache"
  | "queue"
  | "storage"
  | "pipeline"
  | "transform"
  | "processor"
  | "scheduler"
  | "task"
  | "cli"
  | "command"
  | "model"
  | "inference"
  | "external_service"
  | "library"
  | "core_engine"
  | "configuration"
  | "other";

export type EdgeType =
  | "data_flow"
  | "control_flow"
  | "calls"
  | "reads_from"
  | "writes_to"
  | "publishes_to"
  | "consumes_from"
  | "triggers"
  | "depends_on"
  | "routes_to"
  | "creates";

/** `static` is read from source; `inferred` is interpreted. Measured values live in the overlay. */
export type Provenance = "static" | "inferred";

export type PropertyValue = string | number | boolean | string[];

export type ArchitectureNode = {
  id: string;
  label: string;
  category: NodeCategory;
  subtype: string | null;
  technology: string | null;
  description: string | null;
  runtime_significance: string | null;
  source_references: CodeReference[];
  properties: Record<string, PropertyValue>;
  confidence: number;
  provenance: Provenance;
  detected_because: string;
  /** Fields a model wrote, shown as inferred. */
  inferred_fields: string[];
  children: string[];
  surface: string | null;
  /** Absent on graphs stored before schema 3. */
  level?: ArchitectureLevel;
  subsystem?: string | null;
  learning_importance?: number;
};

export type ArchitectureEdge = {
  source: string;
  target: string;
  type: EdgeType;
  description: string | null;
  provenance: Provenance;
  evidence: CodeReference[];
  confidence: number;
};

export type ImportantFile = { path: string; role: string; node_ids: string[] };

export type ArchitectureFlow = {
  name: string;
  node_ids: string[];
  description: string | null;
  provenance: Provenance;
};

export type ArchitectureViewKind =
  | "data_flow"
  | "request_flow"
  | "service_flow"
  | "workflow"
  | "command_flow"
  | "component_flow"
  | "system_overview";

export type ArchitectureGraph = {
  schema_version: number;
  nodes: ArchitectureNode[];
  edges: ArchitectureEdge[];
  entry_points: string[];
  important_files: ImportantFile[];
  interaction_surfaces: string[];
  summary: string | null;
  summary_provenance: Provenance;
  primary_flows: ArchitectureFlow[];
  default_view: ArchitectureViewKind;
  completeness: "complete" | "partial";
  gaps: string[];
  generated_by: "deterministic" | "super_enriched";
  dropped: Record<string, number>;
  /** Schema 3: the progressive levels. Absent or empty on older graphs. */
  understanding?: RepositoryUnderstanding | null;
  system_model?: SystemModel;
  subsystems?: Subsystem[];
  learning_path?: LearningStep[];
  walkthroughs?: Walkthrough[];
  /** Schema 4: the runtime architecture. Absent on older graphs. */
  abstractions?: KeyAbstraction[];
  relations?: CodeRelation[];
  runtime?: RuntimeModel;
  flows?: Flow[];
  reading_path?: ReadingItem[];
};

export type ArchitectureLevel = "system" | "subsystem" | "component" | "implementation";

export type RepositoryKind =
  | "distributed_system"
  | "framework"
  | "library"
  | "web_application"
  | "api_service"
  | "data_platform"
  | "workflow_system"
  | "database"
  | "cli_application"
  | "ml_system"
  | "developer_tool"
  | "hybrid"
  | "unknown";

export type EvidenceKind =
  | "readme"
  | "source"
  | "configuration"
  | "build"
  | "documentation"
  | "component"
  | "subsystem";

/** One citation behind an explanation: the answer to "how do you know?". */
export type Evidence = {
  kind: EvidenceKind;
  label: string;
  path: string | null;
  line: number | null;
  url: string | null;
  target_id: string | null;
  excerpt: string | null;
  end_line?: number | null;
  symbol?: string | null;
  verified?: boolean;
};

export type RepositoryUnderstanding = {
  name: string;
  one_line_description: string | null;
  repository_type: RepositoryKind;
  type_provenance: Provenance;
  type_candidates: RepositoryKind[];
  type_reasons: string[];
  beginner_explanation: string | null;
  primary_purpose: string | null;
  primary_users: string | null;
  languages: string[];
  frameworks: string[];
  main_interaction_surfaces: string[];
  recommended_start: string | null;
  confidence: number;
  evidence: Evidence[];
  /** Fields Nemotron wrote; everything else was quoted or computed. */
  inferred_fields: string[];
};

export type StartingPoint = {
  path: string;
  is_directory: boolean;
  reason: string;
  learn_about: string[];
};

export type Subsystem = {
  id: string;
  label: string;
  description: string | null;
  paths: string[];
  declared_by: string;
  file_count: number;
  languages: string[];
  learning_importance: number;
  importance_signals: Record<string, number>;
  component_ids: string[];
  starting_points: StartingPoint[];
  outline: string[];
  evidence: Evidence[];
  provenance: Provenance;
  inferred_fields: string[];
  /** Schema 4: from build files and imports. */
  depends_on?: ModuleDependency[];
  used_by?: string[];
  key_packages?: string[];
};

export type ModuleDependency = {
  target: string;
  via: string;
  evidence: CodeReference;
};

// -- schema 4: the runtime architecture, from verified code -------------------------

export type KeyAbstraction = {
  id: string;
  name: string;
  kind: string;
  definition: CodeReference;
  referenced_by: number;
  subsystem: string | null;
  usages: CodeReference[];
};

export type CodeRelationType = "creates" | "calls" | "extends" | "references";

export type CodeRelation = {
  source: string;
  target: string;
  type: CodeRelationType;
  evidence: CodeReference;
};

export type Responsibility = { text: string; reference: CodeReference; provenance: Provenance };

export type RuntimeComponent = {
  id: string;
  name: string;
  description: string | null;
  members: string[];
  responsibilities: Responsibility[];
  source_references: CodeReference[];
  subsystem_ids: string[];
  node_ids: string[];
  provenance: Provenance;
  inferred_fields: string[];
};

export type RuntimeLink = {
  source: string;
  target: string;
  type: CodeRelationType;
  label: string | null;
  label_provenance: Provenance;
  evidence: CodeReference[];
};

export type RuntimeModel = {
  components: RuntimeComponent[];
  links: RuntimeLink[];
  provenance: Provenance;
};

export type FlowStep = {
  sequence: number;
  component: string;
  action: string;
  input: string | null;
  output: string | null;
  references: CodeReference[];
};

export type Flow = {
  id: string;
  name: string;
  description: string | null;
  steps: FlowStep[];
  /** True only when every hand-over is a verified call or construction. */
  ordered: boolean;
  provenance: Provenance;
};

export type ReadingItem = {
  title: string;
  why: string;
  reference: CodeReference;
  component: string | null;
};

export type SourceFile = {
  repo_url: string;
  commit_sha: string;
  path: string;
  captured: boolean;
  sha256: string | null;
  first_line: number;
  lines: string[];
  line_count: number;
  host_url: string | null;
};

export type SystemConcept = {
  id: string;
  label: string;
  term: string | null;
  what_it_does: string;
  why_it_matters: string;
  role: string;
  subsystem_ids: string[];
  member_ids: string[];
  evidence: Evidence[];
  confidence: number;
  provenance: Provenance;
  learning_importance: number;
};

export type ConceptLink = {
  source: string;
  target: string;
  type: EdgeType;
  description: string | null;
  provenance: Provenance;
  evidence: Evidence[];
};

export type SystemModel = {
  concepts: SystemConcept[];
  links: ConceptLink[];
  provenance: Provenance;
};

export type LearningStep = {
  title: string;
  why: string;
  target_kind: "concept" | "subsystem" | "node" | "walkthrough" | "question";
  target_id: string | null;
  question: string | null;
  provenance: Provenance;
};

export type WalkthroughStep = { target_id: string; label: string; detail: string | null };

export type Walkthrough = {
  id: string;
  name: string;
  kind: "request" | "job" | "command" | "dag_run" | "call" | "event";
  level: ArchitectureLevel;
  steps: WalkthroughStep[];
  description: string | null;
  provenance: Provenance;
  /** Set when the path runs inside one subsystem: shown when that is explored. */
  subsystem: string | null;
};

export type ExperimentAvailability = {
  available: boolean;
  backend: "spark" | "cli" | null;
  reason: string;
  question: string | null;
  /** Unavailable only because nothing has been built yet. */
  needs_environment?: boolean;
};

export type NodeActions = {
  ask: boolean;
  experiment: ExperimentAvailability;
  suggested_questions: string[];
};

export type ArchitectureView = {
  job_id: string;
  repo_url: string;
  commit_sha: string;
  generated_by: string;
  node_count: number;
  edge_count: number;
  summary: string | null;
  graph: ArchitectureGraph;
  node_actions: Record<string, NodeActions>;
  /** The same, for concepts and subsystems, by their ids. */
  concept_actions?: Record<string, NodeActions>;
  /** Each architecture artifact's status, keyed without the `architecture.` prefix. */
  sections?: Record<string, ArtifactStatus>;
};

// -- analysis artifacts ---------------------------------------------------------

export type ArtifactStatus =
  | "not_requested"
  | "queued"
  | "generating"
  | "verifying"
  | "ready"
  | "partial"
  | "failed"
  | "blocked";

export type ArtifactType =
  | "repo.facts"
  | "repo.manifest"
  | "architecture.structure"
  | "architecture.runtime"
  | "architecture.source"
  | "architecture.components"
  | "architecture.summary"
  | "architecture.runtime_naming";

export type ArtifactError = {
  error_code: string;
  stage: string;
  message: string;
  retryable: boolean;
  timestamp?: string;
};

/** One piece of what RepoLab knows about a commit. The backend owns every status. */
export type ArtifactState = {
  type: ArtifactType;
  status: ArtifactStatus;
  priority: "P0" | "P1" | "P2";
  version: string;
  lazy: boolean;
  depends_on: ArtifactType[];
  description: string;
  updated_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  cache_hits: number;
  error: ArtifactError | null;
};

export type ArtifactDetail = ArtifactState & { payload: Record<string, unknown> | null };

export type ArtifactList = {
  job_id: string;
  /** `null` until the job has resolved which commit it analyses. */
  commit_sha: string | null;
  artifacts: ArtifactState[];
};

/** One value a real run produced. Never estimated. */
export type MeasuredValue = {
  name: string;
  value: number;
  unit: string;
  source: string;
  provenance: "measured";
};

export type RuntimeOverlay = {
  measurements: Record<string, MeasuredValue[]>;
  /** The same values on the concept/subsystem holding each component. Never combined. */
  by_group?: Record<string, MeasuredValue[]>;
  runs: string[];
  notes: string[];
};

/** The component a question is asked about, sent with it to Ask RepoLab. */
export type ArchitectureFocus = {
  node_id: string;
  label: string;
  category: string;
  source_references: CodeReference[];
  relationships: string[];
  level?: "system" | "subsystem" | "component";
  subsystem?: string | null;
  directories?: string[];
};

// -- the Spark playground ---------------------------------------------------

export type DatasetSpec = {
  rows: number;
  seed: number;
  columns: string[];
  skewed_key: string;
  skew_fraction: number;
  skewed_rows: number;
  sha256: string;
  size_bytes: number;
};

export type DatasetPreview = {
  spec: DatasetSpec;
  columns: string[];
  header: string;
  rows: string[][];
  preview_rows: number;
  total_rows: number;
  distinct_keys: number;
  sha256: string;
};

export type PartitionDistribution = {
  partitions: number;
  empty_partitions: number;
  max_records: number;
  median_records: number;
  total_records: number;
  top_records: number[];
};

export type TaskStats = {
  count: number;
  nonempty_count: number;
  slowest_ms: number;
  median_ms: number;
  fastest_ms: number;
  p95_ms: number;
  stddev_ms: number;
  slowest_task_id: number | null;
};

export type StageMeasurement = {
  stage_id: number;
  name: string;
  num_tasks: number;
  duration_ms: number | null;
  shuffle_bytes_read: number;
  shuffle_bytes_written: number;
};

export type SparkMeasurements = {
  app_id: string | null;
  spark_version: string | null;
  total_runtime_ms: number | null;
  stage_count: number;
  task_count: number;
  failed_task_count: number;
  shuffle_bytes_read: number;
  shuffle_records_read: number;
  shuffle_bytes_written: number;
  memory_bytes_spilled: number;
  disk_bytes_spilled: number;
  stages: StageMeasurement[];
  tasks: TaskStats;
  distribution: PartitionDistribution;
  event_log_lines: number;
};

export type Measurement = {
  name: string;
  value: number;
  unit: string;
  source: string;
  variant_id: string | null;
};

export type LogReference = { source: string; excerpt: string };
export type DocumentationReference = {
  url: string;
  title: string | null;
  excerpt: string | null;
  version: string | null;
};

export type EvidenceBundle = {
  code_references: CodeReference[];
  measurements: Measurement[];
  logs: LogReference[];
  documentation: DocumentationReference[];
  simulated: boolean;
};

export type RunView = {
  run_id: string;
  job_id: string | null;
  repo_url: string;
  commit_sha: string;
  dataset_hash: string;
  configuration: { shuffle_partitions?: number; master?: string; driver_memory?: string } | null;
  environment: { kind?: string; image?: string; spark_version?: string } | null;
  dataset: DatasetSpec | null;
  measurements: SparkMeasurements | null;
  evidence: EvidenceBundle | null;
  task_count: number;
  stage_count: number;
  total_runtime_ms: number | null;
  wall_clock_seconds: number;
  event_log_sha256: string;
  event_log_bytes: number;
  added_steps: string[];
};

export type RunStarted = {
  job_id: string;
  status: JobStatus;
  repo_url: string;
  shuffle_partitions: number;
  events_url: string;
  run_url: string;
};

// -- experiments ------------------------------------------------------------

export type ExperimentStatus =
  | "proposed"
  | "awaiting_approval"
  | "approved"
  | "running"
  | "completed"
  | "failed"
  | "rejected"
  /** The question was not one to test, and was answered from evidence. */
  | "answered";

export type ExperimentPlan = {
  question: string;
  hypothesis: string;
  variable: string;
  values: number[];
  fixed_settings: Record<string, string>;
  metrics: string[];
  repeats: number;
  estimated_runs: number;
  estimated_seconds: number;
  estimated_minutes: number;
  baseline_value: number;
  concurrency: number;
  dropped_metrics: string[];
  adjustments: string[];
};

export type Determinism = "structural" | "timing";

export type ComparisonPoint = {
  value: number;
  median: number;
  absolute_difference: number;
  relative_difference_percent: number | null;
  is_baseline: boolean;
};

export type MetricComparison = {
  metric: string;
  unit: string;
  determinism: Determinism;
  baseline_value: number;
  baseline_median: number;
  points: ComparisonPoint[];
  lowest_value: number | null;
  highest_value: number | null;
  span_percent: number | null;
};

export type Aggregate = {
  metric: string;
  unit: string;
  determinism: Determinism;
  samples: number[];
  median: number;
  minimum: number;
  maximum: number;
  spread: number;
  stddev: number;
  mean: number;
  count: number;
  stable: boolean;
};

export type VariantStatistics = {
  variant_id: string;
  value: number;
  runs: number;
  contended_runs: number;
  aggregates: Record<string, Aggregate>;
};

export type ExperimentStatistics = {
  baseline_value: number;
  total_runs: number;
  contended_runs: number;
  variants: VariantStatistics[];
  comparisons: MetricComparison[];
};

export type ClaimKind = "measured" | "documented" | "inferred";

export type Claim = { kind: ClaimKind; statement: string; support: string };

export type Explanation = { answer: string; claims: Claim[]; caveats: string[] };

export type UnsupportedClaim = { statement: string; kind: ClaimKind; problem: string };

export type VerifiedExplanation = {
  explanation: Explanation;
  unsupported: UnsupportedClaim[];
  checked_claims: number;
  grounded: boolean;
};

export type ResourceProfile = {
  master: string;
  driver_memory: string;
  container_memory: string | null;
  host_cpus: number | null;
  concurrency: number;
};

export type ReproducibilityRecord = {
  repo_url: string;
  commit_sha: string;
  image: string;
  checkpoint_id: string | null;
  application_version: string | null;
  dataset: DatasetSpec;
  command: string;
  base_configuration: Record<string, string>;
  resources: ResourceProfile;
  repeats: number;
  added_steps: string[];
  reproducible: boolean;
  started_at: string | null;
  finished_at: string | null;
};

// -- Ask RepoLab: triage and answers ----------------------------------------

export type QuestionKind = "conceptual" | "repository" | "testable";

export type Triage = {
  kind: QuestionKind;
  reason: string;
  /** `rules`, `model` or `fallback`: which step settled the kind. */
  decided_by: string;
  route: "answer" | "experiment";
  backend: "spark" | "cli" | null;
  /** Why a testable question is being answered instead of tested. */
  downgraded: string | null;
  /** How the answer is laid out, when the phrasing settled it. */
  shape?: ExplanationShape | null;
};

/** How an answer is laid out. Presentation only; `QuestionKind` routes. */
export type ExplanationShape =
  | "flow"
  | "execution"
  | "definition"
  | "why"
  | "comparison"
  | "location"
  | "architecture"
  | "debugging"
  | "general";

export type AnswerClaimKind = "repository" | "documented" | "inferred" | "measured";

export type AnswerClaim = { kind: AnswerClaimKind; statement: string; support: string };

export type VisualNode = {
  id: string;
  label: string;
  kind: string;
  component_id: string | null;
  source: string | null;
};

export type VisualEdge = {
  source: string;
  target: string;
  label: string;
  support: string;
  /** `static` when a file or cited relationship shows it; `inferred` otherwise. */
  provenance: string;
};

export type AnswerVisual = { nodes: VisualNode[]; edges: VisualEdge[] };

export type AnswerStep = { title: string; description: string; sources: string[] };

export type SectionBasis = "evidenced" | "inferred" | "unknown";

export type AnswerSection = {
  key: string;
  title: string;
  body: string;
  basis: SectionBasis;
  sources: string[];
};

export type ComparisonRow = {
  attribute: string;
  values: string[];
  support: string;
  /** `static`, `inferred`, or `unknown` when the evidence did not establish it. */
  provenance: string;
};

export type AnswerLocation = { role: string; path: string; line: number | null; why: string };

export type GroundedAnswer = {
  answer: string;
  claims: AnswerClaim[];
  caveats: string[];
  /** The fields below were added later; answers stored before them lack them. */
  summary?: string;
  shape?: ExplanationShape;
  visual?: AnswerVisual | null;
  steps?: AnswerStep[];
  sections?: AnswerSection[];
  comparison?: { subjects: string[]; rows: ComparisonRow[] } | null;
  locations?: AnswerLocation[];
  follow_ups?: string[];
};

export type VerifiedAnswer = {
  answer: GroundedAnswer;
  unsupported: { statement: string; kind: AnswerClaimKind; problem: string }[];
  checked_claims: number;
  grounded: boolean;
  /** Relationships the model drew that the evidence did not show. Not drawn. */
  not_established?: string[];
  /** What verification removed from the layout, and why. */
  dropped?: Record<string, number>;
};

export type AnswerResult = {
  question: string;
  kind: QuestionKind;
  shape?: ExplanationShape;
  answer: VerifiedAnswer | null;
  evidence: EvidenceBundle;
  findings: string[];
  downgraded: string | null;
  error: string | null;
  follow_ups?: string[];
};

// -- the CLI experiment -------------------------------------------------------

export type CliVariantSpec = { label: string; statements: string[] };

export type CliExperimentPlan = {
  kind: "cli";
  question: string;
  hypothesis: string;
  setup: string[];
  variants: CliVariantSpec[];
  repeats: number;
  environment: Record<string, string>;
  fixed_settings: Record<string, string>;
  estimated_runs: number;
  estimated_statements: number;
  estimated_seconds: number;
  estimated_minutes: number;
  concurrency: number;
  adjustments: string[];
  labels: string[];
};

export type Spread = {
  samples: number[];
  median: number;
  minimum: number;
  maximum: number;
  spread: number;
  stddev: number;
  mean: number;
  count: number;
};

export type CliStatementStatistics = {
  index: number;
  statement: string;
  runs: number;
  succeeded_runs: number;
  duration_ms: Spread;
  rows: (number | null)[];
  rows_median: number | null;
  rows_stable: boolean;
  error_classes: string[];
  error_message: string | null;
};

export type CliVariantStatistics = {
  variant_id: string;
  label: string;
  runs: number;
  failed_statements: number;
  total_duration_ms: Spread;
  statements: CliStatementStatistics[];
};

export type CliStatistics = {
  variants: CliVariantStatistics[];
  metrics: string[];
  setup_failures: number;
};

export type CliReproducibilityRecord = {
  repo_url: string;
  commit_sha: string;
  build_kind: string;
  fidelity: string;
  application_version: string | null;
  checkpoint_tag: string | null;
  cli_command: string | null;
  memory: string | null;
  repeats: number;
  isolation: string;
  reproducible: boolean;
};

// -- one question, whatever it turned into -----------------------------------

type ExperimentStateCommon = {
  experiment_id: string;
  job_id: string | null;
  status: ExperimentStatus;
  question: string;
  repo_url: string;
  commit_sha: string;
  awaiting_approval: boolean;
  triage: Triage | null;
  answer: AnswerResult | null;
  /** Set for answers and CLI experiments; a Spark experiment serves it with its explanation. */
  evidence: EvidenceBundle | null;
  rejection: { reason: string; detail: string } | null;
  variant_runs: number;
  measurements: number;
  explanation_ready: boolean;
  explanation_error: string | null;
  error: string | null;
};

/** A Spark experiment -- or a question not yet triaged, or answered, with no plan. */
export type SparkExperimentState = ExperimentStateCommon & {
  kind: "spark" | "pending" | "answer";
  plan: ExperimentPlan | null;
  statistics: ExperimentStatistics | null;
  reproducibility: ReproducibilityRecord | null;
};

export type CliExperimentState = ExperimentStateCommon & {
  kind: "cli";
  plan: CliExperimentPlan | null;
  statistics: CliStatistics | null;
  reproducibility: CliReproducibilityRecord | null;
};

export type ExperimentState = SparkExperimentState | CliExperimentState;

export type ExperimentCreated = {
  experiment_id: string;
  job_id: string;
  status: ExperimentStatus;
  question: string;
  events_url: string;
  approve_url: string;
  reject_url: string;
};

export type ApprovalResult = {
  experiment_id: string;
  status: ExperimentStatus;
  job_id: string | null;
  events_url: string | null;
  message: string;
};

export type ExplanationView = {
  experiment_id: string;
  kind: string;
  question: string;
  explanation: VerifiedExplanation;
  statistics: ExperimentStatistics | CliStatistics | null;
  evidence: EvidenceBundle | null;
  reproducibility: ReproducibilityRecord | null;
};

// -- the CLI experience -----------------------------------------------------

export type SessionStatus = "starting" | "ready" | "failed" | "stopped";

export type CLIExperience = {
  repo_url: string;
  commit_sha: string;
  name: string;
  description: string;
  surface_confidence: number;
  reason: string;
  cli_command: string | null;
  /** What the terminal shows before a statement. Cosmetic; output is never faked. */
  prompt: string;
  fidelity: string;
  simulated: boolean;
  version: string;
  build_source: string;
  capabilities: string[];
  limitations: string[];
  /** Disclosed departures from the requested commit, shown verbatim. */
  deviations: string[];
  evidence: CodeReference[];
  available: boolean;
  unavailable_reason: string;
  real_experiments: boolean;
};

export type EnvironmentResolving = {
  job_id: string;
  status: JobStatus;
  repo_url: string;
  events_url: string;
  environment_url: string;
};

export type EnvironmentView = {
  repo_url: string;
  commit_sha: string;
  experience: CLIExperience | null;
  ready: boolean;
  /** Why there is no experience yet, when there is none. */
  reason: string;
  better_available: boolean;
};

/** One statement and exactly what came back. `refused` means nothing ran. */
export type CommandRecord = {
  sequence: number;
  command: string;
  stdout: string;
  stderr: string;
  exit_code: number | null;
  refused: string | null;
  duration_seconds: number;
  at: string;
  ok: boolean;
  output: string;
};

export type UpgradeOffer = {
  from_fidelity: string;
  to_fidelity: string;
  describe: string;
  pending: boolean;
  fidelity: string;
  source: string;
  version: string | null;
};

export type SessionView = {
  session_id: string;
  status: SessionStatus;
  repo_url: string;
  job_id: string | null;
  error: string | null;
  fidelity: string;
  simulated: boolean;
  cli_command: string | null;
  version: string;
  build_kind: string;
  busy: boolean;
  upgrade: UpgradeOffer | null;
  /** What switching environments costs a learner. Shown wherever one is offered. */
  upgrade_disclosure: string;
  history: CommandRecord[];
  terminal_url: string;
};

// -- lessons ----------------------------------------------------------------

export type CheckKind = "output_contains" | "output_matches" | "succeeds" | "fails_with";

export type StepCheck = {
  kind: CheckKind;
  expected: string;
  /** How the check reads to a learner. */
  describe: string;
};

export type LessonStep = {
  number: number;
  concept: string;
  goal: string;
  context: string;
  /** The exact statement, so the terminal can offer it. */
  action: string;
  expected_observation: string;
  validation: StepCheck;
  hint: string;
  code_references: CodeReference[];
  documentation_references: DocumentationReference[];
  grounded: boolean;
};

export type Lesson = {
  id: string;
  repo_url: string;
  commit_sha: string;
  title: string;
  summary: string;
  steps: LessonStep[];
  /** `super` when a model wrote it, `deterministic` when RepoLab did. */
  generated_by: string;
  fidelity: string;
  simulated: boolean;
  step_count: number;
  created_at: string;
};

/** One step of a Mini Lab rendering. Nothing here was executed. */
export type SimulatedStep = {
  number: number;
  concept: string;
  goal: string;
  context: string;
  /** What the learner would type. Shown, never executed. */
  action: string;
  /** What it would conceptually do, or why nothing can be said about it. */
  explanation: string;
  covered: boolean;
  code_references: CodeReference[];
  label: string;
  requires_real_build: boolean;
};

export type SimulatedLesson = {
  repo_url: string;
  commit_sha: string;
  title: string;
  summary: string;
  notice: string;
  steps: SimulatedStep[];
  lesson: Lesson;
  simulated: boolean;
  label: string;
  covered_count: number;
};

export type LessonView = {
  lesson: Lesson;
  /** False when this is RepoLab's own lesson, served because none was generated. */
  stored: boolean;
  explainable_steps: number[];
  /** Present instead of a terminal when a Mini Lab is all that could be built. */
  simulated: SimulatedLesson | null;
};

export type StepResult = {
  step: number;
  passed: boolean;
  /** `deterministic` or `nano`. A learner can tell a check from an opinion. */
  decided_by: string;
  conclusive: boolean;
  reason: string;
  command: string;
  output: string;
  completed: number[];
  lesson_complete: boolean;
  next_step: number | null;
  hint: string;
};

export type StepAttempt = {
  step: number;
  command: string;
  output: string;
  passed: boolean;
  decided_by: string;
  reason: string;
  at: string;
};

export type LessonProgress = {
  lesson_id: string;
  session_id: string | null;
  attempts: StepAttempt[];
  completed: number[];
  completed_count: number;
  started_at: string;
  finished_at: string | null;
};

export type SupportKind = "observed" | "source" | "documented" | "inferred";

export type WhatHappenedClaim = {
  kind: SupportKind;
  statement: string;
  support: string;
};

export type WhatHappenedView = {
  explanation: { summary: string; claims: WhatHappenedClaim[] } | null;
  evidence: EvidenceBundle | null;
  /** Claims deterministic checking could not corroborate. Shown, not hidden. */
  unsupported: { statement: string; kind: SupportKind; problem: string }[];
  grounded: boolean;
  available: boolean;
  reason: string;
};

export type LessonGenerating = {
  job_id: string;
  status: JobStatus;
  repo_url: string;
  events_url: string;
  lesson_url: string;
};

// -- available experiences --------------------------------------------------

export type ExperienceStatus = "runnable" | "detected_only";

/** One interaction surface, and what RepoLab can do about it. */
export type AvailableExperience = {
  surface: string;
  /** CLAUDE.md's own vocabulary: 🌐 >_ 🔌 📓 ⚡ ⌨ */
  icon: string;
  label: string;
  name: string;
  description: string;
  confidence: number;
  primary: boolean;
  /** Why this surface was detected, naming the signals responsible. */
  reason: string;
  evidence: CodeReference[];
  status: ExperienceStatus;
  /** Where it opens, relative to the workspace. `null` unless runnable. */
  route: string | null;
  adapter: string | null;
  /** The specification's sentence plus the specific reason. Empty when runnable. */
  unavailable_reason: string;
  runnable: boolean;
  /** Below the hybrid threshold: real evidence, too thin to call substantial. */
  weak: boolean;
  /** Whether an adapter exists at all, regardless of this repository. */
  supported: boolean;
  /** The confidence in words. */
  strength?: "strong" | "moderate" | "weak";
  /** What this kind of surface is, in plain words. */
  meaning?: string;
  /** Why it was detected, as readable phrases. */
  signals?: string[];
};

export type ExperienceCatalog = {
  repo_url: string;
  commit_sha: string;
  experiences: AvailableExperience[];
  hybrid: boolean;
  primary_type: string | null;
  rationale: string;
  surface_count: number;
  runnable_count: number;
  /** The surfaces that can actually be opened. What the tabs are gated on. */
  runnable_surfaces: string[];
};

// -- the KPI dashboard ------------------------------------------------------

export type MetricStatus = "measured" | "no_data" | "not_instrumented" | "north_star";

/** One number, or an honest account of why there is none. */
export type Metric = {
  key: string;
  label: string;
  /** `null` for anything not measured. The status says why. */
  value: number | null;
  unit: string;
  status: MetricStatus;
  /** How it was computed, in one sentence. Shown beside the number. */
  definition: string;
  /** The tables or events behind it. */
  source: string;
  /** Anything a reader needs in order not to over-read the number. */
  detail: string;
  /** How many observations the value rests on. */
  samples: number | null;
};

export type MetricGroup = {
  title: string;
  subtitle: string;
  metrics: Metric[];
};

export type DatasetProvenance = {
  repositories: number;
  jobs: number;
  analytics_events: number;
  first_event: string | null;
  last_event: string | null;
  days: number;
  /** Whose activity these numbers describe, in RepoLab's own words. */
  summary: string;
};

export type Dashboard = {
  generated_at: string;
  provenance: DatasetProvenance;
  learning: MetricGroup[];
  operations: MetricGroup[];
  north_star: Metric;
  metric_count: number;
  measured_count: number;
};

// -- live experiences: running a repository's web and API services -------------

export type RuntimeStatus =
  | "planning"
  | "preparing"
  | "installing"
  | "building"
  | "starting"
  | "health_checking"
  | "running"
  | "stopped"
  | "build_failed"
  | "start_failed"
  | "health_check_failed"
  | "dependency_missing"
  | "port_not_found"
  | "unsupported"
  | "timeout";

export type DependencyState = "provisionable" | "external_required" | "unsupported";

export type RuntimeDependency = {
  kind: "mongodb" | "postgres" | "redis";
  state: DependencyState;
  env_var: string | null;
  reason: string;
  evidence: CodeReference[];
  services: string[];
};

export type EnvVarPlan = {
  name: string;
  kind: "generated" | "secret" | "dependency" | "required";
  value: string | null;
  evidence: CodeReference[];
};

export type RouteRef = { method: string; path: string; file: string; line: number };

export type PlanWarning = { message: string; evidence: CodeReference[] };

export type ServicePlan = {
  name: string;
  surfaces: string[];
  working_directory: string;
  runtime: string;
  framework: string | null;
  image: string;
  image_reason: string;
  package_manager: string;
  install: string;
  build: string | null;
  start: string;
  script: string;
  port: number;
  port_source: string;
  env: EnvVarPlan[];
  health_path: string;
  routes: RouteRef[];
  evidence: CodeReference[];
  inferred_fields: string[];
  warnings: PlanWarning[];
};

export type RuntimePlan = {
  services: ServicePlan[];
  dependencies: RuntimeDependency[];
  unplanned: Record<string, string>;
  adapter_version: string;
};

export type RuntimeStep = {
  name: string;
  status: "done" | "failed" | "skipped";
  detail: string;
  command: string | null;
  seconds: number;
  log_tail: string;
};

export type RuntimeFailure = {
  status: RuntimeStatus;
  reason: string;
  evidence: CodeReference[];
  next_step: string | null;
  log_tail: string;
};

export type ServiceRuntime = {
  name: string;
  surfaces: string[];
  status: RuntimeStatus;
  steps: RuntimeStep[];
  port: number | null;
  port_source: string | null;
  host_port: number | null;
  health: { path: string; status_code: number | null; ok: boolean; detail: string } | null;
  failure: RuntimeFailure | null;
  warnings: PlanWarning[];
  log_tail: string;
};

export type RuntimeState = {
  id: string;
  repo_url: string;
  commit_sha: string;
  status: RuntimeStatus;
  services: ServiceRuntime[];
  dependencies: RuntimeDependency[];
};

export type RuntimeView = {
  runtime_id: string | null;
  job_id: string | null;
  events_url: string | null;
  status: RuntimeStatus | null;
  state: RuntimeState | null;
  plan: RuntimePlan | null;
  /** A live origin per service, present only once that service is running. */
  services: { name: string; origin: string | null }[];
};

export type ExplorerResponse = {
  status_code: number;
  headers: [string, string][];
  body: string;
  truncated: boolean;
  binary: boolean;
  elapsed_ms: number;
};

// -- experiences: what can be done with a repository --------------------------

export type ExperienceState = "detected" | "supported" | "runnable" | "running";
export type Reality = "real_repository" | "official_runtime" | "mini_lab";

export type RuntimeOption = {
  strategy: string;
  reality: Reality;
  label: string;
  ready: boolean;
  estimate: string;
  disclosure: string | null;
  evidence: CodeReference[];
};

export type StarterAction = {
  id: string;
  title: string;
  description: string;
  code: string | null;
  source: "readme" | "examples" | "routes" | "repolab";
  evidence: CodeReference[];
};

export type ExperienceDefinition = {
  id: string;
  title: string;
  experience_type: string;
  adapter: string;
  summary: string;
  what_you_do: string;
  what_you_learn: string;
  what_to_notice: string;
  surfaces: string[];
  state: ExperienceState;
  why_not: string | null;
  options: RuntimeOption[];
  starter_actions: StarterAction[];
  evidence: CodeReference[];
  route: string | null;
  rank: number;
  rank_signals: Record<string, number>;
  primary: boolean;
  session_id: string | null;
};

export type ExperienceListing = {
  repo_url: string;
  name: string;
  headline: string;
  experiences: ExperienceDefinition[];
  also: ExperienceDefinition[];
  fallback: ExperienceDefinition | null;
  nothing_runs_because: string[];
  supported_kinds: string[];
};

export type SnippetRunResultView = {
  run_id: string;
  ok: boolean;
  spark_version: string | null;
  columns: string[];
  rows: unknown[][];
  value: unknown;
  error: string | null;
  plan: string | null;
  measurements: {
    stage_count: number;
    task_count: number;
    total_runtime_ms: number | null;
    shuffle_bytes_read: number;
    shuffle_bytes_written: number;
    distribution: { partitions: number; empty_partitions: number };
    tasks: { slowest_ms: number; median_ms: number };
  } | null;
  jobs: number;
  event_log_sha256: string | null;
  dataset_sha256: string;
  wall_clock_seconds: number;
  stdout_tail: string;
  config: { shuffle_partitions: number };
};

export type LibraryRunResultView = {
  ok: boolean;
  exit_code: number;
  stdout: string;
  stderr: string;
  truncated: boolean;
  wall_clock_seconds: number;
};

export type MappedStep = {
  title: string;
  run_evidence: string;
  architecture_id: string | null;
  architecture_label: string | null;
  subsystem_id: string | null;
  source_paths: string[];
};

export type ExperienceRun = {
  id: string;
  status: "running" | "done" | "failed";
  code: string;
  action_id: string | null;
  shuffle_partitions: number | null;
  result: (SnippetRunResultView & LibraryRunResultView) | null;
  mapping: MappedStep[];
  explanation: VerifiedAnswer | null;
  explanation_error: string | null;
  events_job_id: string | null;
};

export type ExperienceSessionView = {
  id: string;
  job_id: string;
  experience_id: string;
  adapter: string;
  strategy: string;
  reality: Reality;
  status: "preparing" | "ready" | "failed" | "stopped";
  events_url: string | null;
  state: {
    runtime: { image: string | null; version: string | null; disclosure: string | null; label: string };
    steps: { name: string; status: string; detail: string }[];
    failure: string | null;
    failure_log: string;
    runs: ExperienceRun[];
    dataset: { name: string; rows: number; columns: [string, string][]; preview: string[][]; sha256: string; generated_by: string } | null;
    minilab: { entries: { command: string; summary: string }[] } | null;
    transcript: { command: string; label: string; output?: string; explanation?: string; source_files?: string[] }[];
  };
  experience: ExperienceDefinition | null;
  /** While preparing: from the preparing worker's heartbeat. */
  health?: TaskHealth | null;
  /** While preparing: the step the worker is on. */
  activity?: TaskActivity | null;
};
