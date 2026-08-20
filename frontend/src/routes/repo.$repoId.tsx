import { createFileRoute, Link, Outlet, notFound } from "@tanstack/react-router";
import { Brain, GitBranch, ChevronsUpDown } from "lucide-react";
import { getRepo } from "@/lib/mock-api";
import { ThemeToggle } from "@/components/repomind/ThemeToggle";

export const Route = createFileRoute("/repo/$repoId")({
  loader: async ({ params }) => {
    const repo = await getRepo(params.repoId);
    if (!repo) throw notFound();
    return { repo };
  },
  component: RepoLayout,
});

function RepoLayout() {
  const { repo } = Route.useLoaderData();

  return (
    <div className="min-h-screen">
      <header className="border-b border-border bg-card/70 backdrop-blur">
        <div className="mx-auto flex w-full max-w-4xl items-center gap-3 px-5 py-4">
          <Link to="/" className="flex items-center gap-2 text-sm font-semibold text-foreground">
            <Brain className="size-4 text-primary" aria-hidden />
            RepoMind
          </Link>
          <span className="text-border">/</span>
          <Link
            to="/"
            className="inline-flex items-center gap-2 rounded-lg border border-border px-2.5 py-1.5 font-mono text-sm text-foreground transition-colors hover:bg-secondary"
            title="Switch repository"
          >
            <GitBranch className="size-3.5 text-muted-foreground" aria-hidden />
            {repo.name}
            <ChevronsUpDown className="size-3.5 text-muted-foreground" aria-hidden />
          </Link>
          <div className="ml-auto flex items-center gap-2">
            <nav className="flex items-center gap-1 rounded-lg border border-border p-1">
              <Link
                to="/repo/$repoId"
                params={{ repoId: repo.id }}
                activeOptions={{ exact: true }}
                activeProps={{ className: "bg-accent text-accent-foreground" }}
                inactiveProps={{ className: "text-muted-foreground hover:text-foreground" }}
                className="rounded-md px-3 py-1.5 text-xs font-medium transition-colors"
              >
                Ask
              </Link>
              <Link
                to="/repo/$repoId/recall"
                params={{ repoId: repo.id }}
                activeProps={{ className: "bg-accent text-accent-foreground" }}
                inactiveProps={{ className: "text-muted-foreground hover:text-foreground" }}
                className="rounded-md px-3 py-1.5 text-xs font-medium transition-colors"
              >
                Issue recall
              </Link>
              <Link
                to="/repo/$repoId/guardian"
                params={{ repoId: repo.id }}
                activeProps={{ className: "bg-accent text-accent-foreground" }}
                inactiveProps={{ className: "text-muted-foreground hover:text-foreground" }}
                className="rounded-md px-3 py-1.5 text-xs font-medium transition-colors"
              >
                RepoGuardian
              </Link>
              <Link
                to="/repo/$repoId/health"
                params={{ repoId: repo.id }}
                activeProps={{ className: "bg-accent text-accent-foreground" }}
                inactiveProps={{ className: "text-muted-foreground hover:text-foreground" }}
                className="rounded-md px-3 py-1.5 text-xs font-medium transition-colors"
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
