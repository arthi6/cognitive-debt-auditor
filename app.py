"""
Cognitive Debt Auditor — Streamlit demo UI.

Run with:
    streamlit run app.py

Design constraints honored here:
- Seeding only runs on button click, never automatically on every rerun.
- All expensive/slow operations use session_state to avoid re-running on
  every Streamlit interaction.
- Every Hindsight/LLM failure path shows a clear message — nothing is
  silently faked.
- Hindsight system status uses the HTTP health endpoint instead of
  calling recall(), avoiding unnecessary SDK/event-loop conflicts.
"""

import time

import streamlit as st

from config import (
    HINDSIGHT_BASE_URL,
    BANK_ID,
    LLM_AVAILABLE,
    OLLAMA_MODEL,
    DEFAULT_DEMO_PROPOSAL,
    DATASET_PATH,
)

from context_ingestor import (
    load_dataset,
    seed_into_hindsight,
    is_already_seeded,
)

from git_ingestor import (
    ingest_git_history,
    seed_git_history_into_hindsight,
    GitIngestError,
)

from audit_agent import audit_change

from reflection_engine import (
    reflect_and_store,
    synthesize_patterns,
)


# ---------------------------------------------------------------------
# PAGE CONFIGURATION
# ---------------------------------------------------------------------

st.set_page_config(
    page_title="Cognitive Debt Auditor",
    layout="centered",
)


# ---------------------------------------------------------------------
# SESSION STATE INIT
# Keeps expensive results across Streamlit reruns.
# ---------------------------------------------------------------------

if "seed_status" not in st.session_state:
    st.session_state.seed_status = None

if "audit_result" not in st.session_state:
    st.session_state.audit_result = None

if "reflection_result" not in st.session_state:
    st.session_state.reflection_result = None

if "timeline" not in st.session_state:
    st.session_state.timeline = []


# ---------------------------------------------------------------------
# HINDSIGHT HEALTH CHECK
# ---------------------------------------------------------------------

def _check_hindsight() -> tuple[bool, str]:
    """
    Check whether Hindsight is available through its HTTP health endpoint.

    Important:
    We intentionally DO NOT call Hindsight.recall() here.

    Streamlit reruns this function frequently. Calling the Hindsight SDK
    on every rerun just to display a connectivity status can cause
    asyncio event-loop conflicts.

    Actual memory retrieval still happens inside memory_retriever.py.
    """

    try:
        import requests

        response = requests.get(
            f"{HINDSIGHT_BASE_URL}/health",
            timeout=5,
        )

        if response.status_code == 200:
            return True, "Connected"

        return (
            False,
            f"Health check returned HTTP {response.status_code}",
        )

    except requests.RequestException as e:
        return False, str(e)

    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------------
# HEADER
# ---------------------------------------------------------------------

st.title("Cognitive Debt Auditor")

st.caption(
    "An AI agent that remembers why your code exists."
)


# ---------------------------------------------------------------------
# 1. SYSTEM STATUS
# ---------------------------------------------------------------------

st.header("System Status")

col1, col2, col3, col4 = st.columns(4)


# ---------------------------------------------------------------------
# HINDSIGHT STATUS
# ---------------------------------------------------------------------

with col1:

    hs_ok, hs_msg = _check_hindsight()

    if hs_ok:
        st.success("Hindsight: Connected")
    else:
        st.error("Hindsight: Unavailable")


# ---------------------------------------------------------------------
# BANK STATUS
# ---------------------------------------------------------------------

with col2:

    st.info(
        f"Bank: {BANK_ID}"
    )


# ---------------------------------------------------------------------
# LLM STATUS
# ---------------------------------------------------------------------

with col3:

    if LLM_AVAILABLE:

        st.success(
            f"LLM: Ollama — {OLLAMA_MODEL}"
        )

    else:

        st.warning(
            "LLM: Not configured"
        )


# ---------------------------------------------------------------------
# MEMORY STATUS
# ---------------------------------------------------------------------

with col4:

    seeded = is_already_seeded()

    if seeded:

        try:
            n = len(load_dataset())

        except Exception:
            n = "?"

        st.success(
            f"Memories: {n} seeded"
        )

    else:

        st.warning(
            "Memories: Not seeded"
        )


# ---------------------------------------------------------------------
# HINDSIGHT ERROR MESSAGE
# ---------------------------------------------------------------------

if not hs_ok:

    st.error(
        "Hindsight is unavailable. Historical memory cannot be verified. "
        f"Details: {hs_msg}"
    )


st.divider()


# ---------------------------------------------------------------------
# 2. LOAD ENGINEERING HISTORY
# ---------------------------------------------------------------------

st.header("Load Engineering History")


seed_col1, seed_col2 = st.columns([1, 1])


# ---------------------------------------------------------------------
# SEED BUTTON
# ---------------------------------------------------------------------

with seed_col1:

    seed_clicked = st.button(
        "Seed Historical Engineering Memory",
        type="primary",
    )


# ---------------------------------------------------------------------
# FORCE RE-SEED
# ---------------------------------------------------------------------

with seed_col2:

    force_reseed = st.checkbox(
        "Force re-seed",
        value=False,
        help="Re-ingest even if this dataset was already seeded.",
    )


# ---------------------------------------------------------------------
# SEEDING LOGIC
# ---------------------------------------------------------------------

if seed_clicked:

    progress_bar = st.progress(
        0,
        text="Starting...",
    )

    result = seed_into_hindsight(
        force=force_reseed
    )

    status = result["status"]

    if status.get("skipped"):

        st.info(
            "Dataset already seeded for this bank — skipped to avoid "
            "duplicates. Check 'Force re-seed' to ingest again."
        )

    else:

        while not status["finished"]:

            done = status["done"]
            total = status["total"]

            pct = (
                int(100 * done / total)
                if total
                else 0
            )

            progress_bar.progress(
                min(pct, 100),
                text=f"Seeding memory: {done}/{total}",
            )

            time.sleep(0.2)

        progress_bar.progress(
            100,
            text=(
                f"Seeding memory: "
                f"{status['done']}/{status['total']}"
            ),
        )

        if status["errors"]:

            st.error(
                f"Completed with "
                f"{len(status['errors'])} error(s):"
            )

            for e in status["errors"]:

                st.text(
                    f"  - {e}"
                )

        else:

            st.success(
                f"Seeded "
                f"{status['done']} historical events "
                f"into Hindsight."
            )

    st.session_state.seed_status = status

    st.rerun()


st.divider()


# ---------------------------------------------------------------------
# 2b. LOAD REAL GIT HISTORY
# ---------------------------------------------------------------------

st.header(
    "Load Real Git History (optional)"
)

st.caption(
    "Ingest an actual repository's commit history — real commit "
    "messages, authors, dates, changed files, and PR-merge/revert "
    "detection — instead of (or alongside) the synthetic dataset above."
)


git_col1, git_col2, git_col3 = st.columns(
    [2, 1, 1]
)


# ---------------------------------------------------------------------
# GIT REPOSITORY PATH
# ---------------------------------------------------------------------

with git_col1:

    git_repo_path = st.text_input(
        "Local path to a git repository",
        value=".",
    )


# ---------------------------------------------------------------------
# MAX COMMITS
# ---------------------------------------------------------------------

with git_col2:

    git_max_commits = st.number_input(
        "Max commits",
        min_value=1,
        max_value=2000,
        value=200,
    )


# ---------------------------------------------------------------------
# SINCE DATE
# ---------------------------------------------------------------------

with git_col3:

    git_since = st.text_input(
        "Since (optional)",
        value="",
        placeholder="e.g. 2024-01-01",
    )


# ---------------------------------------------------------------------
# GIT SEED BUTTON
# ---------------------------------------------------------------------

git_seed_clicked = st.button(
    "Seed Real Git History"
)


# ---------------------------------------------------------------------
# GIT INGESTION
# ---------------------------------------------------------------------

if git_seed_clicked:

    try:

        preview_events = ingest_git_history(
            git_repo_path,
            max_commits=git_max_commits,
            since=git_since or None,
        )

    except GitIngestError as e:

        st.error(
            f"Could not read git history: {e}"
        )

        preview_events = None

    if preview_events is not None:

        st.info(
            f"Found {len(preview_events)} real commit(s) to ingest."
        )

        progress_bar = st.progress(
            0,
            text="Starting...",
        )

        result = seed_git_history_into_hindsight(
            git_repo_path,
            max_commits=git_max_commits,
            since=git_since or None,
        )

        status = result["status"]

        while not status["finished"]:

            done = status["done"]
            total = status["total"]

            pct = (
                int(100 * done / total)
                if total
                else 0
            )

            progress_bar.progress(
                min(pct, 100),
                text=(
                    f"Seeding real git history: "
                    f"{done}/{total}"
                ),
            )

            time.sleep(0.2)

        progress_bar.progress(
            100,
            text=(
                f"Seeding real git history: "
                f"{status['done']}/{status['total']}"
            ),
        )

        if status["errors"]:

            st.error(
                f"Completed with "
                f"{len(status['errors'])} error(s):"
            )

            for e in status["errors"]:

                st.text(
                    f"  - {e}"
                )

        else:

            st.success(
                f"Seeded "
                f"{status['done']} real commit(s) "
                f"into Hindsight."
            )


st.divider()


# ---------------------------------------------------------------------
# 3. PROPOSE A CHANGE
# ---------------------------------------------------------------------

st.header("Propose a Change")


proposed_change = st.text_area(
    "Describe a proposed code or architecture change:",
    value=DEFAULT_DEMO_PROPOSAL,
    height=80,
)


audit_clicked = st.button(
    "Audit This Change",
    type="primary",
)


# ---------------------------------------------------------------------
# AUDIT
# ---------------------------------------------------------------------

if audit_clicked and proposed_change.strip():

    with st.spinner(
        "Recalling historical context and auditing..."
    ):

        result = audit_change(
            proposed_change
        )

    st.session_state.audit_result = result

    st.session_state.timeline.append(
        {
            "stage": "Proposal",
            "detail": proposed_change,
        }
    )

    st.session_state.timeline.append(
        {
            "stage": "Audit",
            "detail": f"Risk: {result.risk_level}",
        }
    )


st.divider()


# ---------------------------------------------------------------------
# GET STORED AUDIT RESULT
# ---------------------------------------------------------------------

result = st.session_state.audit_result


if result:

    # -----------------------------------------------------------------
    # 4. HISTORICAL MEMORY
    # -----------------------------------------------------------------

    st.header("Historical Memory")


    if result.reasoning_mode == "hindsight_unavailable":

        st.error(
            result.memory_status_message
        )


    elif result.reasoning_mode == "no_memory":

        st.warning(
            result.memory_status_message
        )


    elif result.evidence:

        for i, ev in enumerate(
            result.evidence,
            1,
        ):

            with st.container(
                border=True
            ):

                st.markdown(
                    f"**Evidence {i}**"
                )

                st.write(
                    ev
                )


    else:

        st.info(
            "No relevant historical memory was found."
        )


    st.divider()


    # -----------------------------------------------------------------
    # 5. COGNITIVE DEBT AUDIT
    # -----------------------------------------------------------------

    st.header(
        "Cognitive Debt Audit"
    )


    if result.reasoning_mode == "fallback_no_llm":

        st.warning(
            "LLM unavailable — showing retrieved-evidence "
            "fallback, not AI reasoning."
        )


    risk_color = {
        "HIGH": "🔴",
        "MEDIUM": "🟠",
        "LOW": "🟢",
        "UNKNOWN": "⚪",
    }


    st.subheader(
        f"{risk_color.get(result.risk_level, '⚪')} "
        f"Risk Level: {result.risk_level}"
    )


    # -----------------------------------------------------------------
    # HISTORICAL WHY
    # -----------------------------------------------------------------

    if result.historical_why:

        st.markdown(
            "**Historical Why**"
        )

        st.write(
            result.historical_why
        )


    # -----------------------------------------------------------------
    # PREVIOUS INCIDENTS
    # -----------------------------------------------------------------

    if result.previous_incidents:

        st.markdown(
            "**Previous Incidents**"
        )

        for incident in result.previous_incidents:

            st.markdown(
                f"- {incident}"
            )


    # -----------------------------------------------------------------
    # ARCHITECTURAL CONSTRAINTS
    # -----------------------------------------------------------------

    if result.architectural_constraints:

        st.markdown(
            "**Architectural Constraints**"
        )

        for constraint in result.architectural_constraints:

            st.markdown(
                f"- {constraint}"
            )


    # -----------------------------------------------------------------
    # POTENTIAL CONSEQUENCES
    # -----------------------------------------------------------------

    if result.potential_consequences:

        st.markdown(
            "**Potential Risk**"
        )

        st.write(
            result.potential_consequences
        )


    # -----------------------------------------------------------------
    # RECOMMENDED CHECKS
    # -----------------------------------------------------------------

    if result.recommended_checks:

        st.markdown(
            "**Recommended Checks**"
        )

        for check in result.recommended_checks:

            st.markdown(
                f"- {check}"
            )


    # -----------------------------------------------------------------
    # FINAL RECOMMENDATION
    # -----------------------------------------------------------------

    if result.recommendation:

        st.markdown(
            "**Final Recommendation**"
        )

        st.info(
            result.recommendation
        )


    st.divider()


    # -----------------------------------------------------------------
    # 6. MEMORY VS NO MEMORY
    # -----------------------------------------------------------------

    st.header(
        "Memory vs. No Memory"
    )


    no_mem_col, with_mem_col = st.columns(
        2
    )


    with no_mem_col:

        st.markdown(
            "**WITHOUT MEMORY**"
        )

        st.error(
            "Legacy adapter appears unnecessary. "
            "Consider replacing it with the new OAuth flow."
        )


    with with_mem_col:

        st.markdown(
            "**WITH HINDSIGHT**"
        )

        if result.evidence:

            st.success(
                result.recommendation
                or
                "See audit above — grounded in retrieved history."
            )

        else:

            st.info(
                "No grounded comparison available — "
                "no memory was retrieved for this proposal."
            )


    st.divider()


    # -----------------------------------------------------------------
    # 7. RECORD OUTCOME
    # -----------------------------------------------------------------

    st.header(
        "Record Outcome"
    )


    outcome_choice = st.radio(
        "What happened?",
        [
            "Change rejected",
            "Change approved with safeguards",
            "Change successfully completed",
            "Change caused a problem",
        ],
        index=None,
    )


    outcome_notes = st.text_area(
        "Notes (optional)",
        height=60,
    )


    reflect_clicked = st.button(
        "Reflect & Remember",
        type="primary",
    )


    if reflect_clicked:

        if not outcome_choice:

            st.warning(
                "Select an outcome first."
            )

        else:

            with st.spinner(
                "Generating lesson and storing in Hindsight..."
            ):

                refl = reflect_and_store(
                    proposal=result.proposed_change,
                    evidence=result.evidence,
                    audit_result=result,
                    outcome=outcome_choice,
                    notes=outcome_notes,
                )

            st.session_state.reflection_result = refl

            st.session_state.timeline.append(
                {
                    "stage": "Outcome",
                    "detail": outcome_choice,
                }
            )

            if refl.status == "stored":

                st.session_state.timeline.append(
                    {
                        "stage": "New Lesson",
                        "detail": refl.lesson,
                    }
                )


    st.divider()


    # -----------------------------------------------------------------
    # 8. REFLECTION
    # -----------------------------------------------------------------

    refl = st.session_state.reflection_result


    if refl:

        st.header(
            "What the Agent Learned"
        )

        st.write(
            refl.lesson
        )


        if refl.status == "stored":

            st.success(
                "Stored as procedural memory."
            )

        else:

            st.error(
                refl.message
            )


        st.divider()


# ---------------------------------------------------------------------
# 9. MEMORY TIMELINE
# ---------------------------------------------------------------------

if st.session_state.timeline:

    st.header(
        "Memory Timeline"
    )

    for step in st.session_state.timeline:

        st.markdown(
            f"**{step['stage']}** → "
            f"{step['detail']}"
        )


st.divider()


# ---------------------------------------------------------------------
# OVERALL MEMORY SYNTHESIS
# ---------------------------------------------------------------------

if st.button(
    "What has this agent learned overall?"
):

    with st.spinner(
        "Synthesizing patterns across all retained history..."
    ):

        patterns = synthesize_patterns()

    st.markdown(
        "**Recurring patterns across project memory:**"
    )

    st.write(
        patterns
    )