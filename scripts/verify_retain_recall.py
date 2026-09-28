"""
Retain -> Recall Verification (real, unmocked)
--------------------------------------------------
tests/test_core.py mocks Hindsight entirely (so CI can run without a live
server). smoke_test.py's retain/recall round-trip only checks that the
calls don't raise — it never confirms recall actually returns what was
just retained.

This script closes that gap: it retains a UNIQUE probe event, then recalls
with a query built from that same probe, and checks whether the recalled
results actually reflect it.

IMPORTANT: Hindsight runs retained content through its own LLM and
restructures it (e.g. into "<description> | When: <date> | Involving:
<entities> | ..." fields) rather than storing your text verbatim. Anything
that looks like a labeled/bracketed token — "[tag] MARKER: ...", "(id)" —
tends to get parsed as a field and, if unrecognized as a real entity,
dropped ("Involving: none"). Plain flowing prose survives. So the probe
below is written as ONE continuous sentence with no brackets, no leading
tag, and no parenthetical id — and verification checks for substrings of
that prose (plus the timestamp, which has reliably survived across runs)
rather than a special "marker" token.

Run with:
    python scripts/verify_retain_recall.py

Requires: HINDSIGHT_BASE_URL reachable (see README's Docker setup) and
HINDSIGHT_BANK_ID configured, same as the rest of the app.

Exit code 0 = verified. Non-zero = retain->recall is NOT actually working,
even if individual calls don't throw.
"""

import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import HINDSIGHT_BASE_URL, BANK_ID  # noqa: E402


def main() -> int:
    from hindsight_client import Hindsight
    from memory_retriever import _normalize_recall_response

    probe_id = uuid.uuid4().hex[:10]
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # Plain prose, no brackets/tags/parenthetical ids — see module docstring
    # for why that matters here.
    probe_content = (
        f"On {timestamp} the cognitive debt auditor test suite retained "
        f"synthetic verification probe {probe_id} using the script "
        f"verify_retain_recall.py, solely to confirm that content retained "
        f"into Hindsight can be recalled back out successfully."
    )

    print(f"Target: {HINDSIGHT_BASE_URL}  bank: {BANK_ID}")
    print()

    print("[1/4] Connecting to Hindsight...")
    try:
        client = Hindsight(base_url=HINDSIGHT_BASE_URL)
    except Exception as e:
        print(f"[FAIL] Could not initialize Hindsight client: {e}")
        return 1
    print("       connected.")

    try:
        print(f"[2/4] Retaining probe event (id: {probe_id})...")
        try:
            client.retain(bank_id=BANK_ID, content=probe_content)
        except Exception as e:
            print(f"[FAIL] Retain call failed: {e}")
            return 1
        print("       retained.")

        print("[3/4] Waiting for indexing (2s)...")
        time.sleep(2)

        print("[4/4] Recalling with a query built from the probe content...")
        try:
            raw = client.recall(
                bank_id=BANK_ID,
                query=f"cognitive debt auditor verification probe {timestamp}",
            )
        except Exception as e:
            print(f"[FAIL] Recall call failed: {e}")
            return 1
    finally:
        try:
            client.close()
        except Exception:
            pass

    evidence = _normalize_recall_response(raw)

    # Multiple independent signals, any one of which is enough. All three
    # are plain prose fragments (no bracket/tag syntax) so none of them
    # should get stripped the way a labeled marker token does.
    signal_checks = [
        ("run timestamp", timestamp),
        ("probe id", probe_id),
        ("script name", "verify_retain_recall.py"),
    ]
    matched = [name for name, needle in signal_checks
               if any(needle in str(item) for item in evidence)]

    print()
    if matched:
        print(f"[PASS] Retain -> Recall verified. Matched via: {', '.join(matched)} "
              f"({len(evidence)} result(s) returned).")
        return 0

    print("[FAIL] Probe was retained but no matching signal was found on recall.")
    print(f"       Recall returned {len(evidence)} result(s):")
    for e in evidence[:5]:
        print(f"         - {str(e)[:200]}")
    print()
    print("       Retain succeeded but recall did not surface the new content at all.")
    print("       Likely causes: indexing delay (retry with a longer sleep),")
    print("       an embedding/LLM backend misconfiguration on the Hindsight")
    print("       server, or a bank_id mismatch between retain and recall calls.")
    return 1


if __name__ == "__main__":
    sys.exit(main())