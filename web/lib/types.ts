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
  | "upgrade_applied";

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

export type JobState = {
  job_id: string;
  status: JobStatus;
  repo_url: string;
  attempts: number;
  last_seq: number;
  event_count: number;
  elapsed_seconds: number | null;
  stalled: boolean;
  stall_summary: string;
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

export type CodeReference = {
  path: string;
  start_line: number | null;
  end_line: number | null;
  commit_sha: string | null;
  excerpt: string | null;
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

export type ArchitectureNode = {
  id: string;
  kind: "entrypoint" | "module" | "service" | "config" | "datastore" | "external";
  name: string;
  description: string | null;
  file_references: CodeReference[];
  group: string | null;
};

export type ArchitectureEdge = {
  source: string;
  target: string;
  kind: "depends_on" | "invokes" | "configures" | "reads" | "writes" | "exposes";
  description: string | null;
};

export type ArchitectureView = {
  job_id: string;
  generated_by: string;
  node_count: number;
  edge_count: number;
  summary: string | null;
  graph: { nodes: ArchitectureNode[]; edges: ArchitectureEdge[] };
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
  | "rejected";

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

export type ExperimentState = {
  experiment_id: string;
  job_id: string | null;
  status: ExperimentStatus;
  question: string;
  repo_url: string;
  commit_sha: string;
  awaiting_approval: boolean;
  plan: ExperimentPlan | null;
  rejection: { reason: string; detail: string } | null;
  statistics: ExperimentStatistics | null;
  reproducibility: ReproducibilityRecord | null;
  variant_runs: number;
  measurements: number;
  explanation_ready: boolean;
  explanation_error: string | null;
  error: string | null;
};

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
  question: string;
  explanation: VerifiedExplanation;
  statistics: ExperimentStatistics | null;
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
