"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { fetchDashboardData } from "@/lib/supabase";
import type { DashboardData, PipelineRun } from "@/lib/types";

const emptyData: DashboardData = {
  timeline: [],
  stationAccuracy: [],
  champion: [],
  modelHistory: [],
  driftSignals: [],
  pipelineRuns: [],
  leaderboard: [],
};

const fmtDate = (value?: string | null) =>
  value
    ? new Intl.DateTimeFormat("es-CO", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value))
    : "—";

const fmtShortDate = (value: string | number) =>
  new Intl.DateTimeFormat("es-CO", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(
    new Date(value),
  );

const pct = (value?: number | null) => (value === null || value === undefined ? "—" : `${value.toFixed(2)}%`);
const chartPercent = (value: unknown, decimals = 2) => `${Number(value).toFixed(decimals)}%`;
const chartTickPercent = (value: unknown) => `${value}%`;

function EmptyState({ children = "Sin datos aún: el stream se llenará cuando se active la competencia." }) {
  return <div className="rounded-xl border border-dashed border-slate-300 bg-white/60 p-8 text-center text-sm text-slate-500">{children}</div>;
}

function Section({ eyebrow, title, children }: { eyebrow: string; title: string; children: React.ReactNode }) {
  return (
    <section className="mb-8">
      <div className="mb-3">
        <p className="text-xs font-bold uppercase tracking-[0.2em] text-teal">{eyebrow}</p>
        <h2 className="mt-1 text-xl font-bold tracking-tight text-ink">{title}</h2>
      </div>
      {children}
    </section>
  );
}

function MetricCard({ label, value, detail, tone = "dark" }: { label: string; value: string; detail?: string; tone?: "dark" | "teal" | "coral" }) {
  const colors = { dark: "bg-ink text-white", teal: "bg-teal text-white", coral: "bg-coral text-white" };
  return (
    <div className={`rounded-2xl p-5 shadow-soft ${colors[tone]}`}>
      <p className="text-xs font-semibold uppercase tracking-[0.16em] opacity-70">{label}</p>
      <p className="mt-3 text-3xl font-bold tracking-tight">{value}</p>
      {detail && <p className="mt-2 text-sm opacity-75">{detail}</p>}
    </div>
  );
}

function statusClass(status: string) {
  if (["failed", "error"].includes(status)) return "bg-red-100 text-red-700";
  if (["running", "pending"].includes(status)) return "bg-amber-100 text-amber-800";
  return "bg-emerald-100 text-emerald-700";
}

function PipelineSummary({ runs }: { runs: PipelineRun[] }) {
  const latest = ["collector", "infer", "evaluate"].map((pipeline) =>
    runs.filter((run) => run.pipeline === pipeline).sort((a, b) => +new Date(b.started_at) - +new Date(a.started_at))[0],
  );
  if (latest.every((run) => !run)) return <span className="text-sm text-slate-500">Sin ejecuciones</span>;
  return (
    <div className="flex flex-wrap gap-2">
      {latest.map((run, index) =>
        run ? (
          <span key={run.pipeline} className={`rounded-full px-3 py-1 text-xs font-bold ${statusClass(run.status)}`}>
            {run.pipeline}: {run.status}
          </span>
        ) : (
          <span key={index} className="rounded-full bg-slate-100 px-3 py-1 text-xs text-slate-500">{["collector", "infer", "evaluate"][index]}: sin datos</span>
        ),
      )}
    </div>
  );
}

export default function Dashboard() {
  const [data, setData] = useState<DashboardData>(emptyData);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      setData(await fetchDashboardData());
      setError(null);
      setLastUpdated(new Date());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "No fue posible consultar Supabase");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 60_000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const latestTimeline = data.timeline.at(-1);
  const cumulativeBoard = data.leaderboard.filter((row) => row.window_type === "cumulative");
  const bestPosition = cumulativeBoard.filter((row) => row.position !== null).sort((a, b) => (a.position ?? Infinity) - (b.position ?? Infinity))[0];
  const latestChampion = data.champion[0];
  const timelineChart = data.timeline.map((row) => ({ ...row, time: Date.parse(row.calculated_at) }));
  const stationBars = useMemo(() => {
    const latestByStation = new Map<string, (typeof data.stationAccuracy)[number]>();
    for (const row of data.stationAccuracy) {
      const current = latestByStation.get(row.station_id);
      if (!current || row.cycle_rank < current.cycle_rank) latestByStation.set(row.station_id, row);
    }
    return [...latestByStation.values()]
      .map((row) => ({ station_id: row.station_id, accuracy: row.accuracy ?? 0 }))
      .sort((a, b) => a.accuracy - b.accuracy);
  }, [data.stationAccuracy]);

  const championMarkers = data.modelHistory
    .filter((event) => event.status === "succeeded")
    .map((event) => ({ at: event.created_at, version: event.version }));

  if (loading) {
    return <main className="min-h-screen p-6"><div className="mx-auto max-w-7xl animate-pulse"><div className="h-56 rounded-3xl bg-slate-200" /><div className="mt-8 grid gap-4 md:grid-cols-4">{[1, 2, 3, 4].map((item) => <div className="h-32 rounded-2xl bg-slate-200" key={item} />)}</div></div></main>;
  }

  return (
    <main className="min-h-screen px-4 py-6 sm:px-8 lg:px-12">
      <div className="mx-auto max-w-7xl">
        <header className="rounded-3xl bg-ink px-6 py-8 text-white shadow-soft sm:px-10">
          <div className="flex flex-col justify-between gap-6 md:flex-row md:items-end">
            <div>
              <p className="text-sm font-bold uppercase tracking-[0.24em] text-teal-300">Pulso TransMi / observabilidad</p>
              <h1 className="mt-3 max-w-3xl text-4xl font-bold tracking-tight sm:text-5xl">¿Qué tan bien estamos prediciendo la demanda?</h1>
              <p className="mt-4 max-w-2xl text-slate-300">Una lectura operativa del accuracy, el modelo activo, el drift y la salud del pipeline.</p>
            </div>
            <div className="text-left text-sm text-slate-300 md:text-right">
              <p>Actualización automática cada 60 s</p>
              <button onClick={() => void refresh()} disabled={refreshing} className="mt-3 rounded-full bg-white px-4 py-2 font-bold text-ink transition hover:bg-teal-100 disabled:cursor-wait disabled:opacity-60">
                {refreshing ? "Actualizando…" : "Actualizar ahora"}
              </button>
            </div>
          </div>
        </header>

        {error && <div className="mt-5 rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">No se pudo actualizar: {error}. Se muestran los últimos datos disponibles.</div>}

        <section className="-mt-5 grid gap-4 px-2 sm:grid-cols-2 lg:grid-cols-4">
          <MetricCard label="Accuracy actual" value={pct(latestTimeline?.accuracy)} detail={latestTimeline ? `Ciclo ${latestTimeline.cycle_id}` : "Sin ciclos evaluados"} tone="teal" />
          <MetricCard label="Tendencia rolling 24 h" value={pct(latestTimeline?.accuracy_rolling_24h)} detail={latestTimeline ? fmtDate(latestTimeline.calculated_at) : "Esperando observaciones"} />
          <MetricCard label="Leaderboard acumulado" value={bestPosition?.position ? `#${bestPosition.position}` : "—"} detail={bestPosition?.display_name ?? "Sin snapshot"} tone="coral" />
          <div className="rounded-2xl bg-white p-5 shadow-soft"><p className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Estado del pipeline</p><div className="mt-4"><PipelineSummary runs={data.pipelineRuns} /></div><p className="mt-4 text-xs text-slate-400">{lastUpdated ? `Consultado ${fmtDate(lastUpdated.toISOString())}` : "Sin consulta"}</p></div>
        </section>

        <div className="mt-10">
          <Section eyebrow="Pregunta 1 · estabilidad" title="¿La accuracy está cayendo con el tiempo?">
            <div className="rounded-2xl bg-white p-4 shadow-soft sm:p-6">
              {data.timeline.length === 0 ? <EmptyState /> : <div className="h-[330px] w-full"><ResponsiveContainer width="100%" height="100%"><LineChart data={timelineChart} margin={{ top: 16, right: 20, left: 0, bottom: 8 }}><CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" /><XAxis type="number" dataKey="time" domain={["dataMin", "dataMax"]} tickFormatter={fmtShortDate} minTickGap={38} stroke="#64748b" fontSize={11} /><YAxis domain={[0, 100]} tickFormatter={chartTickPercent} stroke="#64748b" fontSize={11} /><Tooltip labelFormatter={(value: unknown) => fmtDate(new Date(Number(value)).toISOString())} formatter={(value: unknown, name: unknown) => [chartPercent(value), String(name) === "accuracy" ? "Accuracy" : "Rolling 24 h"]} /><Legend /><Line type="monotone" dataKey="accuracy" name="Accuracy por ciclo" stroke="#0f766e" strokeWidth={3} dot={{ r: 3 }} /><Line type="monotone" dataKey="accuracy_rolling_24h" name="Rolling 24 h" stroke="#ef6f61" strokeWidth={2} dot={false} />{championMarkers.map((marker) => <ReferenceLine key={`${marker.at}-${marker.version}`} x={Date.parse(marker.at)} stroke="#f59e0b" strokeDasharray="5 5" label={{ value: "champion", position: "insideTop" }} />)}</LineChart></ResponsiveContainer></div>}
            </div>
          </Section>

          <Section eyebrow="Pregunta 2 · estaciones" title="¿Qué estaciones necesitan más atención?">
            <div className="rounded-2xl bg-white p-4 shadow-soft sm:p-6">
              {stationBars.length === 0 ? <EmptyState /> : <div className="h-[380px] w-full"><ResponsiveContainer width="100%" height="100%"><BarChart layout="vertical" data={stationBars} margin={{ top: 8, right: 46, left: 18, bottom: 8 }}><CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#e2e8f0" /><XAxis type="number" domain={[0, 100]} tickFormatter={chartTickPercent} stroke="#64748b" fontSize={11} /><YAxis type="category" dataKey="station_id" width={55} stroke="#64748b" fontSize={11} /><Tooltip formatter={(value: unknown) => [chartPercent(value), "Accuracy"]} /><Bar dataKey="accuracy" name="Accuracy" radius={[0, 8, 8, 0]}>{stationBars.map((station, index) => <Cell key={station.station_id} fill={index < 3 ? "#ef6f61" : "#0f766e"} />)}<LabelList dataKey="accuracy" position="right" formatter={(value: unknown) => chartPercent(value, 1)} fill="#475569" fontSize={11} /></Bar></BarChart></ResponsiveContainer></div>}
              <p className="mt-3 text-xs text-slate-500">Ordenadas de peor a mejor; las primeras barras son las estaciones prioritarias.</p>
            </div>
          </Section>

          <div className="grid gap-8 lg:grid-cols-2">
            <Section eyebrow="Pregunta 3 · modelo" title="¿Qué champion está produciendo las predicciones?"><div className="rounded-2xl bg-white p-6 shadow-soft">{latestChampion ? <dl className="grid gap-4 sm:grid-cols-2">{[["Versión", latestChampion.version], ["Activo desde", fmtDate(latestChampion.active_since)], ["Data cutoff", fmtDate(latestChampion.data_cutoff)], ["Métrica validación", pct(latestChampion.validation_metric)], ["Commit", latestChampion.git_commit?.slice(0, 12) ?? "No registrado"]].map(([label, value]) => <div key={label}><dt className="text-xs font-bold uppercase tracking-wider text-slate-500">{label}</dt><dd className="mt-1 break-all text-sm font-semibold text-ink">{value}</dd></div>)}</dl> : <EmptyState>Sin champion registrado todavía.</EmptyState>}</div></Section>
            <Section eyebrow="Pregunta 4 · drift" title="¿Hay señales abiertas que requieran acción?"><div className="rounded-2xl bg-white p-6 shadow-soft">{data.driftSignals.length === 0 ? <EmptyState>Sin señales abiertas.</EmptyState> : <div className="space-y-3">{data.driftSignals.map((signal) => <div key={signal.id} className="flex items-center justify-between gap-4 rounded-xl border border-slate-100 p-3"><div><p className="font-bold capitalize text-ink">{signal.signal_type.replaceAll("_", " ")}</p><p className="text-xs text-slate-500">{signal.station_id ? `Estación ${signal.station_id} · ` : "Global · "}{fmtDate(signal.detected_at)}</p></div><span className={`rounded-full px-3 py-1 text-xs font-bold uppercase ${signal.severity === "critical" || signal.severity === "high" ? "bg-red-100 text-red-700" : signal.severity === "medium" ? "bg-amber-100 text-amber-800" : "bg-slate-100 text-slate-600"}`}>{signal.severity}</span></div>)}</div>}</div></Section>
          </div>

          <Section eyebrow="Pregunta 5 · operación" title="¿El pipeline está funcionando sin leer logs?"><div className="overflow-hidden rounded-2xl bg-white shadow-soft">{data.pipelineRuns.length === 0 ? <div className="p-6"><EmptyState /></div> : <div className="overflow-x-auto"><table className="w-full min-w-[720px] text-left text-sm"><thead className="bg-slate-50 text-xs uppercase tracking-wider text-slate-500"><tr><th className="px-5 py-4">Pipeline</th><th className="px-5 py-4">Ejecución</th><th className="px-5 py-4">Estado</th><th className="px-5 py-4">Filas</th><th className="px-5 py-4">Inicio</th><th className="px-5 py-4">Error</th></tr></thead><tbody className="divide-y divide-slate-100">{data.pipelineRuns.map((run) => <tr key={`${run.pipeline}-${run.run_id}`}><td className="px-5 py-4 font-bold capitalize">{run.pipeline}</td><td className="px-5 py-4 font-mono text-xs">{run.run_id}</td><td className="px-5 py-4"><span className={`rounded-full px-2.5 py-1 text-xs font-bold ${statusClass(run.status)}`}>{run.status}</span></td><td className="px-5 py-4">{run.rows_processed ?? "—"}</td><td className="px-5 py-4 text-slate-500">{fmtDate(run.started_at)}</td><td className="max-w-xs truncate px-5 py-4 text-red-600">{run.error ?? "—"}</td></tr>)}</tbody></table></div>}</div></Section>
        </div>

        <footer className="border-t border-slate-200 py-6 text-xs text-slate-500">Datos de vistas públicas de Supabase · refresco cada 60 segundos · el dashboard no tiene acceso a tablas operativas ni claves secretas.</footer>
      </div>
    </main>
  );
}
