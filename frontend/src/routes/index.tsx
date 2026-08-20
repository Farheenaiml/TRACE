import { useState } from "react";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { Brain, Plus, Loader2, AlertCircle } from "lucide-react";
import { ThemeToggle } from "@/components/trace/ThemeToggle";
import { toast } from "sonner";

const API_BASE =
  typeof window !== "undefined"
    ? `${window.location.protocol}//${window.location.hostname}:8000`
    : "http://localhost:8000";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "TRACE — Decision intelligence for your codebase" },
      {
        name: "description",
        content:
          "Ask why past engineering decisions were made and get evidence-grounded answers with citations, related decisions and confidence scoring.",
      },
      { property: "og:title", content: "TRACE — Decision intelligence for your codebase" },
      {
        property: "og:description",
        content: "Evidence-grounded answers about why your codebase looks the way it does.",
      },
    ],
  }),
  component: Landing,
});

function Landing() {
  const [repoUrl, setRepoUrl] = useState("");
  const [validationError, setValidationError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const navigate = useNavigate();

  function validateRepoUrl(url: string): string | null {
    let s = url.trim();
    if (!s) return "Repository path/URL cannot be empty.";
    
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

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const cleanUrl = repoUrl.trim();
    const clientErr = validateRepoUrl(cleanUrl);
    if (clientErr) {
      setValidationError(clientErr);
      return;
    }

    setValidationError(null);
    setSubmitting(true);

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
      const repoId = data.repoId;
      
      if (data.status === "ready") {
        toast.success("Repository is already indexed!");
        navigate({ to: "/repo/$repoId", params: { repoId: encodeURIComponent(repoId) } });
      } else {
        // Redirect to /repos/new with the URL so the checklist/status runs and is displayed.
        navigate({ to: "/repos/new", search: { repoUrl: cleanUrl } });
      }
    } catch (err: any) {
      console.error(err);
      toast.error(err.message || "Failed to initialize connection.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-3xl flex-col px-5 py-14 sm:py-24 animate-in fade-in slide-in-from-bottom-2 duration-300">
      <div className="flex items-start justify-between gap-4">
        <div>
          <span className="inline-flex items-center gap-2 rounded-full border border-border bg-card px-3 py-1 text-xs text-muted-foreground shadow-sm">
            <Brain className="size-3.5 text-primary animate-pulse" aria-hidden />
            Decision intelligence
          </span>
          <h1 className="mt-5 text-4xl font-semibold text-foreground sm:text-5xl tracking-tight bg-gradient-to-r from-foreground to-foreground/80 bg-clip-text text-transparent">TRACE</h1>
          <p className="mt-3 max-w-xl text-base leading-7 text-muted-foreground">
            Your codebase remembers what changed. TRACE remembers <em>why</em> — ask in plain
            language and get answers grounded in commits, pull requests and decision records.
          </p>
        </div>
        <ThemeToggle />
      </div>

      {/* Real-time repo connector card */}
      <div className="mt-10 surface p-6 flex flex-col gap-4 border border-primary/10 bg-card/60 backdrop-blur-md shadow-xl relative overflow-hidden group">
        <div className="absolute top-0 right-0 -mt-4 -mr-4 w-24 h-24 bg-primary/5 rounded-full blur-xl pointer-events-none group-hover:bg-primary/10 transition-colors duration-500" />
        <div>
          <h2 className="text-sm font-semibold text-foreground">Connect a live repository</h2>
          <p className="text-xs text-muted-foreground mt-1 leading-5">
            Paste a GitHub repository link or use the <code>owner/repo</code> shorthand. TRACE will index commits, pull requests, issues, discussions, and wiki documentation in real-time.
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
              className="rounded-lg border border-input bg-background/50 px-3.5 py-2.5 text-sm placeholder:text-muted-foreground focus-visible:border-primary/50 focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none disabled:opacity-60 transition-all font-mono"
            />
            {validationError && (
              <p className="text-xs text-destructive flex items-center gap-1.5 mt-0.5 animate-bounce">
                <AlertCircle className="size-3.5 shrink-0" />
                {validationError}
              </p>
            )}
          </div>

          <button
            type="submit"
            disabled={submitting || !repoUrl.trim()}
            className="inline-flex w-full items-center justify-center gap-1.5 rounded-lg bg-primary py-2.5 text-sm font-semibold text-primary-foreground shadow-sm hover:bg-primary/95 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50 transition-all duration-200 cursor-pointer"
          >
            {submitting ? (
              <>
                <Loader2 className="size-4 animate-spin" />
                Validating Repository...
              </>
            ) : (
              "Connect & Index Codebase"
            )}
          </button>
        </form>
      </div>



      <p className="mt-8 text-xs text-muted-foreground">
        Connect a live repository above to explore TRACE in real-time.
      </p>
    </main>
  );
}

