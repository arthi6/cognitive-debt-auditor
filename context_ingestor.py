"""
Context Ingestor
------------------
Loads dataset.json (synthetic engineering history) and retains each event
into Hindsight. Also owns the "don't re-seed on every Streamlit rerun"
mechanism via a local sentinel file (.seed_state.json).

Note on async retain: Hindsight's server-side async retain (operation_id +
polling) has had version-dependent bugs in the official client (see
project history in the repo's public issue tracker). Rather than depend on
a specific server behavior that may differ across Hindsight versions, this
seeds using a background thread on the client side, with per-item progress
reported back to the caller via a callback. This gets you the required
UX (non-frozen UI, visible progress, graceful error handling) without
betting the demo on an API detail that has been unstable across releases.
If your installed Hindsight version's async retain is confirmed stable,
swap seed_into_hindsight's inner loop for the native async+poll pattern —
the call sites in app.py don't need to change.
"""

import hashlib
import json
import threading
import time
from pathlib import Path

from config import HINDSIGHT_BASE_URL, BANK_ID, DATASET_PATH, SEED_STATE_PATH


def load_dataset(path: str = DATASET_PATH) -> list[dict]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"{path} not found. This file must contain the synthetic engineering "
            f"history events. See README for the expected schema."
        )
    data = json.loads(p.read_text())
    if not isinstance(data, list) or not data:
        raise ValueError(f"{path} must contain a non-empty JSON array of events.")
    return data


def _dataset_fingerprint(path: str = DATASET_PATH) -> str:
    """Hash of dataset contents + bank id, used to detect 'already seeded'."""
    content = Path(path).read_bytes()
    return hashlib.sha256(content + BANK_ID.encode()).hexdigest()


def is_already_seeded(path: str = DATASET_PATH, state_path: str = SEED_STATE_PATH) -> bool:
    state_file = Path(state_path)
    if not state_file.exists():
        return False
    try:
        state = json.loads(state_file.read_text())
    except json.JSONDecodeError:
        return False
    return state.get("fingerprint") == _dataset_fingerprint(path) and state.get("status") == "complete"


def _mark_seeded(fingerprint: str, count: int, state_path: str = SEED_STATE_PATH):
    Path(state_path).write_text(json.dumps({
        "fingerprint": fingerprint,
        "status": "complete",
        "count": count,
        "seeded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "bank_id": BANK_ID,
    }, indent=2))


def format_event_for_retain(event: dict) -> str:
    """Turn one dataset event into a single retain-able content string."""
    parts = [
        f"[{event.get('type', 'event')}] {event.get('title', '')} ({event.get('date', '')})",
        f"Description: {event.get('description', '')}",
    ]
    if event.get("decision"):
        parts.append(f"Decision: {event['decision']}")
    if event.get("constraints"):
        parts.append(f"Constraints: {event['constraints']}")
    if event.get("outcome"):
        parts.append(f"Outcome: {event['outcome']}")
    if event.get("related_files"):
        parts.append(f"Related files: {', '.join(event['related_files'])}")
    return " | ".join(parts)


def seed_into_hindsight(
    dataset_path: str = DATASET_PATH,
    force: bool = False,
    progress_callback=None,
) -> dict:
    """
    Retain every dataset event into Hindsight, in a background thread so the
    caller (Streamlit) doesn't block. progress_callback(done, total, item_title)
    is called after each item — use it to drive a UI progress bar.

    Returns a result dict immediately containing a `thread` handle and a
    mutable `status` dict the caller can poll (status["done"], status["total"],
    status["errors"], status["finished"]).
    """
    from hindsight_client import Hindsight

    fingerprint = _dataset_fingerprint(dataset_path)
    if not force and is_already_seeded(dataset_path):
        status = {"done": 0, "total": 0, "errors": [], "finished": True, "skipped": True}
        return {"thread": None, "status": status}

    events = load_dataset(dataset_path)
    status = {"done": 0, "total": len(events), "errors": [], "finished": False, "skipped": False}

    def _worker():
        try:
            client = Hindsight(base_url=HINDSIGHT_BASE_URL)
        except Exception as e:
            status["errors"].append(f"Could not initialize Hindsight client: {e}")
            status["finished"] = True
            return

        for event in events:
            content = format_event_for_retain(event)
            try:
                client.retain(bank_id=BANK_ID, content=content)
            except Exception as e:
                status["errors"].append(f"{event.get('id', '?')}: {e}")
            status["done"] += 1
            if progress_callback:
                try:
                    progress_callback(status["done"], status["total"], event.get("title", ""))
                except Exception:
                    pass  # never let a UI callback crash ingestion

        if not status["errors"]:
            _mark_seeded(fingerprint, status["done"])
        status["finished"] = True

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    return {"thread": thread, "status": status}


if __name__ == "__main__":
    result = seed_into_hindsight(force=True)
    while not result["status"]["finished"]:
        time.sleep(0.5)
    s = result["status"]
    if s.get("skipped"):
        print("Already seeded — no changes made.")
    else:
        print(f"Seeded {s['done']}/{s['total']} events. Errors: {len(s['errors'])}")
        for e in s["errors"]:
            print(f"  - {e}")
