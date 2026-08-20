import { useEffect, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { Search, GitBranch, ArrowRight, Loader2 } from "lucide-react";
import { listRepos, type Repo } from "@/lib/mock-api";

export function RepoSelector() {
  const [query, setQuery] = useState("");
  const [repos, setRepos] = useState<Repo[]>([]);
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();

  // Load repos on mount
  function loadRepos() {
    setLoading(true);
    listRepos()
      .then((data) => {
        setRepos(data);
      })
      .catch((err) => {
        console.error("Failed to load repositories:", err);
      })
      .finally(() => {
        setLoading(false);
      });
  }

  useEffect(() => {
    loadRepos();
  }, []);

  // Filter repositories lists
  const liveRepos = repos.filter(
    (r) => r.id.includes("/") && r.name.toLowerCase().includes(query.toLowerCase().trim())
  );

  return (
    <div className="flex flex-col gap-6">
      {/* Repositories Directory */}
      <div className="surface p-4 sm:p-5">
        <div className="relative mb-4">
          <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search active repositories…"
            aria-label="Search repositories"
            className="w-full rounded-lg border border-input bg-background py-2 pr-3 pl-9 text-sm placeholder:text-muted-foreground focus-visible:border-primary/50 focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none"
          />
        </div>

        {/* Live Repositories List */}
        <div>
          <h4 className="text-[10px] font-bold uppercase tracking-wider text-primary border-b border-border pb-1.5 mb-2">
            Active Indexed Repositories
          </h4>
          
          {loading ? (
            <div className="py-8 flex items-center justify-center gap-2 text-xs text-muted-foreground">
              <Loader2 className="size-4 animate-spin text-primary" />
              Loading workspaces...
            </div>
          ) : liveRepos.length > 0 ? (
            <ul className="divide-y divide-border">
              {liveRepos.map((repo) => (
                <li key={repo.id}>
                  <button
                    type="button"
                    onClick={() => navigate({ to: "/repo/$repoId", params: { repoId: repo.id } })}
                    className="group flex w-full items-center gap-4 rounded-lg px-2 py-3 text-left transition-colors hover:bg-secondary cursor-pointer"
                  >
                    <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                      <GitBranch className="size-4" aria-hidden />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block font-mono text-sm font-medium text-foreground truncate">
                        {repo.id}
                      </span>
                      <span className="mt-0.5 block truncate text-xs text-muted-foreground">
                        {repo.description}
                      </span>
                    </span>
                    <span className="flex items-center gap-2">
                      <span className="text-[10px] font-bold uppercase tracking-wider text-emerald-500 bg-emerald-500/10 border border-emerald-500/20 px-1.5 py-0.5 rounded">
                        Live
                      </span>
                      <ArrowRight className="size-3.5 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="py-6 text-center text-xs text-muted-foreground italic">
              {query.trim() ? "No matching active repositories." : "No active repositories connected. Connect one above!"}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
