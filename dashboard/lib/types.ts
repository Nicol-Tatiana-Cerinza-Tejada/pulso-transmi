export type AccuracyTimeline = {
  cycle_id: string;
  calculated_at: string;
  accuracy: number | null;
  wape: number | null;
  coverage: number | null;
  predictions_count: number | null;
  actuals_count: number | null;
  accuracy_rolling_24h: number | null;
};

export type AccuracyByStation = {
  cycle_id: string;
  station_id: string;
  calculated_at: string;
  accuracy: number | null;
  wape: number | null;
  coverage: number | null;
  predictions_count: number | null;
  actuals_count: number | null;
  cycle_rank: number;
};

export type AccuracyByHorizon = {
  cycle_id: string;
  station_id: string;
  horizon_minutes: 15 | 30 | 45 | 60;
  accuracy: number | null;
  wape: number | null;
  actuals_count: number | null;
  cycle_rank: number;
};

export type RecentDemand = {
  station_id: string;
  ts: string;
  value: number;
  released_at: string | null;
};

export type PipelineHealth = {
  latest_observation_at: string | null;
  latest_collector_finished_at: string | null;
  latest_collector_status: string | null;
  latest_collector_rows: number | null;
  latest_collector_error: string | null;
  observation_delay_seconds: number | null;
};

export type SnapshotHistory = {
  snapshot_id: string;
  created_at: string;
  sha256: string;
  row_count: number;
  station_count: number;
  data_start: string;
  data_end: string;
  model_version: string | null;
  model_status: string | null;
  validation_metric: number | null;
};

export type RetrainHistory = {
  version: string;
  created_at: string;
  model_status: string;
  validation_metric: number | null;
  data_cutoff: string;
  dataset_snapshot_id: string | null;
  snapshot_sha256: string | null;
  snapshot_row_count: number | null;
  decision: string;
  training_metadata: { significance?: { significant?: boolean; confidence_low?: number; confidence_high?: number; delta_accuracy?: number } } | null;
};

export type Champion = {
  version: string;
  active_since: string;
  validation_metric: number | null;
  git_commit: string | null;
  data_cutoff: string;
  artifact_path: string | null;
};

export type ModelHistory = {
  event_id: number;
  action: string;
  status: string;
  reason: string;
  created_at: string;
  completed_at: string | null;
  version: string;
  validation_metric: number | null;
  git_commit: string | null;
  previous_version: string | null;
  previous_status: string | null;
};

export type DriftSignal = {
  id: number;
  station_id: string | null;
  detected_at: string;
  signal_type: string;
  severity: "low" | "medium" | "high" | "critical" | string;
  score: number | null;
  reference_value: number | null;
  current_value: number | null;
  window_start: string | null;
  window_end: string | null;
  status: string;
};

export type PipelineRun = {
  pipeline: "collector" | "infer" | "evaluate" | string;
  run_id: string;
  started_at: string;
  finished_at: string | null;
  status: string;
  rows_processed: number | null;
  error: string | null;
};

export type LeaderboardSnapshot = {
  window_type: "cumulative" | "rolling_24h" | string;
  display_name: string;
  kind: string | null;
  eligible: boolean | null;
  accuracy: number | null;
  raw_wape: number | null;
  accuracy_at_20: number | null;
  coverage: number | null;
  position: number | null;
  calculated_at: string;
};

export type DashboardData = {
  timeline: AccuracyTimeline[];
  stationAccuracy: AccuracyByStation[];
  horizonAccuracy: AccuracyByHorizon[];
  recentDemand: RecentDemand[];
  pipelineHealth: PipelineHealth[];
  snapshots: SnapshotHistory[];
  retrainHistory: RetrainHistory[];
  champion: Champion[];
  modelHistory: ModelHistory[];
  driftSignals: DriftSignal[];
  pipelineRuns: PipelineRun[];
  leaderboard: LeaderboardSnapshot[];
};
