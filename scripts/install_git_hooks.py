"""
Install Git Hooks for Automatic Reflection
------------------------------------------------
Writes a real `post-merge` (and optionally `post-commit`) hook into the
target repo's .git/hooks/ directory. The hook shells out to
scripts/auto_reflect.py, which runs automatically every time a real merge
(or commit) actually happens in that repo — no manual "Reflect & Remember"
click required.

This is the wiring piece for "automatic learning after actual Git/PR
events": it makes the trigger real (a real git event) rather than a
developer remembering to record an outcome by hand.

Usage:
    python scripts/install_git_hooks.py --repo-path /path/to/repo
    python scripts/install_git_hooks.py --repo-path /path/to/repo --hook post-commit
    python scripts/install_git_hooks.py --repo-path /path/to/repo --uninstall
"""

import argparse
import stat
import sys
from pathlib import Path

HOOK_MARKER = "# installed-by: cognitive-debt-auditor auto_reflect"

HOOK_TEMPLATE = """#!/bin/sh
{marker}
# Runs scripts/auto_reflect.py automatically after a real {hook_name} event.
# Never blocks or fails the git operation itself — errors here are logged,
# not raised, so a Hindsight outage never breaks a developer's merge/commit.

AUDITOR_DIR="{auditor_dir}"

python3 "$AUDITOR_DIR/scripts/auto_reflect.py" --event {hook_name} --repo-path "$AUDITOR_DIR" \
    || echo "[auto_reflect] warning: automatic reflection failed (see above) — {hook_name} still completed normally."

exit 0
"""


def install(repo_path: str, hook_name: str, auditor_dir: str) -> Path:
    git_dir = Path(repo_path).resolve() / ".git"
    if not git_dir.exists():
        raise SystemExit(f"'{repo_path}' has no .git directory — is this a real git repo?")

    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_path = hooks_dir / hook_name

    if hook_path.exists() and HOOK_MARKER not in hook_path.read_text():
        backup = hook_path.with_suffix(hook_path.suffix + ".bak")
        hook_path.rename(backup)
        print(f"Existing {hook_name} hook backed up to {backup}")

    content = HOOK_TEMPLATE.format(
        marker=HOOK_MARKER, hook_name=hook_name, auditor_dir=Path(auditor_dir).resolve(),
    )
    hook_path.write_text(content)
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return hook_path


def uninstall(repo_path: str, hook_name: str) -> bool:
    hook_path = Path(repo_path).resolve() / ".git" / "hooks" / hook_name
    if hook_path.exists() and HOOK_MARKER in hook_path.read_text():
        hook_path.unlink()
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Install/uninstall the auto-reflect git hook.")
    parser.add_argument("--repo-path", default=".", help="Repo to install the hook into (its history is what gets reflected on).")
    parser.add_argument("--hook", default="post-merge", choices=["post-merge", "post-commit"])
    parser.add_argument("--auditor-dir", default=str(Path(__file__).resolve().parent.parent),
                         help="Path to this project (defaults to its own location).")
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()

    if args.uninstall:
        removed = uninstall(args.repo_path, args.hook)
        print(f"{'Removed' if removed else 'No installed hook found to remove:'} {args.hook}")
        return 0

    hook_path = install(args.repo_path, args.hook, args.auditor_dir)
    print(f"Installed: {hook_path}")
    print(f"Every real '{args.hook}' in '{args.repo_path}' will now automatically trigger "
          f"scripts/auto_reflect.py and retain a lesson into Hindsight — no manual step needed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
