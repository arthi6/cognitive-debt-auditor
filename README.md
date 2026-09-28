# Cognitive Debt Auditor

An AI agent that remembers why your code exists.

## Problem

Developers spend most of their time understanding existing code, not
writing new code. Stateless AI assistants make architectural decisions
look reversible when they aren't — they have no memory of the incident,
the ADR, or the tenant dependency that explains why a piece of "obviously
removable" code is still there.

## Solution

The Cognitive Debt Auditor retains a project's engineering history
(commits, incidents, architecture decisions, migrations) into
[Hindsight](https://github.com/vectorize-io/hindsight), then recalls
relevant history whenever a developer proposes a change — producing a
grounded risk assessment instead of generic advice. Outcomes are fed back
in as new procedural memory, so future audits get sharper.

## Why Hindsight is necessary

This is not a generic RAG-over-docs app. The value is specifically in
retain → recall → reflect as a loop: history compounds over time, and the
agent's own audit outcomes become new memory for the next audit. A
plain vector store (Chroma/FAISS/Pinecone/etc.) would need that loop
built by hand; Hindsight provides it natively. This project does not
substitute one.

## Architecture

```
dataset.json             synthetic engineering history (16 events)
context_ingestor.py       loads dataset.json, retains into Hindsight,
                          dedupes via a local seed-state sentinel
git_ingestor.py           reads REAL git commit history from any local
                          repo (git log, PR-merge/revert detection) and
                          retains it through the same pipeline
memory_retriever.py       recall() wrapper — normalizes responses safely,
                          never fabricates evidence, handles Hindsight
                          being offline or returning malformed data
audit_agent.py             recall → LLM (or transparent fallback) → structured
                          risk/evidence/recommendation
reflection_engine.py       outcome → lesson → retain as procedural memory;
                          also wraps reflect() for cross-history synthesis
app.py                    Streamlit UI — dataset seeding, real git-history
                          seeding, audit, and reflection
tests/test_core.py         mocked unit tests (no live server required)
tests/test_git_ingestor.py real (unmocked) tests against a temp git repo
tests/test_auto_reflect.py real git-reading tests + mocked Hindsight/LLM
smoke_test.py              checks your actual running environment
scripts/verify_retain_recall.py  real, unmocked retain→recall proof
                                   (retains a unique probe, asserts recall
                                   actually returns it — not just "didn't crash")
scripts/auto_reflect.py           runs reflection automatically, triggered
                                   by a real git merge/commit event
scripts/install_git_hooks.py      installs a post-merge (or post-commit)
                                   hook that calls auto_reflect.py for you
```

### Data flow

```
Historical Engineering Data
  → Hindsight Retain
  → Hindsight Recall
  → Cognitive Debt Audit (LLM or fallback)
  → Historical Evidence + Risk + Recommendation
  → Developer Outcome
  → Reflection → new lesson
  → Hindsight Retain
  → Future Audit Gets Smarter
```

### Memory model

Episodic / semantic / procedural memory is implemented as **tags on
content within a single Hindsight bank**, not as separate physical
stores — this is the honest, buildable version of a tiered memory
architecture; it is not a claim that Hindsight has three separate
databases.

## Setup

```bash
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt
copy .env.example .env         # fill in values, see below
```

### Environment variables

| Variable | Required | Default |
|---|---|---|
| `HINDSIGHT_BASE_URL` | yes | `http://localhost:8888` |
| `HINDSIGHT_BANK_ID` | yes | `cognitive-debt-auditor-demo` |
| `OLLAMA_BASE_URL` | yes | `http://localhost:11434/v1` |
| `OLLAMA_MODEL` | yes | `llama3.2:latest` |

Never commit `.env` — it's in `.gitignore`.

### Docker Hindsight setup

```bash
### Docker Hindsight setup

Hindsight runs locally through Docker and uses Ollama as the LLM provider.

Make sure Docker Desktop and Ollama are running, with
`llama3.2:latest` available locally.

```powershell
docker run -d --name hindsight --restart unless-stopped `
  -p 8888:8888 `
  -p 9999:9999 `
  --add-host=host.docker.internal:host-gateway `
  -e HINDSIGHT_API_LLM_PROVIDER=ollama `
  -e HINDSIGHT_API_LLM_BASE_URL=http://host.docker.internal:11434/v1 `
  -e HINDSIGHT_API_LLM_MODEL=llama3.2:latest `
  -e HINDSIGHT_API_LLM_MAX_CONCURRENT=1 `
  -e HINDSIGHT_API_ENABLE_OBSERVATIONS=false `
  -v hindsight-data:/home/hindsight/.pg0 `
  ghcr.io/vectorize-io/hindsight:latest
```

(Windows `cmd` syntax shown; adjust for PowerShell/WSL as needed.)

## How to seed memory

Either click **"Seed Historical Engineering Memory"** in the app, or:

```bash
python context_ingestor.py
```

A local `.seed_state.json` sentinel (hash of dataset + bank id) prevents
re-seeding the same dataset on every app restart. Use the "Force re-seed"
checkbox in the UI, or `force=True`, to override.

## How to run

```bash
streamlit run app.py
```

## Demo scenario

Default proposal: *"Remove the legacy authentication adapter and replace
it with the new OAuth flow."* See `demo_script.md` for the full 60-second
walkthrough and what the audit should surface.

## Reflection workflow

After an audit, record what actually happened (rejected / approved with
safeguards / completed / caused a problem) and click **"Reflect &
Remember."** This generates a concise lesson and retains it into Hindsight
tagged as procedural memory — the next audit on a related proposal can
recall it.

## Real Git history ingestion

Instead of (or in addition to) the synthetic `dataset.json`, you can seed
Hindsight from an actual repository's commit history — real messages,
authors, dates, changed files, with PR-merge and revert commits detected
and tagged accordingly:

```bash
# preview what would be ingested, without retaining anything
python git_ingestor.py --repo-path /path/to/your/repo --dry-run

# actually retain it
python git_ingestor.py --repo-path /path/to/your/repo --max-commits 300 --since "2024-01-01"
```

Or use the **"Load Real Git History"** section in the Streamlit app. Both
paths reuse the exact same `format_event_for_retain` formatter and retain
pipeline as the synthetic dataset — git-derived and dataset-derived memory
live side by side in the same bank. `tests/test_git_ingestor.py` builds a
real temporary git repo (no network needed) and asserts the parser and
PR-merge/revert classification against real `git log` output.

## Automatic learning after real Git/PR events

The manual "Reflect & Remember" flow in the UI still works, but reflection
can also be triggered automatically by a real merge landing, with no
developer clicking anything:

```bash
# one-time: install the hook into a repo (defaults to this project's own repo)
python scripts/install_git_hooks.py --repo-path /path/to/your/repo --hook post-merge

# uninstall
python scripts/install_git_hooks.py --repo-path /path/to/your/repo --hook post-merge --uninstall
```

From then on, every real `post-merge` in that repo runs
`scripts/auto_reflect.py`, which treats the merge itself as the outcome
("merged into `<branch>`"), recalls relevant prior memory for that change,
and retains a lesson as procedural memory — the same `reflect_and_store()`
used by the manual UI path, just triggered by a real git event instead of
a human. A Hindsight outage during the hook logs a warning but never fails
the actual `git merge` — see `tests/test_auto_reflect.py`.

For CI (e.g. GitHub Actions on `pull_request: closed` with `merged == true`),
call `python scripts/auto_reflect.py --event post-merge --repo-path .`
directly as a workflow step instead of installing a local hook.

## Verifying retain → recall for real

`tests/test_core.py` mocks Hindsight entirely, and `smoke_test.py`'s
round-trip check only confirms the calls don't throw — neither proves
recall actually returns what was just retained. For that:

```bash
python scripts/verify_retain_recall.py
```

This retains a uniquely-tagged probe event against your live Hindsight
instance, then recalls with a query built from that same marker, and
fails loudly if the marker isn't found in the results — the only way to
really confirm retain → recall end to end. Requires a reachable
`HINDSIGHT_BASE_URL` (see Docker setup above).

## Testing

```bash
pytest tests/ -v                        # mocked + real-temp-repo tests, no live server needed
python smoke_test.py                    # checks your actual environment (Hindsight, LLM, dataset)
python scripts/verify_retain_recall.py  # real, unmocked retain->recall proof (needs live Hindsight)
```

## Limitations

- Seeding uses client-side threading for progress/non-blocking UX rather
  than depending on Hindsight's server-side async-retain + operation-id
  polling, since that mechanism has had version-dependent bugs across
  Hindsight releases. If your installed version's async retain is
  confirmed stable, `context_ingestor.seed_into_hindsight` documents how
  to swap it in.
- The episodic/semantic/procedural memory model is tag-based within one
  bank, not separate physical stores.
- `dataset.json` is synthetic demo engineering history — no real
  companies, users, incidents, or production systems. Real history is
  available via `git_ingestor.py` against any actual repo.
- The self-critique relevance filter and fallback-mode reasoning are both
  intentionally conservative; neither replaces careful human review of a
  real production change.
- `git_ingestor.py` shells out to the system `git` binary via subprocess
  (no GitHub API calls), so PR-merge detection relies on the default
  "Merge pull request #N from ..." message GitHub writes on merge commits
  — squash-merges or other message formats won't match that pattern and
  are ingested as plain commits instead.
- `scripts/auto_reflect.py` infers the changed files from the merge
  commit's own diff (`git show`), which is empty for a clean fast-forward
  merge where the branch's changes were already committed — the lesson
  is still stored, just without a `files:` list in that case.

## Future improvements

- Native Hindsight async-retain + operation polling once confirmed stable
  on the target Hindsight version
- PR-merge detection via the GitHub API (or CI environment variables) as
  a fallback when commit messages don't follow GitHub's default merge
  format (e.g. squash-merged PRs)
