import { MessageSquareQuote } from "lucide-react";
import type { Answer, Citation } from "@/lib/mock-api";
import { CitationChip } from "./CitationChip";
import { ConfidenceBadge } from "./ConfidenceBadge";
import { ContradictionBanner } from "./ContradictionBanner";

export function AnswerPanel({
  answer,
  onCitation,
}: {
  answer: Answer;
  onCitation?: ((c: Citation) => void) | undefined;
}) {
  return (
    <section className="flex flex-col gap-4">
      {answer.contradiction && <ContradictionBanner message={answer.contradiction} />}

      <article className="surface p-5 sm:p-7">
        <header className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex items-start gap-2.5">
            <MessageSquareQuote className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden />
            <h2 className="text-base font-semibold text-foreground">{answer.question}</h2>
          </div>
          <ConfidenceBadge level={answer.confidence} />
        </header>

        <p className="mt-5 text-[0.95rem] leading-7 text-foreground/90">{answer.answer}</p>

        <div className="mt-6 border-t border-border pt-4">
          <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
            Evidence
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            {answer.citations.map((c) => (
              <CitationChip key={c.id} citation={c} onSelect={onCitation} />
            ))}
          </div>
        </div>
      </article>
    </section>
  );
}

export function AnswerSkeleton() {
  return (
    <div className="surface animate-pulse p-5 sm:p-7">
      <div className="flex items-center justify-between gap-4">
        <div className="h-4 w-2/3 rounded bg-muted" />
        <div className="h-6 w-32 rounded-full bg-muted" />
      </div>
      <div className="mt-6 space-y-3">
        <div className="h-3 w-full rounded bg-muted" />
        <div className="h-3 w-11/12 rounded bg-muted" />
        <div className="h-3 w-10/12 rounded bg-muted" />
        <div className="h-3 w-7/12 rounded bg-muted" />
      </div>
      <div className="mt-7 flex gap-2">
        <div className="h-6 w-24 rounded-full bg-muted" />
        <div className="h-6 w-20 rounded-full bg-muted" />
        <div className="h-6 w-28 rounded-full bg-muted" />
      </div>
    </div>
  );
}

export function AnswerEmptyState() {
  return (
    <div className="surface flex flex-col items-center px-6 py-14 text-center">
      <div className="flex size-11 items-center justify-center rounded-full bg-accent text-accent-foreground">
        <MessageSquareQuote className="size-5" aria-hidden />
      </div>
      <h2 className="mt-4 text-lg font-semibold text-foreground">Ask why, not just what</h2>
      <p className="mt-2 max-w-md text-sm leading-6 text-muted-foreground">
        RepoMind reads commits, pull requests, issues and decision records to reconstruct the
        reasoning behind your codebase. Ask a question above to see an evidence-grounded answer.
      </p>
    </div>
  );
}
