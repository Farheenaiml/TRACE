import { useState, useEffect } from "react";
import { createFileRoute } from "@tanstack/react-router";
import {
  Activity,
  AlertTriangle,
  Users,
  Clock,
  ShieldAlert,
  TrendingUp,
  TrendingDown,
  Sparkles,
  BarChart2,
  RefreshCcw,
} from "lucide-react";
import { toast } from "sonner";
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from "recharts";
import { Button } from "@/components/ui/button";

export const Route = createFileRoute("/repo/$repoId/health")({
  head: () => ({
    meta: [
      { title: "Project Health Analysis — TRACE" },
      {
        name: "description",
        content:
          "Track repository health, backlog growth, duplicate rates, and contributor activity trends.",
      },
      { property: "og:title", content: "Project Health Analysis — TRACE" },
      { property: "og:description", content: "Repository health analytics and backlog trends." },
    ],
  }),
  component: HealthView,
});

const API_BASE =
  typeof window !== "undefined"
    ? `${window.location.protocol}//${window.location.hostname}:8000`
    : "http://localhost:8000";

interface HealthHistoryPoint {
  timestamp: string;
  open_issue_count: number;
}

interface HealthData {
  repoId: string;
  open_issue_count: number;
  backlog_growth_rate: number;
  duplicate_rate: number;
  active_contributor_count: number;
  security_flag_count: number;
  avg_response_time_hours: number;
  response_time_label: string;
  projected_backlog_next_week: number | null;
  projected_contributor_trend: "growing" | "stable" | "declining" | null;
  forecast_status: string;
  history: HealthHistoryPoint[];
}

interface BriefData {
  generated_at: string;
  summary_text: string;
  raw_stats: {
    open_issue_count_this_week: number | string;
    open_issue_count_last_week: number | string;
    decision_counts: Record<string, number>;
    top_discussed_issues: { title: string; comments: number; url: string }[];
  };
}

const MOCK_HEALTH_FALLBACK: HealthData = {
  repoId: "demo-repo",
  open_issue_count: 14,
  backlog_growth_rate: 5.2,
  duplicate_rate: 18.5,
  active_contributor_count: 8,
  security_flag_count: 2,
  avg_response_time_hours: 12.4,
  response_time_label: "Time to last activity (proxy for maintainer response time)",
  projected_backlog_next_week: 17,
  projected_contributor_trend: "stable",
  forecast_status: "ok",
  history: [
    { timestamp: "2026-08-01T00:00:00Z", open_issue_count: 10 },
    { timestamp: "2026-08-08T00:00:00Z", open_issue_count: 12 },
    { timestamp: "2026-08-15T00:00:00Z", open_issue_count: 13 },
    { timestamp: "2026-08-20T00:00:00Z", open_issue_count: 14 },
  ],
};

function formatPointDate(ts: string) {
  try {
    const d = new Date(ts);
    return `${d.getMonth() + 1}/${d.getDate()}`;
  } catch {
    return ts;
  }
}

function formatBriefDate(iso: string) {
  try {
    return new Date(iso).toLocaleString(undefined, {
      weekday: "short",
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function trendColor(trend: string | null) {
  if (trend === "growing") return "text-success";
  if (trend === "declining") return "text-destructive";
  return "text-muted-foreground";
}

function trendIcon(trend: string | null) {
  if (trend === "growing") return <TrendingUp className="size-4 text-success" />;
  if (trend === "declining") return <TrendingDown className="size-4 text-destructive" />;
  return <BarChart2 className="size-4 text-muted-foreground" />;
}

function HealthView() {
  const { repoId } = Route.useParams();
  const decodedRepoId = decodeURIComponent(repoId);
  const [loading, setLoading] = useState(true);
  const [health, setHealth] = useState<HealthData | null>(null);
  const [brief, setBrief] = useState<BriefData | null>(null);
  const [briefLoading, setBriefLoading] = useState(false);

  useEffect(() => {
    async function loadHealth() {
      setLoading(true);
      try {
        const res = await fetch(`${API_BASE}/health?repoId=${encodeURIComponent(decodedRepoId)}`);
        if (!res.ok) {
          throw new Error(`HTTP ${res.status}`);
        }
        const data: HealthData = await res.json();
        setHealth(data);
      } catch (err) {
        console.warn("Health endpoint fetch failed, showing sample data:", err);
        toast.warning("Backend unavailable — showing sample health data");
        setHealth(MOCK_HEALTH_FALLBACK);
      } finally {
        setLoading(false);
      }
    }
    loadHealth();
  }, [repoId]);

  async function loadBrief() {
    setBriefLoading(true);
    try {
      const res = await fetch(`${API_BASE}/brief?repoId=${encodeURIComponent(decodedRepoId)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: BriefData = await res.json();
      setBrief(data);
    } catch (err: unknown) {
      toast.error("Failed to generate weekly brief", {
        description: err instanceof Error ? err.message : "Unable to load the weekly brief.",
      });
    } finally {
      setBriefLoading(false);
    }
  }

  const formattedChartData =
    health?.history?.map((h) => ({
      date: formatPointDate(h.timestamp),
      open_issues: h.open_issue_count,
    })) || [];

  const forecastReady = health?.forecast_status === "ok";

  return (
    <div className="flex flex-col gap-6">
      {/* Header Banner */}
      <section className="surface p-5 sm:p-6">
        <div className="flex items-center gap-2.5">
          <Activity className="size-5 text-primary" aria-hidden />
          <h1 className="text-lg font-semibold text-foreground">Project Health Analysis</h1>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          Track issue backlog trends, duplicate rates, contributor activity, and security risk
          indicators for {repoId}.
        </p>
      </section>

      {/* Loading Skeleton */}
      {loading && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 animate-pulse">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="surface p-5 h-28 flex flex-col justify-between">
              <div className="h-3.5 w-1/2 rounded bg-muted" />
              <div className="h-7 w-1/3 rounded bg-muted" />
            </div>
          ))}
        </div>
      )}

      {!loading && health && (
        <>
          {/* Metric Stat Cards Grid */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {/* Open Issues Card */}
            <div className="surface p-5 flex flex-col justify-between">
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                  Open Issues
                </span>
                <AlertTriangle className="size-4 text-warning" />
              </div>
              <div className="mt-3 flex items-baseline justify-between">
                <span className="text-2xl font-bold text-foreground">
                  {health.open_issue_count}
                </span>
                {health.backlog_growth_rate !== 0 && (
                  <span
                    className={`inline-flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded-full border ${
                      health.backlog_growth_rate > 0
                        ? "border-warning/35 bg-warning/12 text-warning"
                        : "border-success/35 bg-success/12 text-success"
                    }`}
                  >
                    {health.backlog_growth_rate > 0 ? (
                      <TrendingUp className="size-3" />
                    ) : (
                      <TrendingDown className="size-3" />
                    )}
                    {health.backlog_growth_rate > 0
                      ? `+${health.backlog_growth_rate}%`
                      : `${health.backlog_growth_rate}%`}
                  </span>
                )}
              </div>
              <p className="mt-2 text-[11px] text-muted-foreground">
                Active unresolved issue backlog
              </p>
            </div>

            {/* Duplicate Rate Card */}
            <div className="surface p-5 flex flex-col justify-between">
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                  Duplicate Rate
                </span>
                <Activity className="size-4 text-primary" />
              </div>
              <div className="mt-3 flex items-baseline justify-between">
                <span className="text-2xl font-bold text-foreground">{health.duplicate_rate}%</span>
                {health.security_flag_count > 0 && (
                  <span className="inline-flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded-full border border-destructive/35 bg-destructive/12 text-destructive">
                    <ShieldAlert className="size-3" />
                    {health.security_flag_count} security
                  </span>
                )}
              </div>
              <p className="mt-2 text-[11px] text-muted-foreground">
                Classified duplicate issue ratio
              </p>
            </div>

            {/* Active Contributors Card */}
            <div className="surface p-5 flex flex-col justify-between">
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                  Active Contributors
                </span>
                <Users className="size-4 text-primary" />
              </div>
              <div className="mt-3 flex items-baseline justify-between">
                <span className="text-2xl font-bold text-foreground">
                  {health.active_contributor_count}
                </span>
                <span className="text-xs text-muted-foreground">last 30d</span>
              </div>
              <p className="mt-2 text-[11px] text-muted-foreground">
                Distinct active issue/commit authors
              </p>
            </div>

            {/* Avg Response Time / Activity Window Proxy Card */}
            <div className="surface p-5 flex flex-col justify-between">
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                  Time to Activity
                </span>
                <Clock className="size-4 text-muted-foreground" />
              </div>
              <div className="mt-3 flex items-baseline justify-between">
                <span className="text-2xl font-bold text-foreground">
                  {health.avg_response_time_hours}h
                </span>
                <span className="text-xs text-muted-foreground font-mono">proxy</span>
              </div>
              <p
                className="mt-2 text-[11px] text-muted-foreground truncate"
                title={health.response_time_label}
              >
                Time to last activity window
              </p>
            </div>
          </div>

          {/* Honest Metric Proxy Note Callout Banner */}
          <div className="rounded-lg border border-border bg-secondary/30 p-4 text-xs text-muted-foreground leading-relaxed">
            <p className="font-semibold text-foreground mb-1">Metric Transparency Note</p>
            <p>
              Maintainer response time is computed as <strong>time-to-last-activity</strong>{" "}
              (creation date to last update time) as an honest proxy, because initial maintainer
              comment timestamps are not explicitly provided in the raw GitHub dataset.
            </p>
          </div>

          {/* Backlog Growth Trend Line Chart */}
          <section className="surface p-5 sm:p-6">
            <h2 className="text-sm font-semibold text-foreground mb-4 flex items-center gap-2">
              <TrendingUp className="size-4 text-primary" />
              Open Issue Backlog Trend
            </h2>

            {formattedChartData.length >= 2 ? (
              <div className="h-64 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart
                    data={formattedChartData}
                    margin={{ top: 10, right: 20, left: -20, bottom: 0 }}
                  >
                    <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
                    <XAxis
                      dataKey="date"
                      stroke="currentColor"
                      className="text-[11px] text-muted-foreground"
                    />
                    <YAxis
                      stroke="currentColor"
                      className="text-[11px] text-muted-foreground"
                      allowDecimals={false}
                    />
                    <Tooltip
                      contentStyle={{
                        backgroundColor: "var(--card)",
                        borderColor: "var(--border)",
                        borderRadius: "0.5rem",
                        color: "var(--foreground)",
                        fontSize: "0.75rem",
                      }}
                    />
                    <Line
                      type="monotone"
                      dataKey="open_issues"
                      name="Open Issues"
                      stroke="var(--primary)"
                      strokeWidth={2}
                      dot={{ r: 4, fill: "var(--primary)" }}
                      activeDot={{ r: 6 }}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center py-12 text-center text-xs text-muted-foreground border border-dashed border-border rounded-lg">
                <Activity className="size-6 text-muted-foreground/60 mb-2" />
                <p>Not enough historical snapshots to render a trend line.</p>
                <p className="mt-1 text-[11px] text-muted-foreground/80">
                  Trend lines appear automatically once 2+ background health snapshots are
                  persisted.
                </p>
              </div>
            )}
          </section>

          {/* Forecast Section */}
          <section className="surface p-5 sm:p-6">
            <h2 className="text-sm font-semibold text-foreground mb-1 flex items-center gap-2">
              <BarChart2 className="size-4 text-primary" />
              7-Day Backlog Forecast
              <span className="text-[10px] font-normal text-muted-foreground bg-secondary/60 border border-border px-1.5 py-0.5 rounded ml-1">
                Linear projection · not a guarantee
              </span>
            </h2>
            <p className="text-xs text-muted-foreground mb-4">
              Fitted using ordinary least-squares on open issue history snapshots. Requires ≥ 3 data
              points.
            </p>

            {forecastReady ? (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div className="rounded-lg border border-border bg-secondary/20 p-4 flex flex-col gap-1">
                  <span className="text-[10px] uppercase tracking-wider font-semibold text-muted-foreground">
                    Projected Backlog (next week)
                  </span>
                  <span className="text-3xl font-bold text-foreground mt-1">
                    {health.projected_backlog_next_week ?? "—"}
                    <span className="text-sm font-normal text-muted-foreground ml-1.5">issues</span>
                  </span>
                  <p className="text-[11px] text-muted-foreground mt-1">
                    Based on linear trend over {health.history.length} historical snapshots
                  </p>
                </div>
                <div className="rounded-lg border border-border bg-secondary/20 p-4 flex flex-col gap-1">
                  <span className="text-[10px] uppercase tracking-wider font-semibold text-muted-foreground">
                    Contributor Activity Trend
                  </span>
                  <div
                    className={`flex items-center gap-2 text-2xl font-bold mt-1 capitalize ${trendColor(health.projected_contributor_trend)}`}
                  >
                    {trendIcon(health.projected_contributor_trend)}
                    {health.projected_contributor_trend ?? "—"}
                  </div>
                  <p className="text-[11px] text-muted-foreground mt-1">
                    {health.projected_contributor_trend === "growing"
                      ? "Issue volume trending down — contributor bandwidth likely improving."
                      : health.projected_contributor_trend === "declining"
                        ? "Issue volume trending up — monitor contributor capacity."
                        : "Backlog relatively stable week-over-week."}
                  </p>
                </div>
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center py-10 text-center text-xs text-muted-foreground border border-dashed border-border rounded-lg">
                <BarChart2 className="size-6 text-muted-foreground/60 mb-2" />
                <p className="font-medium">Forecast unavailable</p>
                <p className="mt-1 text-[11px] text-muted-foreground/80">
                  {health.forecast_status === "insufficient_history"
                    ? "Requires at least 3 health snapshots. Keep visiting this page to build history."
                    : "Insufficient variance in historical data for a meaningful projection."}
                </p>
              </div>
            )}
          </section>

          {/* Weekly Brief Section */}
          <section className="surface p-5 sm:p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-sm font-semibold text-foreground flex items-center gap-2">
                <Sparkles className="size-4 text-primary" />
                Maintainer Weekly Brief
              </h2>
              <Button
                size="sm"
                variant="outline"
                onClick={loadBrief}
                disabled={briefLoading}
                className="h-8 text-xs gap-1.5"
              >
                {briefLoading ? (
                  <>
                    <RefreshCcw className="size-3.5 animate-spin" />
                    Generating...
                  </>
                ) : (
                  <>
                    <Sparkles className="size-3.5" />
                    {brief ? "Regenerate Brief" : "Generate Brief"}
                  </>
                )}
              </Button>
            </div>

            {!brief && !briefLoading && (
              <div className="flex flex-col items-center justify-center py-10 text-center text-xs text-muted-foreground border border-dashed border-border rounded-lg">
                <Sparkles className="size-6 text-muted-foreground/60 mb-2" />
                <p className="font-medium">No brief generated yet</p>
                <p className="mt-1 text-[11px] text-muted-foreground/80">
                  Click "Generate Brief" to get a 30-second AI-written summary of this week's
                  repository health.
                </p>
              </div>
            )}

            {briefLoading && (
              <div className="rounded-lg border border-border bg-secondary/20 p-5 animate-pulse space-y-2">
                <div className="h-3.5 w-3/4 rounded bg-muted" />
                <div className="h-3.5 w-full rounded bg-muted" />
                <div className="h-3.5 w-5/6 rounded bg-muted" />
                <div className="h-3.5 w-2/3 rounded bg-muted" />
              </div>
            )}

            {brief && !briefLoading && (
              <div className="rounded-lg border border-border bg-card/60 p-5 space-y-4">
                <p className="text-sm leading-relaxed text-foreground/95 italic">
                  "{brief.summary_text}"
                </p>
                <div className="flex items-center justify-between pt-2 border-t border-border/60">
                  <span className="text-[10px] text-muted-foreground">
                    Generated {formatBriefDate(brief.generated_at)}
                  </span>
                  <div className="flex flex-wrap gap-2">
                    {Object.entries(brief.raw_stats.decision_counts).map(([k, v]) => (
                      <span
                        key={k}
                        className="text-[10px] font-mono px-1.5 py-0.5 rounded border border-border text-muted-foreground"
                      >
                        {v} {k.replace(/_/g, " ")}
                      </span>
                    ))}
                  </div>
                </div>
                {brief.raw_stats.top_discussed_issues.length > 0 && (
                  <div className="pt-1">
                    <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground mb-1.5">
                      Top Discussed Issues
                    </p>
                    <ul className="space-y-1">
                      {brief.raw_stats.top_discussed_issues.map((issue, i) => (
                        <li key={i} className="flex items-center gap-2 text-xs">
                          <span className="w-4 h-4 text-[10px] font-bold rounded-full bg-primary/15 text-primary inline-flex items-center justify-center shrink-0">
                            {i + 1}
                          </span>
                          <a
                            href={issue.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-primary hover:underline truncate"
                          >
                            {issue.title}
                          </a>
                          <span className="shrink-0 text-[10px] text-muted-foreground">
                            ({issue.comments} comments)
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}
