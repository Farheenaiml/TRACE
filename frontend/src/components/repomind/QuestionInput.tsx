import { useState } from "react";
import { Sparkles, CornerDownLeft } from "lucide-react";
import { Button } from "@/components/ui/button";

export function QuestionInput({
  onAsk,
  loading,
  suggestions = [],
}: {
  onAsk: (q: string) => void;
  loading?: boolean;
  suggestions?: string[];
}) {
  const [value, setValue] = useState("");

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!value.trim() || loading) return;
    onAsk(value.trim());
  }

  return (
    <section className="surface p-4 sm:p-5">
      <form onSubmit={submit} className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="relative flex-1">
          <Sparkles className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
          <input
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="Why was OAuth chosen over JWT in the auth module?"
            aria-label="Ask a question about this repository"
            className="w-full rounded-lg border border-input bg-background py-3 pr-3 pl-9 text-sm text-foreground placeholder:text-muted-foreground focus-visible:border-primary/50 focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none"
          />
        </div>
        <Button type="submit" size="lg" disabled={loading || !value.trim()} className="sm:w-32">
          {loading ? "Thinking…" : "Ask"}
          {!loading && <CornerDownLeft className="size-3.5 opacity-70" />}
        </Button>
      </form>

      {suggestions.length > 0 && (
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted-foreground">Try:</span>
          {suggestions.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => {
                setValue(s);
                onAsk(s);
              }}
              className="rounded-full border border-border px-3 py-1 text-xs text-muted-foreground transition-colors hover:border-primary/40 hover:bg-accent hover:text-accent-foreground"
            >
              {s}
            </button>
          ))}
        </div>
      )}
    </section>
  );
}
