import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { toast } from "sonner";
import { askQuestion, sampleQuestions, type Answer } from "@/lib/mock-api";
import { QuestionInput } from "@/components/trace/QuestionInput";
import { AnswerPanel, AnswerSkeleton, AnswerEmptyState } from "@/components/trace/AnswerPanel";
import { RelatedDecisionsList } from "@/components/trace/RelatedDecisionsList";

export const Route = createFileRoute("/repo/$repoId/")({
  head: () => ({
    meta: [
      { title: "Ask your codebase — TRACE" },
      {
        name: "description",
        content:
          "Ask why a decision was made and get an answer with citations, confidence and related decisions.",
      },
      { property: "og:title", content: "Ask your codebase — TRACE" },
      {
        property: "og:description",
        content: "Evidence-grounded answers about past engineering decisions.",
      },
    ],
  }),
  component: Dashboard,
});


function Dashboard() {
  const { repoId } = Route.useParams();
  const [loading, setLoading] = useState(false);
  const [answer, setAnswer] = useState<Answer | null>(null);

  async function handleAsk(question: string) {
    setLoading(true);
    setAnswer(null);
    const result = await askQuestion(repoId, question);
    setAnswer(result);
    setLoading(false);
  }

  return (
    <div className="flex flex-col gap-6">
      <QuestionInput
        onAsk={handleAsk}
        loading={loading}
        suggestions={sampleQuestions().slice(0, 2)}
      />

      {loading && <AnswerSkeleton />}
      {!loading && !answer && <AnswerEmptyState />}

      {!loading && answer && (
        <>
          <AnswerPanel
            answer={answer}
            onCitation={(c) => toast(c.label, { description: "Opening source evidence…" })}
          />
          <RelatedDecisionsList decisions={answer.related} />
        </>
      )}
    </div>
  );
}
