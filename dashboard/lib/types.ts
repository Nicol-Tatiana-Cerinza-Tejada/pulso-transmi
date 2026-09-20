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
  champion: Champion[];
  modelHistory: ModelHistory[];
  driftSignals: DriftSignal[];
  pipelineRuns: PipelineRun[];
  leaderboard: LeaderboardSnapshot[];
};
