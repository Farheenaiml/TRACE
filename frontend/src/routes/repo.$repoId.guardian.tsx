import { useState, useEffect } from "react";
import { createFileRoute } from "@tanstack/react-router";
import {
  Shield,
  Play,
  Lock,
  ExternalLink,
  Loader2,
  Check,
  AlertTriangle,
  AlertCircle,
  ThumbsUp,
  ThumbsDown,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";

export const Route = createFileRoute("/repo/$repoId/guardian")({
  head: () => ({
    meta: [
      { title: "RepoGuardian Triage — RepoMind" },
      {
        name: "description",
        content:
          "Automated triage scanning, duplicate issue checking, and AI-powered escalation scoring.",
      },
      { property: "og:title", content: "RepoGuardian Triage — RepoMind" },
      {
        property: "og:description",
        content: "AI-driven triage scanning and GitHub issue prioritization.",
      },
    ],
  }),
  component: GuardianView,
});

const API_BASE =
  typeof window !== "undefined"
    ? `${window.location.protocol}//${window.location.hostname}:8000`
    : "http://localhost:8000";

interface Duplicate {
  id: number;
  title: string;
  url: string;
  similarity: number;
}

interface ScanResult {
  issue_id: string;
  title: string;
  url: string;
  decision: "escalate" | "needs_more_info" | "duplicate" | "low_priority";
  reason: string;
  security_sensitive: boolean;
  duplicates: Duplicate[];
  has_corrected_duplicate?: boolean;
}

interface ScanResponse {
  repoId: string;
  last_run?: string | null;
  scanned: number;
  results: ScanResult[];
}

function formatLastRun(isoString: string | null) {
  if (!isoString) return null;
  try {
    const date = new Date(isoString);
    const now = new Date();
    const diffSeconds = Math.floor((now.getTime() - date.getTime()) / 1000);
    if (diffSeconds < 60) return "just now";
    if (diffSeconds < 3600) return `${Math.floor(diffSeconds / 60)}m ago`;
    if (diffSeconds < 86400) return `${Math.floor(diffSeconds / 3600)}h ago`;
    return date.toLocaleDateString();
  } catch {
    return isoString;
  }
}

function getDecisionBadge(decision: string) {
  switch (decision) {
    case "escalate":
      return "border-destructive/35 bg-destructive/12 text-destructive";
    case "needs_more_info":
      return "border-warning/40 bg-warning/15 text-warning";
    case "duplicate":
      return "border-border bg-secondary text-muted-foreground";
    case "low_priority":
      return "border-primary/30 bg-primary/10 text-primary";
    default:
      return "border-border bg-secondary text-muted-foreground";
  }
}

function formatDecision(decision: string) {
  return decision
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

function GuardianView() {
  const { repoId } = Route.useParams();
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [statusText, setStatusText] = useState("");
  const [results, setResults] = useState<ScanResult[] | null>(null);
  const [lastRun, setLastRun] = useState<string | null>(null);

  // Track commenting state per issue (issue_id -> status)
  const [commentStates, setCommentStates] = useState<
    Record<string, "idle" | "posting" | "success" | "error">
  >({});
  const [commentErrors, setCommentErrors] = useState<Record<string, string>>({});

  const [activeCorrectionId, setActiveCorrectionId] = useState<string | null>(null);
  const [feedbackStates, setFeedbackStates] = useState<
    Record<string, { submitted: boolean; correct: boolean; correctedDecision?: string }>
  >({});

  async function handleFeedback(
    issueId: string,
    originalDecision: string,
    correct: boolean,
    correctedDecision?: string,
  ) {
    try {
      const response = await fetch(`${API_BASE}/agent/feedback`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          repoId,
          issueId,
          decision: originalDecision,
          correct,
          correctedDecision: correctedDecision || null,
        }),
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      setFeedbackStates((prev) => ({
        ...prev,
        [issueId]: {
          submitted: true,
          correct,
          correctedDecision,
        },
      }));

      toast.success(
        correct
          ? "Feedback submitted: Confirmed!"
          : `Feedback submitted: Corrected to ${formatDecision(correctedDecision || "")}`,
      );
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Is backend server running?";
      console.error("Feedback submission failed:", err);
      toast.error("Failed to submit feedback", {
        description: message,
      });
    }
  }

  // Auto-fetch cached scan status on page load
  useEffect(() => {
    async function fetchStatus() {
      try {
        const response = await fetch(
          `${API_BASE}/agent/status?repoId=${encodeURIComponent(repoId)}`,
        );
        if (response.ok) {
          const data: ScanResponse = await response.json();
          if (data.results && data.results.length > 0) {
            setResults(data.results);
          }
          if (data.last_run) {
            setLastRun(data.last_run);
          }
        }
      } catch (err) {
        console.warn("Failed to load initial agent scan status:", err);
      }
    }
    fetchStatus();
  }, [repoId]);

  // Simulate progress steps
  useEffect(() => {
    let interval: NodeJS.Timeout;
    if (loading) {
      setProgress(0);
      setStatusText("Initializing triage scan...");
      interval = setInterval(() => {
        setProgress((prev) => {
          if (prev < 25) return prev + 6;
          if (prev < 55) return prev + 4;
          if (prev < 85) return prev + 1.5;
          if (prev < 96) return prev + 0.5;
          return prev;
        });
      }, 400);
    } else {
      setProgress(0);
    }
    return () => clearInterval(interval);
  }, [loading]);

  useEffect(() => {
    if (!loading) return;
    if (progress < 15) {
      setStatusText("Connecting to repository issues database...");
    } else if (progress < 40) {
      setStatusText("Loading SentenceTransformer model & processing embeddings...");
    } else if (progress < 70) {
      setStatusText("Querying Qdrant vector database for duplicate reports...");
    } else if (progress < 90) {
      setStatusText("Running LLM triage scoring & analyzing context...");
    } else {
      setStatusText("Formatting triage explanations & flags...");
    }
  }, [progress, loading]);

  async function handleScan() {
    setLoading(true);
    setResults(null);
    setCommentStates({});
    setCommentErrors({});

    try {
      const response = await fetch(`${API_BASE}/agent/scan?repoId=${encodeURIComponent(repoId)}`, {
        method: "POST",
      });

      if (!response.ok) {
        let errText = "Failed to run scan.";
        try {
          const errData = await response.json();
          errText = errData.detail || errText;
        } catch {
          // ignore
        }
        throw new Error(errText);
      }

      const data: ScanResponse = await response.json();
      setProgress(100);
      // Short delay so the user feels the 100% completion state
      setTimeout(() => {
        setResults(data.results);
        if (data.last_run) {
          setLastRun(data.last_run);
        } else {
          setLastRun(new Date().toISOString());
        }
        setLoading(false);
      }, 400);
      toast.success("Scan completed successfully!", {
        description: `Investigated ${data.scanned} issues.`,
      });
    } catch (err: unknown) {
      const message =
        err instanceof Error
          ? err.message
          : "Please make sure backend server is running and data is ingested.";
      console.error(err);
      toast.error("Triage scan failed", {
        description: message,
      });
      setLoading(false);
    }
  }

  async function handleComment(item: ScanResult) {
    const issueId = item.issue_id;
    setCommentStates((prev) => ({ ...prev, [issueId]: "posting" }));
    setCommentErrors((prev) => ({ ...prev, [issueId]: "" }));

    const message = `**[RepoGuardian Triage Action: ${item.decision.replace(/_/g, " ").toUpperCase()}]**\n\n${item.reason}`;

    try {
      const response = await fetch(`${API_BASE}/agent/comment`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          repoId,
          issueNumber: issueId,
          message,
        }),
      });

      if (!response.ok) {
        let errText = "Failed to post comment.";
        try {
          const errData = await response.json();
          errText = errData.detail || errText;
        } catch {
          // ignore
        }
        throw new Error(errText);
      }

      setCommentStates((prev) => ({ ...prev, [issueId]: "success" }));
      toast.success("GitHub comment posted!", {
        description: `Successfully commented on issue #${issueId}.`,
      });
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Verify GITHUB_TOKEN has write access.";
      console.error(err);
      setCommentStates((prev) => ({ ...prev, [issueId]: "error" }));
      setCommentErrors((prev) => ({ ...prev, [issueId]: message }));
      toast.error("Failed to post comment", {
        description: message,
      });
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <section className="surface p-5 sm:p-6">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2.5">
              <Shield className="size-5 text-primary" aria-hidden />
              <h1 className="text-lg font-semibold text-foreground">RepoGuardian</h1>
            </div>
            <p className="mt-1 text-sm text-muted-foreground flex flex-wrap items-center gap-x-2 gap-y-1">
              <span>
                Run automated triaging across open issues to find duplicates, flag security risks,
                and score escalation priority.
              </span>
              {lastRun && (
                <span className="inline-flex items-center gap-1 text-xs text-primary font-medium border border-primary/20 bg-primary/10 px-2 py-0.5 rounded-full">
                  Last scanned {formatLastRun(lastRun)}
                </span>
              )}
            </p>
          </div>
          <div>
            <Button onClick={handleScan} disabled={loading} className="w-full md:w-auto">
              {loading ? (
                <>
                  <Loader2 className="mr-2 size-4 animate-spin" />
                  Scanning...
                </>
              ) : (
                <>
                  <Play className="mr-2 size-3.5 fill-current" />
                  {results ? "Run scan again" : "Scan for issues"}
                </>
              )}
            </Button>
          </div>
        </div>

        {/* Progress Indicator */}
        {loading && (
          <div className="mt-6 rounded-lg border border-border bg-secondary/30 p-4 animate-in fade-in slide-in-from-bottom-2 duration-300">
            <div className="flex items-center justify-between text-sm font-medium mb-2">
              <span className="text-foreground">{statusText}</span>
              <span className="font-mono text-xs text-muted-foreground">
                {Math.round(progress)}%
              </span>
            </div>
            <div className="w-full bg-secondary h-2.5 rounded-full overflow-hidden">
              <div
                className="bg-primary h-full rounded-full transition-all duration-300 ease-out"
                style={{ width: `${progress}%` }}
              />
            </div>
            <p className="mt-2 text-xs text-muted-foreground/80">
              Please keep this tab active. RepoGuardian runs duplicate vector matching and LLM-based
              triage scoring per issue.
            </p>
          </div>
        )}
      </section>

      {/* Results State */}
      {!loading && results && (
        <section className="surface overflow-hidden">
          <header className="flex items-center justify-between border-b border-border px-5 py-4 bg-muted/20">
            <h2 className="text-sm font-semibold text-foreground">
              Scanned GitHub Issues &amp; Triage Decisions
            </h2>
            <span className="text-xs text-muted-foreground">
              {results.length} issues investigated
            </span>
          </header>
          {results.length === 0 ? (
            <div className="flex flex-col items-center px-6 py-12 text-center">
              <AlertCircle className="size-8 text-muted-foreground" />
              <p className="mt-3 text-sm text-muted-foreground">No open issues found to scan.</p>
            </div>
          ) : (
            <ul className="divide-y divide-border">
              {results.map((item) => {
                const commentState = commentStates[item.issue_id] || "idle";
                const commentError = commentErrors[item.issue_id];
                const showCommentBtn =
                  item.decision === "escalate" || item.decision === "duplicate";

                return (
                  <li
                    key={item.issue_id}
                    className="px-5 py-5 hover:bg-secondary/20 transition-colors"
                  >
                    <div className="flex flex-col md:flex-row md:items-start justify-between gap-4">
                      {/* Main issue details */}
                      <div className="flex-1 min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <a
                            href={item.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-sm font-semibold text-foreground hover:underline hover:text-primary transition-colors inline-flex items-center gap-1"
                          >
                            #{item.issue_id}: {item.title}
                            <ExternalLink className="size-3 opacity-60" />
                          </a>

                          <span
                            className={`shrink-0 rounded-full border px-2 py-0.5 font-semibold text-xs ${getDecisionBadge(item.decision)}`}
                          >
                            {formatDecision(item.decision)}
                          </span>

                          {item.security_sensitive && (
                            <span className="inline-flex items-center gap-1 text-[10px] uppercase tracking-wider font-bold text-red-500 bg-red-500/10 border border-red-500/25 px-1.5 py-0.5 rounded">
                              <Lock className="size-2.5" />
                              Security
                            </span>
                          )}

                          {/* Feedback controls */}
                          {!feedbackStates[item.issue_id]?.submitted ? (
                            <div className="inline-flex items-center gap-1.5 ml-2 text-muted-foreground">
                              <button
                                type="button"
                                onClick={() => handleFeedback(item.issue_id, item.decision, true)}
                                className="hover:text-success transition-colors p-1 rounded hover:bg-secondary/40"
                                title="Correct decision"
                              >
                                <ThumbsUp className="size-3.5" />
                              </button>
                              <button
                                type="button"
                                onClick={() =>
                                  setActiveCorrectionId(
                                    activeCorrectionId === item.issue_id ? null : item.issue_id,
                                  )
                                }
                                className={`hover:text-destructive transition-colors p-1 rounded hover:bg-secondary/40 ${activeCorrectionId === item.issue_id ? "text-destructive bg-secondary/50" : ""}`}
                                title="Incorrect decision"
                              >
                                <ThumbsDown className="size-3.5" />
                              </button>
                            </div>
                          ) : (
                            <span className="text-xs text-muted-foreground italic ml-2 inline-flex items-center gap-1">
                              {feedbackStates[item.issue_id].correct ? (
                                <>
                                  <ThumbsUp className="size-3 text-success fill-current" />
                                  Confirmed
                                </>
                              ) : (
                                <>
                                  <ThumbsDown className="size-3 text-destructive fill-current" />
                                  Corrected to{" "}
                                  {formatDecision(
                                    feedbackStates[item.issue_id].correctedDecision || "",
                                  )}
                                </>
                              )}
                            </span>
                          )}

                          {/* Maintainer-corrected badge */}
                          {item.has_corrected_duplicate && (
                            <span className="inline-flex items-center gap-1 text-[10px] uppercase tracking-wider font-bold text-emerald-500 bg-emerald-500/10 border border-emerald-500/25 px-1.5 py-0.5 rounded animate-pulse">
                              Maintainer Corrected
                            </span>
                          )}
                        </div>

                        {/* Inline Dropdown for Triage Decision Correction */}
                        {activeCorrectionId === item.issue_id && (
                          <div className="mt-2 flex items-center gap-2 text-xs bg-secondary/20 p-2 rounded-lg border border-border max-w-xs animate-in fade-in duration-200">
                            <span className="text-muted-foreground font-medium">Correct to:</span>
                            <select
                              onChange={(e) => {
                                if (e.target.value) {
                                  handleFeedback(
                                    item.issue_id,
                                    item.decision,
                                    false,
                                    e.target.value,
                                  );
                                  setActiveCorrectionId(null);
                                }
                              }}
                              defaultValue=""
                              className="bg-background border border-input rounded px-1.5 py-0.5 text-xs text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                            >
                              <option value="" disabled>
                                Select option...
                              </option>
                              {["escalate", "needs_more_info", "duplicate", "low_priority"].map(
                                (opt) => (
                                  <option key={opt} value={opt}>
                                    {formatDecision(opt)}
                                  </option>
                                ),
                              )}
                            </select>
                            <Button
                              size="sm"
                              variant="ghost"
                              className="h-6 px-1.5 text-[10px]"
                              onClick={() => setActiveCorrectionId(null)}
                            >
                              Cancel
                            </Button>
                          </div>
                        )}

                        <p className="mt-2 text-sm leading-relaxed text-foreground/90">
                          {item.reason}
                        </p>

                        {/* Duplicates block */}
                        {item.duplicates && item.duplicates.length > 0 && (
                          <div className="mt-3.5 rounded-lg border border-border bg-card/60 p-3 max-w-2xl">
                            <p className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider mb-2 flex items-center gap-1.5">
                              <AlertTriangle className="size-3 text-warning" />
                              Detected Duplicate Evidence
                            </p>
                            <ul className="space-y-2">
                              {item.duplicates.map((dup) => (
                                <li
                                  key={dup.id}
                                  className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-1 text-xs"
                                >
                                  <a
                                    href={dup.url}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="text-primary hover:underline font-medium truncate max-w-md inline-flex items-center gap-1"
                                  >
                                    #{dup.id}: {dup.title}
                                    <ExternalLink className="size-2.5 opacity-60" />
                                  </a>
                                  <span className="shrink-0 font-mono text-[10px] border border-success/35 bg-success/12 text-success px-1.5 py-0.2 rounded-full self-start sm:self-auto">
                                    {Math.round(dup.similarity * 100)}% match
                                  </span>
                                </li>
                              ))}
                            </ul>
                          </div>
                        )}

                        {commentError && (
                          <p className="mt-2 text-xs text-destructive font-medium flex items-center gap-1">
                            <AlertCircle className="size-3" />
                            Error commenting: {commentError}
                          </p>
                        )}
                      </div>

                      {/* Action buttons */}
                      {showCommentBtn && (
                        <div className="shrink-0 flex items-center self-start md:self-center">
                          {commentState === "success" ? (
                            <span className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-semibold text-success border border-success/35 bg-success/12 rounded-lg">
                              <Check className="size-3.5" />
                              Commented
                            </span>
                          ) : (
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => handleComment(item)}
                              disabled={commentState === "posting"}
                              className="text-xs h-8 px-3"
                            >
                              {commentState === "posting" ? (
                                <>
                                  <Loader2 className="mr-1.5 size-3 animate-spin" />
                                  Commenting...
                                </>
                              ) : (
                                <>
                                  <MessageSquare className="mr-1.5 size-3" />
                                  Approve &amp; Comment
                                </>
                              )}
                            </Button>
                          )}
                        </div>
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      )}

      {/* Initial/Empty State */}
      {!loading && !results && (
        <div className="surface flex flex-col items-center px-6 py-14 text-center">
          <div className="flex size-11 items-center justify-center rounded-full bg-accent text-accent-foreground mb-4">
            <Shield className="size-5" aria-hidden />
          </div>
          <h2 className="text-base font-semibold text-foreground">No triage results loaded</h2>
          <p className="mt-2 max-w-md text-sm leading-6 text-muted-foreground">
            Click "Scan for issues" to begin the AI-powered triage and duplicate detection pipeline
            for the currently selected repository.
          </p>
        </div>
      )}
    </div>
  );
}
