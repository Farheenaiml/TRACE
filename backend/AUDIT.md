# TRACE System Integration Audit Report

This report presents a thorough audit of the **TRACE** codebase, checking both the backend (`backend/`) and frontend (`frontend/`) implementations to evaluate system functionality and identify gaps.

---

## 1. Requirement Checklist & Verification

### 1. Happy Path Fetch Verification
* **Status:** ❌ Missing
* **Evidence:**
  * [`repo.$repoId.index.tsx`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/frontend/src/routes/repo.$repoId.index.tsx#L4): Imports `askQuestion` and `sampleQuestions` directly from `mock-api.ts`.
  * [`repo.$repoId.index.tsx`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/frontend/src/routes/repo.$repoId.index.tsx#L37): Calls `askQuestion(repoId, question)` which handles logic entirely on the client side without contacting the backend.
  * [`repo.$repoId.recall.tsx`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/frontend/src/routes/repo.$repoId.recall.tsx#L5): Imports `recallIssue` from `mock-api.ts`.
  * [`repo.$repoId.recall.tsx`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/frontend/src/routes/repo.$repoId.recall.tsx#L49): Invokes `recallIssue(repoId, title, body)` client-side.
  * *Note:* Although `API_BASE` is defined at [`repo.$repoId.recall.tsx#L26`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/frontend/src/routes/repo.$repoId.recall.tsx#L26), it is never used.

### 2. APScheduler Background Scanning
* **Status:** ✅ Working
* **Evidence:**
  * [`main.py`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/backend/main.py#L115): Instantiates `BackgroundScheduler()`.
  * [`main.py`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/backend/main.py#L117-L121): Registers `start_scheduler()` on the FastAPI `@app.on_event("startup")` hook, registering `scheduled_scan_job` to run at `SCAN_INTERVAL_MINUTES` intervals and logging `[scheduled scan] APScheduler started...` to stdout.
  * [`main.py`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/backend/main.py#L94-L113): Implements `scheduled_scan_job()` which searches for local ingested issue files `*_issues.json` in `data/raw/` and calls `run_repo_scan()`, which executes `run_scan(issues_list)`. Logs prefixed with `[scheduled scan]` are printed to stdout at each phase.

### 3. Agent Status Endpoint (`GET /agent/status`)
* **Status:** ✅ Working
* **Evidence:**
  * [`main.py`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/backend/main.py#L886-L916): Exposes the `GET /agent/status` route.
  * It attempts to load `{repo_slug}_agent_results.json` or `{repo_slug}_issues_agent_results.json`. If it exists as a JSON object, it returns it directly (including the real ISO-8601 timestamp `last_run` generated at [`main.py#L85`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/backend/main.py#L85)).
  * [`repo.$repoId.guardian.tsx`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/frontend/src/routes/repo.$repoId.guardian.tsx#L172-L192): Fetches status on page load via `${API_BASE}/agent/status?repoId=...` and sets `lastRun` to the returned timestamp, which is formatted and rendered in the sub-header at line 342.

### 4. Health Calculation Module (`health.py`)
* **Status:** ✅ Working
* **Evidence:**
  * [`health.py`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/backend/health.py#L35): Implements `compute_health(repo_id)`.
  * [`health.py`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/backend/health.py#L63-L108): Reads real ingested data from `data/raw/{repo_slug}_issues.json` and `{repo_slug}_agent_results.json` to calculate metrics (backlog count, contributor counts, duplicate rate, etc.).
  * [`health.py`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/backend/health.py#L114-L131): Automatically reads, appends the current snapshot, and saves the history to `data/raw/{repo_slug}_health_history.json` on each call.
  * *File Presence Note:* The `data/raw/` directory and `*_health_history.json` files do not exist yet on disk because:
    1. They are explicitly ignored in [`.gitignore`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/.gitignore#L16).
    2. No repository ingestion or health calculation has run locally to generate them.

### 5. Local Real Health Endpoint (`GET /health`)
* **Status:** ⚠️ Partially Implemented
* **Evidence:**
  * [`main.py`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/backend/main.py#L982-L1011): Declares `GET /health`.
  * [`main.py`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/backend/main.py#L992): Checks `if not issues_path.exists() or (is_mock and check_db_empty_for_repo(repoId)):`.
  * If true, it falls back to a hardcoded mock health dictionary (lines 993-1008). Because no local issues have been ingested to the database or saved to disk (so `issues_path.exists()` is false), this endpoint always returns hardcoded sample data rather than real computed metrics when tested locally. Once files are ingested and Neo4j is populated, the condition will be bypassed and it will correctly route to `compute_health()`.

### 6. Generalization of Extractor and Graph Store
* **Status:** ⚠️ Partially Implemented
* **Evidence:**
  * **Code level (✅ Working):**
    * [`extractor.py`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/backend/extractor.py#L51-L69): Dynamically handles `source_type` (defaulting to `"commit"` but fully parameterized).
    * [`graph_store.py`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/backend/graph_store.py#L44-L50): Implements `get_node_label(source_type)` which converts `"issue"` to label `"Issue"`, and `"pull_request"` / `"pr"` to `"PullRequest"`.
    * [`graph_store.py`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/backend/graph_store.py#L61-L66): Executes parameterized Cypher merges to dynamic labels (`MERGE (c:${label} {id: $source_id})`).
    * [`graph_store.py`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/backend/graph_store.py#L101): Properly includes the correct payload `type` for Qdrant.
  * **Runtime level (❌ Missing):**
    * Running `docker ps` returns a daemon connection error. Since the Docker daemon is offline on the local host, there are no running Neo4j or Qdrant containers. Consequently, no database nodes actually exist or can be verified locally.

### 7. Triage Feedback Persistence (`POST /agent/feedback`)
* **Status:** ✅ Working
* **Evidence:**
  * [`main.py`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/backend/main.py#L1017-L1093): Implements `POST /agent/feedback` using `FeedbackRequest`.
  * It attempts to write a `:Feedback` node and link it to the issue via a `:HAS_FEEDBACK` relationship in Neo4j (lines 1034-1061). If the database write fails or is offline, it falls back to appending feedback to `data/raw/{repo_slug}_feedback.json` (lines 1063-1091).
  * [`repo.$repoId.guardian.tsx`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/frontend/src/routes/repo.$repoId.guardian.tsx#L130-L142): `handleFeedback()` executes a POST request to `/agent/feedback`. This is triggered by:
    * Thumbs-up button: [`repo.$repoId.guardian.tsx#L448`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/frontend/src/routes/repo.$repoId.guardian.tsx#L448) (confirms decision).
    * Correction dropdown select option: [`repo.$repoId.guardian.tsx#L501`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/frontend/src/routes/repo.$repoId.guardian.tsx#L501) (corrects decision).

### 8. Feedback Integration in LLM Escalation
* **Status:** ✅ Working
* **Evidence:**
  * [`agent.py`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/backend/agent.py#L241-L248): `score_escalation()` queries historical feedback using `get_feedback_for_issue(repo_id, d["id"])`. If a duplicate issue has maintainer feedback where the decision was marked incorrect (`not fb.get("correct")`), it compiles a detailed correction string.
  * [`agent.py`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/backend/agent.py#L272-L273): Appends the maintainer feedback string into the final user prompt sent to the LLM:
    ```python
    if feedback_str:
        user_prompt += f"\n\nMaintainer Feedback on Similar Past Issues:\n{feedback_str}"
    ```

### 9. Needs More Info Follow-up Field
* **Status:** ❌ Missing
* **Evidence:**
  * No field named `suggested_followup` or similar variant exists in `agent.py`, `main.py`, or any frontend file.
  * The LLM prompt in `score_escalation()` only expects `'decision'`, `'reason'`, and `'security_sensitive'` keys (lines 260-263), and the deterministic fallback does not produce a follow-up.
  * No textarea renders in the UI to display or edit follow-up steps for `needs_more_info` decisions.

### 10. Brief Generation & Health Forecasting
* **Status:** ✅ Working
* **Evidence:**
  * [`brief.py`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/backend/brief.py#L34-L196): `generate_weekly_brief()` computes stats from health history, scan decisions, and highly-discussed issues, and prompts the LLM (falling back to a local summary builder) to generate a brief.
  * [`main.py`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/backend/main.py#L1012-L1015): Wire-up of `GET /brief`.
  * [`health.py`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/backend/health.py#L143-L185): OLS forecasting only runs when `len(valid_snaps) >= 3`. Otherwise, it exits early returning `"forecast_status": "insufficient_history"` and setting projected fields to `None` without faking values or crashing.

---

## 2. References to Mock/Sample Data

Below is a detailed inventory of remaining mock elements in the codebase:

### Frontend references to `mock-api.ts`
* [`repo.$repoId.index.tsx#L4`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/frontend/src/routes/repo.$repoId.index.tsx#L4)
* [`repo.$repoId.recall.tsx#L5`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/frontend/src/routes/repo.$repoId.recall.tsx#L5)
* [`repo.$repoId.tsx#L3`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/frontend/src/routes/repo.$repoId.tsx#L3)
* [`components/trace/AnswerPanel.tsx#L2`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/frontend/src/components/trace/AnswerPanel.tsx#L2)
* [`components/trace/CitationChip.tsx#L2`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/frontend/src/components/trace/CitationChip.tsx#L2)
* [`components/trace/ConfidenceBadge.tsx#L2`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/frontend/src/components/trace/ConfidenceBadge.tsx#L2)
* [`components/trace/RelatedDecisionsList.tsx#L2`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/frontend/src/components/trace/RelatedDecisionsList.tsx#L2)
* [`components/trace/RepoSelector.tsx#L4`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/frontend/src/components/trace/RepoSelector.tsx#L4)
* [`components/repomind/ConfidenceBadge.tsx#L2`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/frontend/src/components/repomind/ConfidenceBadge.tsx#L2)
* [`components/repomind/RepoSelector.tsx#L4`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/frontend/src/components/repomind/RepoSelector.tsx#L4)
* [`components/repomind/RelatedDecisionsList.tsx#L2`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/frontend/src/components/repomind/RelatedDecisionsList.tsx#L2)
* [`components/repomind/CitationChip.tsx#L2`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/frontend/src/components/repomind/CitationChip.tsx#L2)
* [`components/repomind/AnswerPanel.tsx#L2`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/frontend/src/components/repomind/AnswerPanel.tsx#L2)
* [`frontend/src/lib/mock-api.ts`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/frontend/src/lib/mock-api.ts): Contains mock objects `REPOS` (line 53), `SAMPLE_ANSWERS` (line 77), `FALLBACK` (line 181), and `RECALL_MATCHES` (line 206).

### Backend references to `MOCK_REPOS`
* [`main.py#L222`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/backend/main.py#L222): List declaration.
* [`main.py#L508`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/backend/main.py#L508): Fallback in `GET /repos`.
* [`main.py#L531`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/backend/main.py#L531): Fallback in `GET /repos` (GitHub integration active).
* [`main.py#L534`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/backend/main.py#L534): Iteration to inject mocks.
* [`main.py#L541`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/backend/main.py#L541): Final fallback.
* [`main.py#L579`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/backend/main.py#L579): Check in `GET /repos/{repo_id}`.
* [`main.py#L611`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/backend/main.py#L611): Check in `POST /query`.
* [`main.py#L799`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/backend/main.py#L799): Check in `POST /recall`.
* [`main.py#L990`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/backend/main.py#L990): Check in `GET /health`.

### Backend references to other mock data arrays
* [`main.py#L246`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/backend/main.py#L246): `MOCK_ANSWERS` definition.
* [`main.py#L296`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/backend/main.py#L296): `MOCK_RECALLS` definition.

### Frontend local fallbacks
* [`repo.$repoId.health.tsx#L79`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/frontend/src/routes/repo.$repoId.health.tsx#L79): `MOCK_HEALTH_FALLBACK` definition (loaded when backend fetch fails, line 154).

---

## 3. Endpoint Dead-Code Analysis

### Unimplemented frontend calls to missing main.py routes
* **None.** All API endpoints called in the frontend fetch hooks exist on the backend.

### Dead-code candidate backend endpoints (never called by the frontend)
* **`POST /query`** ([`main.py#L585`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/backend/main.py#L585)): Unused because `repo.$repoId.index.tsx` (the "Ask" dashboard) resolves questions entirely on the client side using the mock module.
* **`POST /recall`** ([`main.py#L797`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/backend/main.py#L797)): Unused because `repo.$repoId.recall.tsx` (the "Recall" dashboard) performs semantic searches locally using mock data.

---

## 4. Prioritized Punch List

### Group A: Compulsory Gaps (High Priority)
1. **Connect Ask Dashboard to Backend:** Modify [`repo.$repoId.index.tsx`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/frontend/src/routes/repo.$repoId.index.tsx) to fetch from `${API_BASE}/query` instead of importing `askQuestion` from `mock-api.ts`.
2. **Connect Recall Dashboard to Backend:** Modify [`repo.$repoId.recall.tsx`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/frontend/src/routes/repo.$repoId.recall.tsx) to fetch from `${API_BASE}/recall` instead of importing `recallIssue` from `mock-api.ts`.
3. **Remove Mock Fallbacks from Health Endpoint:** Edit the `/health` endpoint logic in [`main.py#L992`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/backend/main.py#L992) so it raises a `404` or runs on empty data rather than returning hardcoded mock datasets when no issues have been ingested.
4. **Clean Up Mock References:** Remove `MOCK_REPOS`, `MOCK_ANSWERS`, and `MOCK_RECALLS` from the backend codebase, and remove `mock-api.ts` from the frontend folder once endpoints are fully wired up.

### Group B: Bonus Gaps (Medium Priority)
1. **Implement `suggested_followup`:**
   * Update the LLM instructions and schema structure in `score_escalation()` ([`agent.py`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/backend/agent.py)) to output a `suggested_followup` string when the decision is `"needs_more_info"`.
   * Add a `suggested_followup` parameter to the API responses.
   * Add a responsive, editable `<textarea>` to the UI inside [`repo.$repoId.guardian.tsx`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/frontend/src/routes/repo.$repoId.guardian.tsx) allowing maintainers to inspect and tweak the suggested follow-up instructions.

### Group C: Polish Items (Low Priority)
1. **Ensure Offline Safety for Health Page:** In [`repo.$repoId.health.tsx`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/frontend/src/routes/repo.$repoId.health.tsx), improve styling/handling for `forecast_status` when it is not returned (e.g. if the mock fallback is hit) to prevent fallback rendering bugs.
2. **Docker Dev Environment Verification:** Write clear documentation or a verification script in the repo explaining how to fire up local Docker containers for Neo4j and Qdrant to verify database persistence in an isolated development environment.
