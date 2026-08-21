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

const API_BASE =
  typeof window !== "undefined"
    ? `${window.location.protocol}//${window.location.hostname}:8000`
    : "http://localhost:8000";

export async function listRepos(): Promise<Repo[]> {
  try {
    const response = await fetch(`${API_BASE}/repos`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return await response.json();
  } catch (error) {
    console.error("Failed to list repositories:", error);
    return [];
  }
}

export async function getRepo(id: string): Promise<Repo | undefined> {
  try {
    const response = await fetch(`${API_BASE}/repos/${id}`);
    if (response.ok) return await response.json();
  } catch (error) {
    console.error(`Failed to get repository ${id}:`, error);
  }
  return undefined;
}

export function sampleQuestions(): string[] {
  return [];
}

export async function askQuestion(repoId: string, question: string): Promise<Answer> {
  const response = await fetch(`${API_BASE}/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ repoId, question }),
  });
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${response.status}`);
  }
  return await response.json();
}

export async function recallIssue(
  repoId: string,
  title: string,
  body: string,
): Promise<RecallMatch[]> {
  const response = await fetch(`${API_BASE}/recall`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ repoId, title, body }),
  });
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${response.status}`);
  }
  return await response.json();
}
