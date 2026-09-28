# Demo Script — Cognitive Debt Auditor

## The scenario (rehearse this exact exchange)

**Proposed change:**
> "Remove the legacy authentication adapter and replace it with the new OAuth flow."

This is pre-loaded as the default text in the app's "Propose a Change" box.

## 60-second flow

**0:00–0:10 — Setup**
"Here's a codebase with three years of real engineering history behind one
file: a legacy authentication adapter. A new engineer just proposed removing
it."

**0:10–0:20 — Without memory**
Point at the "WITHOUT MEMORY" column: *"Legacy adapter appears unnecessary.
Consider replacing it with the new OAuth flow."* — this is what any
stateless assistant says. Sounds reasonable. It's also exactly the mistake
this team already made once, in 2022.

**0:20–0:45 — With Hindsight**
Click "Audit This Change." Walk through what comes back:
- Historical Memory cards showing the 2022 incident, the ADR, the SSO
  tenant dependency
- Risk: HIGH
- Recommendation: verify tenant compatibility before removing, rollback
  available

"The agent isn't guessing — it recalled a real postmortem and a binding
architecture decision before answering."

**0:45–0:60 — The compounding moment**
Pick an outcome ("Change approved with safeguards"), click "Reflect &
Remember." Show the generated lesson getting stored as procedural memory.
"Next time someone proposes this, the agent also recalls what we decided
today — not just what happened in 2022."

## Before recording

- Run `python smoke_test.py` — all required checks must pass
- Seed memory once via the UI button (or `python context_ingestor.py`)
  and confirm the seed didn't duplicate on a second app reload
- Run the audit once beforehand to confirm recall reliably surfaces the
  ADR-014 / 2022-incident evidence for the default proposal — if it
  doesn't, don't discover that live

## What NOT to claim

- Don't claim live git ingestion — this seeds a curated synthetic dataset
- Don't claim the episodic/semantic/procedural split is separate
  infrastructure — it's tags within one Hindsight bank
- If running in fallback mode (no OPENAI_API_KEY), say so explicitly —
  the audit shown is evidence-only, not LLM reasoning
