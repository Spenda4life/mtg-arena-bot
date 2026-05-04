# AI-Era Codebase Cleanup Concepts (from transcript)

## Core thesis
- **Software fundamentals matter more with AI, not less.**
- **Code is not cheap** when it becomes hard to change.
- The real cost driver is **complexity/entropy**: each uncontrolled change makes future changes harder.

## Practical failure modes and cleanup responses

### 1) "AI didn’t build what I meant"
**Problem:** Weak shared understanding between human and AI.

**Cleanup concept:**
- Force explicit design clarification before coding.
- Use a structured "grill me" phase: assumptions, constraints, edge cases, success criteria.

**Codebase action items:**
- Add/change templates in PR descriptions to require:
  - problem statement,
  - non-goals,
  - acceptance criteria,
  - risks/rollback.
- Capture design decisions close to code (small ADR notes per module).

---

### 2) "AI is verbose and misaligned"
**Problem:** No shared domain vocabulary.

**Cleanup concept:**
- Build a **ubiquitous language** (DDD): one glossary used in code, docs, and prompts.

**Codebase action items:**
- Standardize naming conventions for domain terms.
- Add a `docs/ubiquitous_language.md` glossary.
- Refactor ambiguous identifiers to domain-specific names.

---

### 3) "AI built something, but it doesn’t work"
**Problem:** Weak feedback loops; large unverified changes.

**Cleanup concept:**
- "Rate of feedback is your speed limit."
- Prefer **small steps + TDD** over large generated diffs.

**Codebase action items:**
- Require lint/type/test locally before merge.
- Encourage red-green-refactor loops for feature work.
- Break large tasks into testable sub-tasks.

---

### 4) "Codebase feels harder every week"
**Problem:** Architectural entropy; shallow modules.

**Cleanup concept:**
- Favor **deep modules**: simple interfaces hiding substantial internal logic.
- Reduce dependency tangles and cross-module leakage.

**Codebase action items:**
- Identify clusters of related logic and wrap behind clear module interfaces.
- Reduce "pass-through" utility layers with noisy APIs.
- Move tests toward interface-level contract tests per module.

---

### 5) "AI velocity increased, but humans are overloaded"
**Problem:** Cognitive overload from too many exposed details.

**Cleanup concept:**
- Design interfaces deliberately; treat internals as gray boxes where safe.
- Human owns strategic design; AI can own tactical implementation.

**Codebase action items:**
- Document module boundaries and ownership.
- Define which modules are high-criticality (require deeper review) vs lower-risk.
- Review and evolve interface design continuously.

## Suggested cleanup roadmap
1. Build/refresh domain glossary.
2. Map current modules + dependencies.
3. Choose 1–2 high-churn areas and refactor toward deeper modules.
4. Add interface/contract tests around refactored boundaries.
5. Enforce smaller PRs with explicit acceptance criteria.
6. Run recurring architecture check-ins to prevent entropy regression.

## Operating model with AI
- Use AI as a fast implementer, not autonomous architect.
- Keep humans responsible for:
  - module boundaries,
  - interface quality,
  - test strategy,
  - long-term design coherence.
