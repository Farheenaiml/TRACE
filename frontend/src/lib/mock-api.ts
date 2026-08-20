/**
 * API client for RepoMind.
 *
 * Every function here mirrors a future REST endpoint so swapping in a real
 * backend is a one-file change:
 *   listRepos()      -> GET  /repos
 *   askQuestion()    -> POST /query   { repoId, question }
 *   recallIssue()    -> POST /recall  { repoId, title, body }
 */

export type Confidence = "high" | "medium" | "low";

export type Citation = {
  id: string;
  label: string;
  kind: "commit" | "pr" | "issue" | "doc";
  url: string;
};

export type Decision = {
  id: string;
  title: string;
  when: string;
  author: string;
};

export type Answer = {
  question: string;
  answer: string;
  confidence: Confidence;
  citations: Citation[];
  contradiction?: string;
  related: Decision[];
};

export type Repo = {
  id: string;
  name: string;
  description: string;
  language: string;
  decisions: number;
};

export type RecallMatch = {
  id: string;
  title: string;
  summary: string;
  similarity: number;
  when: string;
  status: "closed" | "open" | "merged";
};

const REPOS: Repo[] = [
  {
    id: "auth-service",
    name: "auth-service",
    description: "Identity, sessions and token issuance for the platform.",
    language: "TypeScript",
    decisions: 218,
  },
  {
    id: "minihooked",
    name: "MiniHooked",
    description: "Lightweight React hooks toolkit used across product teams.",
    language: "TypeScript",
    decisions: 94,
  },
  {
    id: "casesense",
    name: "CaseSense",
    description: "Case-management engine with a rules DSL and audit trail.",
    language: "Python",
    decisions: 341,
  },
];

const SAMPLE_ANSWERS: Answer[] = [
  {
    question: "Why was OAuth chosen over JWT in the auth module?",
    answer:
      "OAuth 2.0 was adopted as the authorization framework in Q2 2024 after the team hit revocation limits with self-issued JWTs. The discussion in PR #142 concluded that stateless JWTs made instant session revocation impossible without a deny-list, which would have reintroduced a shared datastore anyway. OAuth with short-lived access tokens plus refresh-token rotation gave the security team a revocation path and let the mobile clients reuse the provider's consent screen. JWT was not removed entirely — it remains the wire format for the access tokens themselves.",
    confidence: "high",
    citations: [
      { id: "c1", label: "commit a3f21c", kind: "commit", url: "#" },
      { id: "c2", label: "PR #142", kind: "pr", url: "#" },
      { id: "c3", label: "ADR-011 Token strategy", kind: "doc", url: "#" },
      { id: "c4", label: "issue #98", kind: "issue", url: "#" },
    ],
    contradiction:
      "This may conflict with a past decision: commit a3f21c removed the basic-auth fallback for security reasons, so any OAuth outage path must not re-enable it.",
    related: [
      {
        id: "d1",
        title: "Adopted refresh-token rotation for mobile clients",
        when: "3 months ago",
        author: "@rin",
      },
      {
        id: "d2",
        title: "Dropped basic-auth fallback from the gateway",
        when: "7 months ago",
        author: "@marcus",
      },
      {
        id: "d3",
        title: "Moved session store from Redis to Postgres",
        when: "9 months ago",
        author: "@aditi",
      },
      {
        id: "d4",
        title: "Standardised on 15-minute access token TTL",
        when: "1 year ago",
        author: "@rin",
      },
    ],
  },
  {
    question: "Why do we still ship a custom useDebounce instead of a library?",
    answer:
      "The team evaluated three third-party debounce hooks in early 2025 and kept the in-house implementation. The deciding factor was bundle weight: the candidates pulled in lodash-style scheduling helpers that added ~4kb gzipped for behaviour the internal version covers in 22 lines. A secondary concern raised in issue #61 was that external hooks flushed pending calls on unmount, which broke the search-as-you-type analytics contract.",
    confidence: "medium",
    citations: [
      { id: "c5", label: "commit 7b19de", kind: "commit", url: "#" },
      { id: "c6", label: "issue #61", kind: "issue", url: "#" },
      { id: "c7", label: "PR #77", kind: "pr", url: "#" },
    ],
    related: [
      {
        id: "d5",
        title: "Set a 12kb budget for the hooks bundle",
        when: "5 months ago",
        author: "@lena",
      },
      {
        id: "d6",
        title: "Rejected lodash as a runtime dependency",
        when: "8 months ago",
        author: "@marcus",
      },
      {
        id: "d7",
        title: "Added flush-on-unmount opt-in to useDebounce",
        when: "10 months ago",
        author: "@lena",
      },
    ],
  },
  {
    question: "Why is the rules engine evaluated server-side only?",
    answer:
      "Server-side evaluation was chosen so the rule set never leaves the audit boundary. Compliance review in 2024 flagged that shipping the DSL to the browser would expose scoring thresholds that clients are contractually not allowed to see. The team accepted the extra round-trip latency (~80ms p95) and added an optimistic UI layer instead. A partial client-side validator exists, but it only checks syntax, never outcomes.",
    confidence: "high",
    citations: [
      { id: "c8", label: "commit f04a11", kind: "commit", url: "#" },
      { id: "c9", label: "ADR-004 Rule evaluation", kind: "doc", url: "#" },
    ],
    related: [
      {
        id: "d8",
        title: "Added optimistic UI for rule previews",
        when: "2 months ago",
        author: "@aditi",
      },
      {
        id: "d9",
        title: "Split syntax validation out of the evaluator",
        when: "6 months ago",
        author: "@jonas",
      },
      {
        id: "d10",
        title: "Introduced immutable audit log for rule runs",
        when: "1 year ago",
        author: "@aditi",
      },
    ],
  },
];

const FALLBACK: Answer = {
  question: "",
  answer:
    "Based on the indexed history for this repository, there is no single decision record that directly answers this. The closest signal comes from a cluster of commits touching the same module, where the reasoning was captured in review comments rather than in a commit message or ADR. Treat the summary below as a reconstruction, not a documented decision, and confirm with the listed authors before relying on it.",
  confidence: "low",
  citations: [
    { id: "cf1", label: "commit 91cc02", kind: "commit", url: "#" },
    { id: "cf2", label: "PR #205", kind: "pr", url: "#" },
  ],
  related: [
    {
      id: "df1",
      title: "Review comments migrated into ADR format",
      when: "4 months ago",
      author: "@jonas",
    },
    {
      id: "df2",
      title: "Backfilled decision index for legacy commits",
      when: "11 months ago",
      author: "@rin",
    },
  ],
};

const RECALL_MATCHES: RecallMatch[] = [
  {
    id: "r1",
    title: "Login loop after token refresh on Safari",
    summary:
      "Same symptom class: refresh token rejected because the cookie SameSite attribute was dropped by the proxy. Fixed by pinning SameSite=None; Secure at the gateway.",
    similarity: 0.93,
    when: "4 months ago",
    status: "closed",
  },
  {
    id: "r2",
    title: "Intermittent 401s during deploy windows",
    summary:
      "Signing keys were rotated before the old key left the JWKS cache. The rollout order was changed so keys publish one deploy ahead of use.",
    similarity: 0.81,
    when: "7 months ago",
    status: "closed",
  },
  {
    id: "r3",
    title: "ADR-011: token strategy and revocation",
    summary:
      "Decision record explaining short-lived access tokens plus rotation, which constrains any fix that lengthens session lifetime.",
    similarity: 0.74,
    when: "1 year ago",
    status: "merged",
  },
  {
    id: "r4",
    title: "Session store migration caused duplicate sessions",
    summary:
      "During the Redis to Postgres cutover, dual writes produced two live sessions per user. Related if the report mentions duplicate devices.",
    similarity: 0.62,
    when: "9 months ago",
    status: "closed",
  },
  {
    id: "r5",
    title: "Rate limiter counts refresh calls as logins",
    summary:
      "Open issue with overlapping vocabulary; may be the same root cause if the user is being throttled.",
    similarity: 0.48,
    when: "3 weeks ago",
    status: "open",
  },
];

const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));

const API_BASE =
  typeof window !== "undefined"
    ? `${window.location.protocol}//${window.location.hostname}:8000`
    : "http://localhost:8000";

export async function listRepos(): Promise<Repo[]> {
  try {
    const response = await fetch(`${API_BASE}/repos`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return await response.json();
  } catch {
    return REPOS;
  }
}

export async function getRepo(id: string): Promise<Repo | undefined> {
  try {
    const response = await fetch(`${API_BASE}/repos/${id}`);
    if (response.ok) return await response.json();
  } catch {
    // Use local demo data when the backend is unavailable.
  }
  return REPOS.find((r) => r.id === id);
}

export function sampleQuestions(): string[] {
  return SAMPLE_ANSWERS.map((a) => a.question);
}

export async function askQuestion(_repoId: string, question: string): Promise<Answer> {
  await delay(1100);
  const q = question.toLowerCase();
  const hit = SAMPLE_ANSWERS.find((a) =>
    a.question
      .toLowerCase()
      .split(/\W+/)
      .filter((w) => w.length > 4)
      .some((w) => q.includes(w)),
  );
  return { ...(hit ?? FALLBACK), question };
}

export async function recallIssue(
  _repoId: string,
  title: string,
  _body: string,
): Promise<RecallMatch[]> {
  await delay(900);
  if (!title.trim()) return [];
  return RECALL_MATCHES;
}
