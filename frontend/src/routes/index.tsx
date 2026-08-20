import { createFileRoute } from "@tanstack/react-router";
import { Brain } from "lucide-react";
import { RepoSelector } from "@/components/trace/RepoSelector";
import { ThemeToggle } from "@/components/trace/ThemeToggle";

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
  return (
    <main className="mx-auto flex min-h-screen w-full max-w-3xl flex-col px-5 py-14 sm:py-24">
      <div className="flex items-start justify-between gap-4">
        <div>
          <span className="inline-flex items-center gap-2 rounded-full border border-border bg-card px-3 py-1 text-xs text-muted-foreground">
            <Brain className="size-3.5 text-primary" aria-hidden />
            Decision intelligence
          </span>
          <h1 className="mt-5 text-4xl font-semibold text-foreground sm:text-5xl">TRACE</h1>
          <p className="mt-3 max-w-xl text-base leading-7 text-muted-foreground">
            Your codebase remembers what changed. TRACE remembers <em>why</em> — ask in plain
            language and get answers grounded in commits, pull requests and decision records.
          </p>
        </div>
        <ThemeToggle />
      </div>

      <div className="mt-10">
        <h2 className="mb-3 text-sm font-medium text-muted-foreground">Choose a repository</h2>
        <RepoSelector />
      </div>

      <p className="mt-8 text-xs text-muted-foreground">
        Demo data only — no repositories are indexed in this preview.
      </p>
    </main>
  );
}
