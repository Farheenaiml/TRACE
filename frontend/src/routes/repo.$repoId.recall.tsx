import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { Search, Inbox } from "lucide-react";
import { Button } from "@/components/ui/button";
import { recallIssue, type RecallMatch } from "@/lib/mock-api";

export const Route = createFileRoute("/repo/$repoId/recall")({
  head: () => ({
    meta: [
      { title: "Issue Recall — TRACE" },
      {
        name: "description",
        content:
          "Paste a new issue and surface similar past issues and decisions before you start work.",
      },
      { property: "og:title", content: "Issue Recall — TRACE" },
      {
        property: "og:description",
        content: "Find past issues and decisions similar to a new report.",
      },
    ],
  }),
  component: RecallView,
});

const API_BASE =
  typeof window !== "undefined"
    ? `${window.location.protocol}//${window.location.hostname}:8000`
    : "http://localhost:8000";

function similarityTone(score: number) {
  if (score >= 0.8) return "border-success/35 bg-success/12 text-success";
  if (score >= 0.6) return "border-warning/40 bg-warning/15 text-warning";
  return "border-border bg-secondary text-muted-foreground";
}

function RecallView() {
  const { repoId } = Route.useParams();
  const decodedRepoId = decodeURIComponent(repoId);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState<RecallMatch[] | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim() || loading) return;
    setLoading(true);
    setResults(null);
    setResults(await recallIssue(decodedRepoId, title, body));
    setLoading(false);
  }

  return (
    <div className="flex flex-col gap-6">
      <section className="surface p-5 sm:p-6">
        <h1 className="text-lg font-semibold text-foreground">Issue recall</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Paste a new issue and RepoMind surfaces past issues and decisions that overlap with it.
        </p>

        <form onSubmit={handleSubmit} className="mt-5 flex flex-col gap-3">
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Issue title — e.g. Users get logged out randomly on Safari"
            aria-label="Issue title"
            className="w-full rounded-lg border border-input bg-background px-3 py-2.5 text-sm placeholder:text-muted-foreground focus-visible:border-primary/50 focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none"
          />
          <textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            rows={5}
            placeholder="Description, reproduction steps, logs…"
            aria-label="Issue description"
            className="w-full resize-y rounded-lg border border-input bg-background px-3 py-2.5 text-sm leading-6 placeholder:text-muted-foreground focus-visible:border-primary/50 focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none"
          />
          <div className="flex justify-end">
            <Button type="submit" disabled={loading || !title.trim()}>
              <Search className="size-3.5 opacity-80" />
              {loading ? "Searching…" : "Find similar"}
            </Button>
          </div>
        </form>
      </section>

      {loading && (
        <div className="surface animate-pulse divide-y divide-border">
          {[0, 1, 2].map((i) => (
            <div key={i} className="space-y-3 p-5">
              <div className="h-3.5 w-1/2 rounded bg-muted" />
              <div className="h-3 w-full rounded bg-muted" />
              <div className="h-3 w-9/12 rounded bg-muted" />
            </div>
          ))}
        </div>
      )}

      {!loading && results === null && (
        <div className="surface flex flex-col items-center px-6 py-14 text-center">
          <div className="flex size-11 items-center justify-center rounded-full bg-accent text-accent-foreground mb-4">
            <Inbox className="size-5" aria-hidden />
          </div>
          <h2 className="text-base font-semibold text-foreground">No matches loaded yet</h2>
          <p className="mt-2 max-w-md text-sm leading-6 text-muted-foreground">
            Describe the issue above and click "Find similar" to scan the repository indexing pipeline for candidate overlaps.
          </p>
        </div>
      )}

      {!loading && results && (
        <section className="surface overflow-hidden">
          <header className="flex items-center justify-between border-b border-border px-5 py-4">
            <h2 className="text-sm font-semibold text-foreground">
              Similar past issues &amp; decisions
            </h2>
            <span className="text-xs text-muted-foreground">{results.length} matches</span>
          </header>
          <ul className="divide-y divide-border">
            {results.map((m) => (
              <li key={m.id} className="px-5 py-4 transition-colors hover:bg-secondary">
                <div className="flex items-start justify-between gap-4">
                  <p className="text-sm font-medium text-foreground">{m.title}</p>
                  <span
                    className={`shrink-0 rounded-full border px-2 py-0.5 font-mono text-xs ${similarityTone(m.similarity)}`}
                  >
                    {Math.round(m.similarity * 100)}% match
                  </span>
                </div>
                <p className="mt-1.5 text-sm leading-6 text-muted-foreground">{m.summary}</p>
                <p className="mt-2 text-xs text-muted-foreground">
                  {m.when} · <span className="capitalize">{m.status}</span>
                </p>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
