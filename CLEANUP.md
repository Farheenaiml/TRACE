# Repository Cleanup & Consolidation Record

This document records the files, functions, and packages that were removed or consolidated during the TRACE codebase cleanup phase.

---

## 1. Consolidated Logic

### LLM Call Fallback Pattern Consolidation
* **Original state**: The Groq API -> Ollama fallback -> local rule-based deterministic fallback completion pattern was copy-pasted in multiple modules:
  * `backend/main.py` (`call_llm`)
  * `backend/agent.py` (`score_escalation`)
  * `backend/brief.py` (`generate_weekly_brief`)
* **Consolidated state**:
  * Created a new shared module [`backend/llm.py`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/backend/llm.py) containing `call_llm_json(system_prompt, user_prompt, temperature)`.
  * Rewrote all three call sites to import and run `call_llm_json` from this shared module, keeping only the case-specific deterministic fallback code at the call site if `call_llm_json` returns `None`.

### Frontend API Wrappers
* **Original state**: `frontend/src/lib/mock-api.ts` contained `askQuestion` and `recallIssue` wrappers that only returned mock data after a hardcoded delay, bypassing the real FastAPI server completely.
* **Consolidated state**:
  * Modified `askQuestion` and `recallIssue` in `mock-api.ts` to attempt fetching from the real backend routes (`/query` and `/recall` respectively).
  * Maintained local mock datasets inside `mock-api.ts` purely as caught-block fallbacks to implement the required graceful-degradation when the backend is offline.

---

## 2. Deleted Files

### Backend
* [`backend/models.py`](file:///c:/Users/sadaf/OneDrive/Desktop/TRACE/TRACE/backend/models.py): Removed entirely. It defined type dictionaries that were completely unused and never imported anywhere in the backend codebase.

### Frontend
* `frontend/src/components/repomind/` directory: Deleted entirely. This folder was a leftover duplicate of the `components/trace/` components and was never imported or referenced.
* `frontend/src/hooks/use-mobile.tsx`: Deleted. It was only imported by `components/ui/sidebar.tsx`, which was unused.
* Unused shadcn/ui components in `frontend/src/components/ui/`:
  * Keep: `button.tsx`, `sonner.tsx`
  * Deleted: `accordion.tsx`, `alert-dialog.tsx`, `alert.tsx`, `aspect-ratio.tsx`, `avatar.tsx`, `badge.tsx`, `breadcrumb.tsx`, `calendar.tsx`, `card.tsx`, `carousel.tsx`, `chart.tsx`, `checkbox.tsx`, `collapsible.tsx`, `command.tsx`, `context-menu.tsx`, `dialog.tsx`, `drawer.tsx`, `dropdown-menu.tsx`, `form.tsx`, `hover-card.tsx`, `input-otp.tsx`, `input.tsx`, `label.tsx`, `menubar.tsx`, `navigation-menu.tsx`, `pagination.tsx`, `popover.tsx`, `progress.tsx`, `radio-group.tsx`, `resizable.tsx`, `scroll-area.tsx`, `select.tsx`, `separator.tsx`, `sheet.tsx`, `sidebar.tsx`, `skeleton.tsx`, `slider.tsx`, `switch.tsx`, `table.tsx`, `tabs.tsx`, `textarea.tsx`, `toggle-group.tsx`, `toggle.tsx`, `tooltip.tsx`.

---

## 3. Removed Dependencies

### Backend requirements.txt
* Removed `spacy` (never imported or referenced).

### Frontend package.json
* Removed unused libraries and Radix primitives associated with the deleted components:
  * `@hookform/resolvers`
  * `@radix-ui/react-accordion`
  * `@radix-ui/react-alert-dialog`
  * `@radix-ui/react-aspect-ratio`
  * `@radix-ui/react-avatar`
  * `@radix-ui/react-checkbox`
  * `@radix-ui/react-collapsible`
  * `@radix-ui/react-context-menu`
  * `@radix-ui/react-dialog`
  * `@radix-ui/react-dropdown-menu`
  * `@radix-ui/react-hover-card`
  * `@radix-ui/react-label`
  * `@radix-ui/react-menubar`
  * `@radix-ui/react-navigation-menu`
  * `@radix-ui/react-popover`
  * `@radix-ui/react-progress`
  * `@radix-ui/react-radio-group`
  * `@radix-ui/react-scroll-area`
  * `@radix-ui/react-select`
  * `@radix-ui/react-separator`
  * `@radix-ui/react-slider`
  * `@radix-ui/react-switch`
  * `@radix-ui/react-tabs`
  * `@radix-ui/react-toggle`
  * `@radix-ui/react-toggle-group`
  * `@radix-ui/react-tooltip`
  * `cmdk`
  * `date-fns`
  * `embla-carousel-react`
  * `input-otp`
  * `react-day-picker`
  * `react-hook-form`
  * `react-resizable-panels`
  * `vaul`
  * `zod`
