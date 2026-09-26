import { createClient } from "@supabase/supabase-js";
import type {
  AccuracyByStation,
  AccuracyByHorizon,
  AccuracyTimeline,
  Champion,
  DriftSignal,
  LeaderboardSnapshot,
  ModelHistory,
  PipelineRun,
  PipelineHealth,
  RecentDemand,
  RetrainHistory,
  SnapshotHistory,
} from "./types";

const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
const publishableKey = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;

if (!url || !publishableKey) {
  throw new Error("Faltan NEXT_PUBLIC_SUPABASE_URL o NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY");
}

// Este módulo solo recibe la publishable key. Nunca usar aquí una service-role
// key: este cliente se empaqueta y se ejecuta en el navegador.
export const supabase = createClient(url, publishableKey, {
  auth: { persistSession: false, autoRefreshToken: false },
});

export async function fetchDashboardData() {
  const [timeline, stationAccuracy, horizonAccuracy, recentDemand, pipelineHealth, snapshots, retrainHistory, champion, modelHistory, driftSignals, pipelineRuns, leaderboard] =
    await Promise.all([
      supabase.from("v_accuracy_timeline").select("*").order("calculated_at", { ascending: true }),
      supabase.from("v_accuracy_by_station").select("*").order("cycle_rank", { ascending: true }),
      supabase.from("v_accuracy_by_horizon").select("*").order("cycle_rank", { ascending: true }),
      supabase.from("v_demand_recent").select("*").order("ts", { ascending: true }),
      supabase.from("v_pipeline_health").select("*"),
      supabase.from("v_snapshot_history").select("*").order("created_at", { ascending: false }).limit(20),
      supabase.from("v_retrain_history").select("*").order("created_at", { ascending: false }).limit(20),
      supabase.from("v_champion_current").select("*").order("active_since", { ascending: false }),
      supabase.from("v_model_history").select("*").order("created_at", { ascending: false }).limit(100),
      supabase.from("v_drift_signals").select("*").order("detected_at", { ascending: false }).limit(100),
      supabase.from("v_pipeline_runs").select("*").order("started_at", { ascending: false }).limit(100),
      supabase.from("v_leaderboard_snapshot").select("*").order("position", { ascending: true }),
    ]);

  const responses = [timeline, stationAccuracy, horizonAccuracy, recentDemand, pipelineHealth, snapshots, retrainHistory, champion, modelHistory, driftSignals, pipelineRuns, leaderboard];
  const failed = responses.find((response) => response.error);
  if (failed?.error) throw new Error(failed.error.message);

  return {
    timeline: (timeline.data ?? []) as AccuracyTimeline[],
    stationAccuracy: (stationAccuracy.data ?? []) as AccuracyByStation[],
    horizonAccuracy: (horizonAccuracy.data ?? []) as AccuracyByHorizon[],
    recentDemand: (recentDemand.data ?? []) as RecentDemand[],
    pipelineHealth: (pipelineHealth.data ?? []) as PipelineHealth[],
    snapshots: (snapshots.data ?? []) as SnapshotHistory[],
    retrainHistory: (retrainHistory.data ?? []) as RetrainHistory[],
    champion: (champion.data ?? []) as Champion[],
    modelHistory: (modelHistory.data ?? []) as ModelHistory[],
    driftSignals: (driftSignals.data ?? []) as DriftSignal[],
    pipelineRuns: (pipelineRuns.data ?? []) as PipelineRun[],
    leaderboard: (leaderboard.data ?? []) as LeaderboardSnapshot[],
  } satisfies {
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
}
