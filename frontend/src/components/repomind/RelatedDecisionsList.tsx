import { History, ArrowUpRight } from "lucide-react";
import type { Decision } from "@/lib/mock-api";

export function RelatedDecisionsList({ decisions }: { decisions: Decision[] }) {
  if (decisions.length === 0) return null;

  return (
    <section className="surface overflow-hidden">
      <header className="flex items-center gap-2 border-b border-border px-5 py-4">
        <History className="size-4 text-muted-foreground" aria-hidden />
        <h3 className="text-sm font-semibold text-foreground">Related past decisions</h3>
        <span className="ml-auto text-xs text-muted-foreground">{decisions.length} found</span>
      </header>
      <ul className="divide-y divide-border">
        {decisions.map((d) => (
          <li key={d.id}>
            <button
              type="button"
              className="group flex w-full items-center gap-4 px-5 py-4 text-left transition-colors hover:bg-secondary"
            >
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-foreground">{d.title}</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {d.when} · <span className="font-mono">{d.author}</span>
                </p>
              </div>
              <ArrowUpRight className="size-4 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
