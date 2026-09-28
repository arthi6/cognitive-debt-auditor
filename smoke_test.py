"""
Local smoke test — verifies your actual running environment, not mocks.
Run with: python smoke_test.py

Unlike tests/test_core.py (which mocks Hindsight/OpenAI so it runs
anywhere), this script checks the real things: can we import everything,
is config present, is Hindsight actually reachable at HINDSIGHT_BASE_URL,
does the bank respond, and (if OPENAI_API_KEY is set) does a basic
retain/recall round-trip work.

Exits non-zero if any REQUIRED check fails. LLM checks are informational
only, since the app has a documented fallback mode without an LLM.
"""

import sys


def check(label, fn, required=True):
    try:
        detail = fn()
        print(f"[PASS] {label}" + (f" — {detail}" if detail else ""))
        return True
    except Exception as e:
        level = "FAIL" if required else "WARN"
        print(f"[{level}] {label} — {e}")
        return not required


def main():
    ok = True

    def imports():
        import config, context_ingestor, git_ingestor, memory_retriever, audit_agent, reflection_engine, app  # noqa
        return None
    ok &= check("Imports (all project modules)", imports)

    def git_binary_available():
        import subprocess
        out = subprocess.run(["git", "--version"], capture_output=True, text=True, check=True)
        return out.stdout.strip()
    ok &= check("git binary available (required for git_ingestor.py)", git_binary_available)

    def env_config():
        from config import HINDSIGHT_BASE_URL, BANK_ID
        if not HINDSIGHT_BASE_URL or not BANK_ID:
            raise RuntimeError("HINDSIGHT_BASE_URL or HINDSIGHT_BANK_ID not set")
        return f"base_url={HINDSIGHT_BASE_URL}, bank_id={BANK_ID}"
    ok &= check("Environment configuration", env_config)

    def dataset():
        from context_ingestor import load_dataset
        events = load_dataset()
        return f"{len(events)} events"
    ok &= check("dataset.json valid and loadable", dataset)

    def hindsight_connectivity():
        from hindsight_client import Hindsight
        from config import HINDSIGHT_BASE_URL
        client = Hindsight(base_url=HINDSIGHT_BASE_URL)
        return "client initialized"
    ok &= check("Hindsight client initializes", hindsight_connectivity)

    def bank_reachable():
        from hindsight_client import Hindsight
        from config import HINDSIGHT_BASE_URL, BANK_ID
        client = Hindsight(base_url=HINDSIGHT_BASE_URL)
        client.recall(bank_id=BANK_ID, query="smoke test connectivity check")
        return f"bank '{BANK_ID}' responded"
    ok &= check("Hindsight bank reachable (recall round-trip)", bank_reachable)

    def llm_configured():
        from config import LLM_AVAILABLE, OPENAI_MODEL
        if not LLM_AVAILABLE:
            raise RuntimeError("OPENAI_API_KEY not set — app will run in fallback mode")
        return f"model={OPENAI_MODEL}"
    check("LLM configured (optional — fallback mode covers this)", llm_configured, required=False)

    def retain_recall_roundtrip():
        from hindsight_client import Hindsight
        from config import HINDSIGHT_BASE_URL, BANK_ID
        client = Hindsight(base_url=HINDSIGHT_BASE_URL)
        client.retain(bank_id=BANK_ID, content="smoke_test: connectivity marker")
        client.recall(bank_id=BANK_ID, query="smoke_test connectivity marker")
        return "retain + recall succeeded"
    check("Basic retain/recall round-trip", retain_recall_roundtrip, required=False)

    print()
    if ok:
        print("Smoke test passed. Required checks are green.")
        sys.exit(0)
    else:
        print("Smoke test FAILED. Fix the FAIL items above before demoing.")
        sys.exit(1)


if __name__ == "__main__":
    main()
