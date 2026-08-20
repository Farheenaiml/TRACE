import { useEffect, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { Search, GitBranch, ArrowRight, Loader2, CheckCircle2, Circle, AlertCircle } from "lucide-react";
import { listRepos, ingestRepo, getIngestStatus, type Repo } from "@/lib/mock-api";

const INGEST_PHASES = [
  { id: "graphql", label: "Query GitHub GraphQL Discussions API" },
  { id: "cloning", label: "Perform local shallow Git clone (depth 30)" },
  { id: "ast_parsing", label: "Traverse codebase AST structure (classes/methods)" },
  { id: "commits", label: "Mine local commit history details using PyDriller" },
  { id: "issues", label: "Fetch GitHub REST Issues & Pull Request timelines" },
  { id: "extracting_rationale", label: "Extract decision rationales using NLP AI" },
  { id: "indexing", label: "Generate and write nodes/relationships to Neo4j Graph DB" },
];

export function RepoSelector() {
  const [query, setQuery] = useState("");
  const [repos, setRepos] = useState<Repo[]>([]);
  const [ingesting, setIngesting] = useState(false);
  const [currentPhase, setCurrentPhase] = useState<string>("");
  const [errorMsg, setErrorMsg] = useState("");
  const navigate = useNavigate();

  useEffect(() => {
    listRepos().then(setRepos);
  }, []);

  const filteredRepos = repos.filter((r) =>
    r.name.toLowerCase().includes(query.toLowerCase().trim()),
  );

  const handleIngest = async () => {
    setIngesting(true);
    setErrorMsg("");
    setCurrentPhase("graphql");
    let intervalId: any;
    try {
      const newRepo = await ingestRepo(query);
      const repoId = newRepo.id;

      // Start polling status every 1 second
      intervalId = setInterval(async () => {
        try {
          const statusRes = await getIngestStatus(repoId);
          const phase = statusRes.status;

          if (phase === "done") {
            clearInterval(intervalId);
            const updatedRepos = await listRepos();
            setRepos(updatedRepos);
            setQuery("");
            setIngesting(false);
            navigate({ to: "/repo/$repoId", params: { repoId } });
          } else if (phase.startsWith("error:")) {
            clearInterval(intervalId);
            setIngesting(false);
            setErrorMsg(phase.replace("error: ", ""));
          } else {
            setCurrentPhase(phase);
          }
        } catch (pollErr) {
          console.error("Error polling ingestion status:", pollErr);
        }
      }, 1000);

    } catch (err: any) {
      console.error(err);
      setErrorMsg(err.message || "Failed to initiate repository ingestion. Make sure it is public and correct.");
      setIngesting(false);
    }
  };

  return (
    <div className="surface p-4 sm:p-5">
      <div className="relative">
        <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          disabled={ingesting}
          placeholder="Search or paste GitHub URL…"
          aria-label="Search or paste GitHub URL"
          className="w-full rounded-lg border border-input bg-background py-2.5 pr-3 pl-9 text-sm placeholder:text-muted-foreground focus-visible:border-primary/50 focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none disabled:opacity-50"
        />
      </div>

      {ingesting && (
        <div className="mt-5 border border-border bg-card/60 backdrop-blur rounded-lg p-5">
          <div className="flex items-center justify-between mb-4 border-b border-border pb-3">
            <h3 className="text-sm font-semibold text-foreground flex items-center gap-2">
              <Loader2 className="size-4 animate-spin text-primary" />
              Ingesting Codebase
            </h3>
            <span className="text-xs text-muted-foreground italic">Running intelligence pipeline...</span>
          </div>
          <ul className="space-y-3.5">
            {INGEST_PHASES.map((phase, idx) => {
              const currentPhaseIdx = INGEST_PHASES.findIndex((p) => p.id === currentPhase);
              const isCompleted = idx < currentPhaseIdx;
              const isActive = idx === currentPhaseIdx;

              return (
                <li key={phase.id} className="flex items-start gap-3 text-sm">
                  {isCompleted ? (
                    <CheckCircle2 className="size-4.5 text-emerald-500 shrink-0 mt-0.5" />
                  ) : isActive ? (
                    <Loader2 className="size-4.5 text-primary animate-spin shrink-0 mt-0.5" />
                  ) : (
                    <Circle className="size-4.5 text-muted-foreground shrink-0 mt-0.5 opacity-30" />
                  )}
                  <span className={`leading-tight ${isCompleted ? 'text-muted-foreground line-through' : isActive ? 'text-foreground font-semibold' : 'text-muted-foreground opacity-60'}`}>
                    {phase.label}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {errorMsg && (
        <div className="mt-4 flex items-start gap-2.5 rounded-lg border border-destructive/20 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          <AlertCircle className="size-4 shrink-0 mt-0.5" />
          <div>
            <h4 className="font-semibold">Ingestion Failed</h4>
            <p className="mt-0.5 text-xs font-medium leading-5 opacity-90">{errorMsg}</p>
          </div>
        </div>
      )}

      {!ingesting && (
        <ul className="mt-3 divide-y divide-border">
          {filteredRepos.map((repo) => (
            <li key={repo.id}>
              <button
                type="button"
                onClick={() => navigate({ to: "/repo/$repoId", params: { repoId: repo.id } })}
                className="group flex w-full items-center gap-4 rounded-lg px-3 py-4 text-left transition-colors hover:bg-secondary"
              >
                <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-accent text-accent-foreground">
                  <GitBranch className="size-4" aria-hidden />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block font-mono text-sm font-medium text-foreground">
                    {repo.name}
                  </span>
                  <span className="mt-1 block truncate text-xs text-muted-foreground">
                    {repo.description}
                  </span>
                </span>
                <span className="hidden text-xs text-muted-foreground sm:block">
                  {repo.language} · {repo.decisions} decisions
                </span>
                <ArrowRight className="size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
              </button>
            </li>
          ))}
          {filteredRepos.length === 0 && (
            <li className="px-3 py-8 text-center text-sm text-muted-foreground">
              <p className="mb-4">No repositories match “{query}”.</p>
              {query.includes("/") || query.trim().length > 3 ? (
                <div className="flex flex-col items-center justify-center gap-2">
                  <button
                    type="button"
                    onClick={handleIngest}
                    className="ui-button inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground shadow transition hover:bg-primary/95 disabled:opacity-50 cursor-pointer"
                  >
                    Ingest &amp; Open Repository
                  </button>
                </div>
              ) : (
                <p className="text-xs text-muted-foreground italic">
                  Tip: Enter 'owner/repo' or a GitHub URL (e.g., 'facebook/react') to ingest any public repo.
                </p>
              )}
            </li>
          )}
        </ul>
      )}
    </div>
  );
}
