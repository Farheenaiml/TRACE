import { GitCommit, GitPullRequest, CircleDot, FileText } from "lucide-react";
import type { Citation } from "@/lib/mock-api";

const ICONS = {
  commit: GitCommit,
  pr: GitPullRequest,
  issue: CircleDot,
  doc: FileText,
} as const;

export function CitationChip({
  citation,
  onSelect,
}: {
  citation: Citation;
  onSelect?: ((c: Citation) => void) | undefined;
}) {
  const Icon =
    (citation.kind in ICONS ? ICONS[citation.kind as keyof typeof ICONS] : null) || GitCommit;
  return (
    <button
      type="button"
      onClick={() => onSelect?.(citation)}
      className="inline-flex items-center gap-1.5 rounded-full border border-border bg-secondary px-2.5 py-1 font-mono text-xs text-secondary-foreground transition-colors hover:border-primary/40 hover:bg-accent hover:text-accent-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
    >
      <Icon className="size-3.5 opacity-70" aria-hidden />
      {citation.label}
    </button>
  );
}
