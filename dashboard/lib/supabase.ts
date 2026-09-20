import { createClient } from "@supabase/supabase-js";
import type {
  AccuracyByStation,
  AccuracyTimeline,
  Champion,
  DriftSignal,
  LeaderboardSnapshot,
  ModelHistory,
  PipelineRun,
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
  const [timeline, stationAccuracy, champion, modelHistory, driftSignals, pipelineRuns, leaderboard] =
    await Promise.all([
      supabase.from("v_accuracy_timeline").select("*").order("calculated_at", { ascending: true }),
      supabase.from("v_accuracy_by_station").select("*").order("cycle_rank", { ascending: true }),
      supabase.from("v_champion_current").select("*").order("active_since", { ascending: false }),
      supabase.from("v_model_history").select("*").order("created_at", { ascending: false }).limit(100),
      supabase.from("v_drift_signals").select("*").order("detected_at", { ascending: false }).limit(100),
      supabase.from("v_pipeline_runs").select("*").order("started_at", { ascending: false }).limit(100),
      supabase.from("v_leaderboard_snapshot").select("*").order("position", { ascending: true }),
    ]);

  const responses = [timeline, stationAccuracy, champion, modelHistory, driftSignals, pipelineRuns, leaderboard];
  const failed = responses.find((response) => response.error);
  if (failed?.error) throw new Error(failed.error.message);

  return {
    timeline: (timeline.data ?? []) as AccuracyTimeline[],
    stationAccuracy: (stationAccuracy.data ?? []) as AccuracyByStation[],
    champion: (champion.data ?? []) as Champion[],
    modelHistory: (modelHistory.data ?? []) as ModelHistory[],
    driftSignals: (driftSignals.data ?? []) as DriftSignal[],
    pipelineRuns: (pipelineRuns.data ?? []) as PipelineRun[],
    leaderboard: (leaderboard.data ?? []) as LeaderboardSnapshot[],
  } satisfies {
    timeline: AccuracyTimeline[];
    stationAccuracy: AccuracyByStation[];
    champion: Champion[];
    modelHistory: ModelHistory[];
    driftSignals: DriftSignal[];
    pipelineRuns: PipelineRun[];
    leaderboard: LeaderboardSnapshot[];
  };
}
