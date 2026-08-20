import { ShieldCheck, ShieldAlert, ShieldQuestion } from "lucide-react";
import type { Confidence } from "@/lib/mock-api";
import { cn } from "@/lib/utils";

const MAP = {
  high: {
    label: "High confidence",
    Icon: ShieldCheck,
    cls: "border-success/35 bg-success/12 text-success",
  },
  medium: {
    label: "Medium confidence",
    Icon: ShieldQuestion,
    cls: "border-warning/40 bg-warning/15 text-warning",
  },
  low: {
    label: "Low confidence",
    Icon: ShieldAlert,
    cls: "border-destructive/35 bg-destructive/12 text-destructive",
  },
} as const;

export function ConfidenceBadge({ level, className }: { level: Confidence; className?: string }) {
  const { label, Icon, cls } = MAP[level];
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium",
        cls,
        className,
      )}
    >
      <Icon className="size-3.5" aria-hidden />
      {label}
    </span>
  );
}
