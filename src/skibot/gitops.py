"""Commit and push generated files back to the repository (only inside GitHub Actions)."""
from __future__ import annotations

import logging
import os
import subprocess

from .config import ROOT

log = logging.getLogger("skibot")


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, check=check, capture_output=True, text=True)


def commit_and_push(message: str, paths: list[str]) -> bool:
    if not os.getenv("GITHUB_ACTIONS"):
        log.info("Not running in GitHub Actions - skipping git push.")
        return False
    _git("config", "user.name", "ski-news-bot")
    _git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    _git("add", "--all", *paths)
    if _git("diff", "--cached", "--quiet", check=False).returncode == 0:
        return True  # nothing to commit
    _git("commit", "-m", message)
    last = ""
    for attempt in range(1, 4):
        _git("pull", "--rebase", "--autostash", check=False)
        res = _git("push", check=False)
        if res.returncode == 0:
            return True
        last = res.stderr
        log.warning("git push attempt %s failed: %s", attempt, last.strip()[:200])
    raise RuntimeError(f"git push failed after 3 attempts: {last[:300]}")
