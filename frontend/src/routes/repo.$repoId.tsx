import { useState, useEffect } from "react";
import { createFileRoute, Link, Outlet, notFound, useNavigate } from "@tanstack/react-router";
import { Brain, GitBranch, ChevronsUpDown, Check, Plus } from "lucide-react";
import { getRepo, listRepos } from "@/lib/mock-api";
import { ThemeToggle } from "@/components/trace/ThemeToggle";

export const Route = createFileRoute("/repo/$repoId")({
  loader: async ({ params }) => {
    const rawRepoId = decodeURIComponent(params.repoId);
    const repo = await getRepo(rawRepoId);
    if (!repo) throw notFound();
    return { repo };
  },
  component: RepoLayout,
});

function RepoLayout() {
  const { repo } = Route.useLoaderData();
  const [showSwitcher, setShowSwitcher] = useState(false);
  const [repos, setRepos] = useState<any[]>([]);
  const navigate = useNavigate();

  useEffect(() => {
    listRepos().then(setRepos).catch(console.error);
  }, []);

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b border-border bg-card/70 backdrop-blur sticky top-0 z-40">
        <div className="mx-auto flex w-full max-w-4xl items-center gap-3 px-5 py-4">
          <Link to="/" className="flex items-center gap-2 text-sm font-semibold text-foreground">
            <Brain className="size-4 text-primary animate-pulse" aria-hidden />
            <span className="font-display font-bold tracking-tight">TRACE</span>
          </Link>
          <span className="text-border">/</span>
          
          {/* Interactive switcher dropdown */}
          <div className="relative">
            <button
              onClick={() => setShowSwitcher(!showSwitcher)}
              className="inline-flex items-center gap-2 rounded-lg border border-border px-2.5 py-1.5 font-mono text-xs font-semibold text-foreground transition-colors hover:bg-secondary focus:outline-none cursor-pointer bg-card/50"
              title="Switch repository"
            >
              <GitBranch className="size-3.5 text-primary" aria-hidden />
              <span className="truncate max-w-[120px] sm:max-w-[200px]">{repo.name}</span>
              <ChevronsUpDown className="size-3 text-muted-foreground" aria-hidden />
            </button>
            
            {showSwitcher && (
              <>
                {/* Click outside backdrop */}
                <div 
                  className="fixed inset-0 z-30" 
                  onClick={() => setShowSwitcher(false)} 
                />
                <div className="absolute left-0 mt-1.5 z-40 w-60 rounded-xl border border-border bg-card shadow-xl p-1.5 animate-in fade-in slide-in-from-top-1 duration-200">
                  <div className="px-2 py-1 text-[9px] font-bold uppercase tracking-wider text-muted-foreground">
                    Switch Workspace
                  </div>
                  <div className="mt-1 max-h-60 overflow-y-auto space-y-0.5">
                    {repos.map((r) => (
                      <button
                        key={r.id}
                        onClick={() => {
                          setShowSwitcher(false);
                          navigate({ to: "/repo/$repoId", params: { repoId: encodeURIComponent(r.id) } });
                        }}
                        className={`flex w-full items-center justify-between rounded-lg px-2.5 py-2 text-left font-mono text-xs transition-colors hover:bg-secondary cursor-pointer ${
                          r.id === repo.id ? "bg-accent text-accent-foreground font-semibold" : "text-foreground"
                        }`}
                      >
                        <span className="truncate pr-2">{r.name}</span>
                        {r.id === repo.id && <Check className="size-3.5 text-primary" />}
                      </button>
                    ))}
                  </div>
                  <div className="border-t border-border mt-1.5 pt-1.5">
                    <Link
                      to="/repos/new"
                      onClick={() => setShowSwitcher(false)}
                      className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs font-semibold text-primary hover:bg-primary/5 transition-colors cursor-pointer"
                    >
                      <Plus className="size-3.5" />
                      Connect codebase
                    </Link>
                  </div>
                </div>
              </>
            )}
          </div>

          <div className="ml-auto flex items-center gap-2">
            <nav className="flex items-center gap-0.5 rounded-lg border border-border p-0.5 bg-background/50">
              <Link
                to="/repo/$repoId"
                params={{ repoId: encodeURIComponent(repo.id) }}
                activeOptions={{ exact: true }}
                activeProps={{ className: "bg-accent text-accent-foreground shadow-sm" }}
                inactiveProps={{ className: "text-muted-foreground hover:text-foreground hover:bg-secondary/40" }}
                className="rounded-md px-2.5 py-1.5 text-xs font-semibold transition-colors"
              >
                Ask
              </Link>
              <Link
                to="/repo/$repoId/recall"
                params={{ repoId: encodeURIComponent(repo.id) }}
                activeProps={{ className: "bg-accent text-accent-foreground shadow-sm" }}
                inactiveProps={{ className: "text-muted-foreground hover:text-foreground hover:bg-secondary/40" }}
                className="rounded-md px-2.5 py-1.5 text-xs font-semibold transition-colors"
              >
                Recall
              </Link>
              <Link
                to="/repo/$repoId/guardian"
                params={{ repoId: encodeURIComponent(repo.id) }}
                activeProps={{ className: "bg-accent text-accent-foreground shadow-sm" }}
                inactiveProps={{ className: "text-muted-foreground hover:text-foreground hover:bg-secondary/40" }}
                className="rounded-md px-2.5 py-1.5 text-xs font-semibold transition-colors"
              >
                Guardian
              </Link>
              <Link
                to="/repo/$repoId/health"
                params={{ repoId: encodeURIComponent(repo.id) }}
                activeProps={{ className: "bg-accent text-accent-foreground shadow-sm" }}
                inactiveProps={{ className: "text-muted-foreground hover:text-foreground hover:bg-secondary/40" }}
                className="rounded-md px-2.5 py-1.5 text-xs font-semibold transition-colors"
              >
                Health
              </Link>
            </nav>
            <ThemeToggle />
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-4xl px-5 py-8 sm:py-10">
        <Outlet />
      </main>
    </div>
  );
}
