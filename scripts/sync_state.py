#!/usr/bin/env python3
"""Durably checkpoint only runtime/state.json in the current Actions repository.

The publisher calls this after its atomic local write, including BEFORE an
irreversible media_publish request. Any failure stops the publisher. This hook
never merges, resets, force-pushes, exposes Git output, or stages other files.
The workflow must serialize writers and check out the configured default branch.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys


STATE_RELATIVE = "runtime/state.json"
MAX_STATE_BYTES = 2 * 1024 * 1024


class SyncError(RuntimeError):
    """A deliberately generic error safe to report to the caller."""


def git(arguments: list[str], *, cwd: Path, environment: dict[str, str],
        allowed: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", *arguments],
            cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90,
        )
    except (OSError, subprocess.SubprocessError):
        raise SyncError("Git checkpoint operation did not complete.") from None
    if result.returncode not in allowed:
        raise SyncError("Git checkpoint operation was not confirmed.")
    return result


def output(result: subprocess.CompletedProcess) -> str:
    try:
        return result.stdout.decode("utf-8").strip()
    except UnicodeDecodeError:
        raise SyncError("Unexpected Git checkpoint response.") from None


def checked_state(path: Path, root: Path) -> bytes:
    expected = root / STATE_RELATIVE
    if path != expected or expected.is_symlink() or expected.parent.is_symlink():
        raise SyncError("Only the repository's regular runtime/state.json can be synchronized.")
    try:
        details = expected.stat(follow_symlinks=False)
        if (not stat.S_ISREG(details.st_mode) or details.st_uid != os.getuid()
                or details.st_mode & 0o077 or details.st_size > MAX_STATE_BYTES):
            raise SyncError("State must be an owned, private, bounded regular file.")
        with expected.open("rb") as stream:
            content = stream.read(MAX_STATE_BYTES + 1)
        value = json.loads(content)
    except (OSError, ValueError):
        raise SyncError("State is unavailable or invalid.") from None
    if len(content) > MAX_STATE_BYTES or not isinstance(value, dict):
        raise SyncError("State is unavailable or invalid.")
    if value.get("schema_version") != 1 or not isinstance(value.get("pieces"), dict):
        raise SyncError("State has an unsupported format.")
    return content


def sync_state(argument: str) -> None:
    environment = os.environ.copy()
    if environment.get("GITHUB_ACTIONS") != "true":
        raise SyncError("This checkpoint hook runs only in the configured Actions workflow.")
    # The workflow passes its repository.default_branch through this dedicated
    # setting, without overwriting GitHub's reserved context variables.
    branch = environment.get("ACCIOLY_STATE_BRANCH") or environment.get("GITHUB_REF_NAME", "")
    if not branch or "\x00" in branch or "\n" in branch or "\r" in branch:
        raise SyncError("The workflow branch is missing or invalid.")
    # Preserve checkout's Git authentication, but never pass the Meta token to
    # Git or allow inherited Git tracing to write credentials into log files.
    environment.pop("IG_ACCESS_TOKEN", None)
    for key in list(environment):
        if key.startswith("GIT_TRACE") or key in ("GIT_CURL_VERBOSE", "GIT_TRACE_CURL"):
            environment.pop(key, None)
    environment["GIT_TERMINAL_PROMPT"] = "0"
    cwd = Path.cwd()
    root_text = output(git(["rev-parse", "--show-toplevel"], cwd=cwd, environment=environment))
    root = Path(root_text).resolve(strict=True)
    git(["check-ref-format", "--branch", branch], cwd=root, environment=environment)
    # A normalized absolute argument is supplied by publisher.py. Reject an
    # alternate path rather than resolving a symlink to the authorized file.
    state = Path(os.path.abspath(argument))
    initial = checked_state(state, root)
    staged = output(git(["diff", "--cached", "--name-only", "-z"], cwd=root,
                        environment=environment))
    if staged:
        raise SyncError("A pre-existing staged change prevents checkpoint synchronization.")
    local_head = output(git(["rev-parse", "HEAD"], cwd=root, environment=environment))
    target_ref = "refs/heads/" + branch
    git(["fetch", "--no-tags", "origin", target_ref], cwd=root, environment=environment)
    remote_head = output(git(["rev-parse", "FETCH_HEAD"], cwd=root, environment=environment))
    if local_head != remote_head:
        raise SyncError("The remote branch moved; checkpoint synchronization requires reconciliation.")
    if hashlib.sha256(checked_state(state, root)).digest() != hashlib.sha256(initial).digest():
        raise SyncError("State changed during checkpoint synchronization.")
    git(["add", "--force", "--", STATE_RELATIVE], cwd=root, environment=environment)
    staged = output(git(["diff", "--cached", "--name-only", "-z"], cwd=root,
                        environment=environment))
    if staged and staged != STATE_RELATIVE + "\x00":
        raise SyncError("Unexpected staged files prevent checkpoint synchronization.")
    changed = git(["diff", "--cached", "--quiet", "--", STATE_RELATIVE],
                  cwd=root, environment=environment, allowed=(0, 1)).returncode == 1
    if changed:
        git(["-c", "user.name=Accioly Publisher", "-c",
             "user.email=41898282+github-actions[bot]@users.noreply.github.com",
             "commit", "--no-verify", "-m", "Checkpoint do publicador Accioly",
             "--", STATE_RELATIVE], cwd=root, environment=environment)
        git(["push", "--porcelain", "origin", "HEAD:" + target_ref],
            cwd=root, environment=environment)
    final_head = output(git(["rev-parse", "HEAD"], cwd=root, environment=environment))
    remote_lines = output(git(["ls-remote", "--exit-code", "--heads", "origin", target_ref],
                              cwd=root, environment=environment)).splitlines()
    if len(remote_lines) != 1 or remote_lines[0].split() != [final_head, target_ref]:
        raise SyncError("The remote checkpoint was not confirmed.")
    committed = git(["show", "HEAD:" + STATE_RELATIVE], cwd=root, environment=environment).stdout
    if committed != initial:
        raise SyncError("The remote commit does not contain the intended checkpoint.")


def main() -> int:
    if len(sys.argv) != 2:
        print("Checkpoint synchronization requires exactly one state path.", file=sys.stderr)
        return 2
    try:
        sync_state(sys.argv[1])
    except (SyncError, OSError, ValueError):
        # Provider output, repository URLs, credentials, state contents, and
        # exceptions are intentionally omitted from both stdout and stderr.
        print("State checkpoint synchronization was not confirmed; publication must stop.", file=sys.stderr)
        return 2
    print("State checkpoint confirmed on the configured remote branch.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
