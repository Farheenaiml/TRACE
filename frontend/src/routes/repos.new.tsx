import { useState, useEffect } from "react";
import { createFileRoute, useNavigate, Link } from "@tanstack/react-router";
import { Brain, Loader2, AlertCircle, CheckCircle2, ArrowLeft } from "lucide-react";
import { ThemeToggle } from "@/components/trace/ThemeToggle";
import { toast } from "sonner";

const API_BASE =
  typeof window !== "undefined"
    ? `${window.location.protocol}//${window.location.hostname}:8000`
    : "http://localhost:8000";

type RepoNewSearch = {
  repoUrl?: string | undefined;
};

export const Route = createFileRoute("/repos/new")({
  validateSearch: (search: Record<string, unknown>): RepoNewSearch => {
    const searchUrl = search["repoUrl"];
    return {
      repoUrl: typeof searchUrl === "string" ? searchUrl : undefined,
    };
  },
  head: () => ({
    meta: [
      { title: "Connect Repository — TRACE" },
      { name: "description", content: "Index and connect a new GitHub repository." }
    ],
  }),
  component: ConnectRepo,
});

type IngestStep =
  | "fetching_commits"
  | "fetching_issues"
  | "extracting_rationale"
  | "building_graph"
  | "embedding"
  | "done";

function ConnectRepo() {
  const { repoUrl: searchRepoUrl } = Route.useSearch();
  const [repoUrl, setRepoUrl] = useState("");
  const [validationError, setValidationError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  
  // Ingestion status states
  const [ingestingId, setIngestingId] = useState<string | null>(null);
  const [status, setStatus] = useState<"ingesting" | "ready" | "failed" | null>(null);
  const [currentStep, setCurrentStep] = useState<IngestStep | "not_started">("not_started");
  const [error, setError] = useState<string | null>(null);

  const navigate = useNavigate();

  // Parse owner/repo on client-side for early feedback
  function validateRepoUrl(url: string): string | null {
    let s = url.trim();
    if (!s) return "Repository path/URL cannot be empty.";
    
    // Remove protocol and domain
    s = s.replace(/^(https?:\/\/|git:\/\/|git\+ssh:\/\/|git@)/, "");
    s = s.replace(/^github\.com[:/]/, "");
    s = s.replace(/\.git$/, "");
    s = s.replace(/^\/+|\/+$/g, ""); // strip slashes

    const parts = s.split("/");
    if (parts.length !== 2 || !parts[0] || !parts[1]) {
      return "Format must be owner/repo or a full GitHub repository URL.";
    }
    return null;
  }

  async function startIngestion(url: string) {
    const cleanUrl = url.trim();
    const clientErr = validateRepoUrl(cleanUrl);
    if (clientErr) {
      setValidationError(clientErr);
      return;
    }

    setValidationError(null);
    setSubmitting(true);
    setError(null);
    setStatus("ingesting");
    setCurrentStep("fetching_commits");

    try {
      const res = await fetch(`${API_BASE}/repos/add`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ repoUrl: cleanUrl }),
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || `Server returned HTTP ${res.status}`);
      }

      const data = await res.json();
      setIngestingId(data.repoId);
      setStatus(data.status); // "ingesting" or "ready"
      if (data.status === "ready") {
        setCurrentStep("done");
        toast.success("Repository is already indexed!");
        setTimeout(() => {
          navigate({ to: "/repo/$repoId", params: { repoId: encodeURIComponent(data.repoId) } });
        }, 1000);
      } else {
        toast.success("Indexing initialized. Keep this window open!");
      }
    } catch (err: any) {
      console.error(err);
      setError(err.message || "Failed to initialize connection.");
      setStatus("failed");
    } finally {
      setSubmitting(false);
    }
  }

  // Handle connection submit
  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    startIngestion(repoUrl);
  }

  // Auto-trigger ingestion if search parameter repoUrl is set
  useEffect(() => {
    if (searchRepoUrl && !ingestingId && status === null) {
      setRepoUrl(searchRepoUrl);
      startIngestion(searchRepoUrl);
    }
  }, [searchRepoUrl]);


  // Ingestion status polling
  useEffect(() => {
    if (!ingestingId || status !== "ingesting") return;
    const currentIngestingId = ingestingId as string;

    let timer: NodeJS.Timeout;
    
    async function poll() {
      try {
        const res = await fetch(`${API_BASE}/repos/${currentIngestingId}/ingest-status`);
        if (!res.ok) throw new Error(`HTTP Error ${res.status}`);
        const data = await res.json();
        
        setStatus(data.status);
        setCurrentStep(data.step);
        
        if (data.status === "ready") {
          toast.success("Repository indexing complete!");
          setTimeout(() => {
            navigate({ to: "/repo/$repoId", params: { repoId: encodeURIComponent(currentIngestingId) } });
          }, 1500);
        } else if (data.status === "failed") {
          setError(data.error || "Background ingestion process failed.");
        } else {
          // Keep polling
          timer = setTimeout(poll, 2000);
        }
      } catch (err: any) {
        console.warn("Status polling error:", err);
        // Retry anyway
        timer = setTimeout(poll, 3000);
      }
    }

    timer = setTimeout(poll, 1500);

    return () => clearTimeout(timer);
  }, [ingestingId, status, navigate]);

  // Steps checklist definition
  const stepsList = [
    { key: "fetching_commits", label: "Metadata & Commits Fetching" },
    { key: "fetching_issues", label: "Issues & Pull Requests Fetching" },
    { key: "extracting_rationale", label: "Decision Rationale Extraction" },
    { key: "building_graph", label: "Indexing Neo4j Graph Database" },
    { key: "embedding", label: "Qdrant Semantic Indexing" },
  ];

  function getStepState(stepKey: IngestStep) {
    if (status === "failed") {
      if (currentStep === stepKey) return "failed";
      return "pending";
    }
    if (currentStep === "done" || status === "ready") return "completed";
    
    const stepOrder: IngestStep[] = [
      "fetching_commits",
      "fetching_issues",
      "extracting_rationale",
      "building_graph",
      "embedding",
      "done",
    ];
    
    const currentIndex = stepOrder.indexOf(currentStep as IngestStep);
    const stepIndex = stepOrder.indexOf(stepKey);
    
    if (currentIndex > stepIndex) return "completed";
    if (currentIndex === stepIndex) return "active";
    return "pending";
  }

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-2xl flex-col px-5 py-10 sm:py-16">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border pb-6">
        <div className="flex items-center gap-3">
          <Link
            to="/"
            className="inline-flex size-8 items-center justify-center rounded-lg border border-border bg-card text-muted-foreground hover:bg-accent hover:text-foreground"
            title="Go back"
          >
            <ArrowLeft className="size-4" />
          </Link>
          <div>
            <h1 className="text-xl font-semibold text-foreground">Connect codebase</h1>
            <p className="text-xs text-muted-foreground mt-0.5">Add a new repository to your TRACE dashboard.</p>
          </div>
        </div>
        <ThemeToggle />
      </div>

      <div className="mt-8 flex flex-col gap-6">
        {/* Main form */}
        {!ingestingId && status !== "ingesting" && (
          <div className="surface p-6 flex flex-col gap-4">
            <div>
              <h2 className="text-sm font-semibold text-foreground">Enter Repository Details</h2>
              <p className="text-xs text-muted-foreground mt-1 leading-5">
                Paste a GitHub repository link or use the <code>owner/repo</code> shorthand. TRACE will automatically extract architectural decisions from commit histories, pull requests, issue timelines, discussions, and docs.
              </p>
            </div>

            <form onSubmit={handleSubmit} className="flex flex-col gap-3">
              <div className="flex flex-col gap-1.5">
                <input
                  type="text"
                  value={repoUrl}
                  onChange={(e) => {
                    setRepoUrl(e.target.value);
                    if (validationError) setValidationError(null);
                  }}
                  placeholder="e.g. facebook/react or https://github.com/facebook/react"
                  disabled={submitting}
                  className="rounded-lg border border-input bg-background px-3 py-2 text-sm placeholder:text-muted-foreground focus-visible:border-primary/50 focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none disabled:opacity-60"
                  autoFocus
                />
                {validationError && (
                  <p className="text-xs text-destructive flex items-center gap-1.5 mt-0.5">
                    <AlertCircle className="size-3.5 shrink-0" />
                    {validationError}
                  </p>
                )}
              </div>

              <button
                type="submit"
                disabled={submitting || !repoUrl.trim()}
                className="inline-flex w-full items-center justify-center gap-1.5 rounded-lg bg-primary py-2.5 text-sm font-semibold text-primary-foreground shadow-sm hover:bg-primary/95 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50 cursor-pointer"
              >
                {submitting ? (
                  <>
                    <Loader2 className="size-4 animate-spin" />
                    Connecting to GitHub...
                  </>
                ) : (
                  "Index Repository"
                )}
              </button>
            </form>
          </div>
        )}

        {/* Connection pipeline loading / checklist */}
        {(ingestingId || status === "ingesting" || status === "ready") && (
          <div className="surface p-6 flex flex-col gap-6">
            <div>
              <h2 className="text-sm font-semibold text-foreground">Indexing Pipeline</h2>
              <p className="text-xs text-muted-foreground mt-0.5 font-mono truncate">{ingestingId}</p>
            </div>

            <div className="relative pl-1">
              {/* Connecting line */}
              <div className="absolute left-[11px] top-3 bottom-3 w-0.5 bg-border -translate-x-1/2" />
              
              <ul className="relative flex flex-col gap-5">
                {stepsList.map((step) => {
                  const state = getStepState(step.key as IngestStep);
                  return (
                    <li key={step.key} className="flex items-center gap-3.5 relative">
                      <span className={`relative z-10 flex size-5.5 shrink-0 items-center justify-center rounded-full border bg-card transition-all ${
                        state === "active" ? "border-primary ring-2 ring-primary/20 scale-105" : "border-border"
                      }`}>
                        {state === "completed" && (
                          <CheckCircle2 className="size-4.5 text-emerald-500 fill-emerald-500/10 border-none" />
                        )}
                        {state === "active" && (
                          <Loader2 className="size-3 animate-spin text-primary" />
                        )}
                        {state === "pending" && (
                          <span className="size-1.5 rounded-full bg-muted-foreground/30" />
                        )}
                        {state === "failed" && (
                          <AlertCircle className="size-4 text-destructive animate-bounce" />
                        )}
                      </span>
                      <div className="flex-1 min-w-0">
                        <span
                          className={`block text-xs sm:text-sm font-semibold transition-colors ${
                            state === "active"
                              ? "text-primary"
                              : state === "completed"
                                ? "text-muted-foreground line-through decoration-muted-foreground/30 font-medium"
                                : "text-muted-foreground/70 font-normal"
                          }`}
                        >
                          {step.label}
                        </span>
                        {state === "active" && (
                          <span className="block text-[10px] text-muted-foreground/95 animate-pulse mt-0.5 font-medium">
                            Indexing... this may take a few seconds
                          </span>
                        )}
                      </div>
                    </li>
                  );
                })}
              </ul>
            </div>

            {status === "ingesting" && (
              <div className="rounded-lg border border-primary/20 bg-primary/5 p-4 flex flex-col gap-1">
                <span className="text-[10px] font-bold uppercase tracking-wider text-primary">Status</span>
                <p className="text-xs text-muted-foreground">
                  Ingestion runs in the background. Keep this browser tab open to watch execution steps in real-time.
                </p>
              </div>
            )}

            {status === "ready" && (
              <div className="rounded-lg border border-emerald-500/20 bg-emerald-500/5 p-4 flex flex-col gap-1 text-center animate-bounce">
                <span className="text-[10px] font-bold uppercase tracking-wider text-emerald-600">Complete</span>
                <p className="text-xs text-muted-foreground">
                  Ready! Loading workspace dashboard...
                </p>
              </div>
            )}
          </div>
        )}

        {/* Error state */}
        {status === "failed" && error && (
          <div className="rounded-lg border border-destructive/20 bg-destructive/5 p-5 flex flex-col gap-4">
            <div className="flex items-start gap-3 text-destructive">
              <AlertCircle className="size-5 shrink-0 mt-0.5" />
              <div className="flex-1 min-w-0">
                <h3 className="text-sm font-semibold">Indexing Failed</h3>
                <p className="mt-1.5 font-mono text-xs whitespace-pre-wrap leading-5 text-muted-foreground bg-card p-3 rounded-lg border border-border">
                  {error}
                </p>
              </div>
            </div>
            <button
              onClick={() => {
                setIngestingId(null);
                setStatus(null);
                setError(null);
                setCurrentStep("not_started");
              }}
              type="button"
              className="self-end rounded-lg bg-destructive px-3 py-1.5 text-xs font-semibold text-destructive-foreground shadow-sm hover:bg-destructive/90"
            >
              Retry
            </button>
          </div>
        )}
      </div>
    </main>
  );
}
