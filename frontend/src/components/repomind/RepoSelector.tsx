import { useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { Search, GitBranch, ArrowRight } from "lucide-react";
import { listRepos } from "@/lib/mock-api";

export function RepoSelector() {
  const [query, setQuery] = useState("");
  const navigate = useNavigate();
  const repos = listRepos().filter((r) =>
    r.name.toLowerCase().includes(query.toLowerCase().trim()),
  );

  return (
    <div className="surface p-4 sm:p-5">
      <div className="relative">
        <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search repositories…"
          aria-label="Search repositories"
          className="w-full rounded-lg border border-input bg-background py-2.5 pr-3 pl-9 text-sm placeholder:text-muted-foreground focus-visible:border-primary/50 focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none"
        />
      </div>

      <ul className="mt-3 divide-y divide-border">
        {repos.map((repo) => (
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
        {repos.length === 0 && (
          <li className="px-3 py-8 text-center text-sm text-muted-foreground">
            No repositories match “{query}”.
          </li>
        )}
      </ul>
    </div>
  );
}
