"""Back up Ali's vault to a private GitHub repo with git: every change becomes a commit, so any
earlier version of any note can be recovered.

- Uses the git already installed and the GitHub sign-in git already has (Git Credential Manager).
- Only commits and pushes. Never force-pushes, resets, rebases or cleans: history is only ever added to.
- Git's files live in the vault's hidden .git folder; ignore rules go in .git/info/exclude, so no
  file is added among his notes. Obsidian and JARVIS both skip dot-folders.
- Runs when notes change (at most every BACKUP_EVERY) and when Ali asks.
"""
import os
import subprocess
import threading
import time

import clock
import data

REMOTE = os.environ.get("JARVIS_BACKUP_REMOTE", "").strip()
BACKUP_EVERY = 30 * 60
EXCLUDE = [".obsidian/workspace*.json", ".obsidian/cache", ".trash/", ".DS_Store", "*.tmp"]

_state = {"last": None, "last_try": 0.0, "error": "", "dirty": True}
_lock = threading.Lock()


def _git(*args, timeout=120):
    root = data.vault_root()
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode:
        raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout) else "git failed")
    return r.stdout.strip()


def enabled():
    return bool(REMOTE) and not data.DEMO and data.vault_root() is not None


def status():
    return {"enabled": enabled(), "remote": REMOTE.rsplit("/", 1)[-1].removesuffix(".git") if REMOTE else "",
            "last": _state["last"], "error": _state["error"]}


def mark_dirty():
    _state["dirty"] = True


def setup():
    """One-time: make the vault a git repo pointing at the private GitHub repo. Safe to re-run."""
    root = data.vault_root()
    if not (root / ".git").exists():
        _git("init", "-b", "main")
    excl = root / ".git" / "info" / "exclude"
    excl.parent.mkdir(parents=True, exist_ok=True)
    have = excl.read_text(encoding="utf-8") if excl.exists() else ""
    missing = [e for e in EXCLUDE if e not in have]
    if missing:
        excl.write_text(have.rstrip("\n") + "\n" + "\n".join(missing) + "\n", encoding="utf-8")
    remotes = _git("remote").split()
    if "origin" not in remotes:
        _git("remote", "add", "origin", REMOTE)


def run(reason="scheduled"):
    """Commit whatever changed and push. Returns a short human summary."""
    if not enabled():
        raise RuntimeError("Backup isn't set up: JARVIS_BACKUP_REMOTE is empty in .env.")
    with _lock:
        _state["last_try"] = time.time()
        try:
            setup()
            _git("add", "-A")
            changed = [l for l in _git("diff", "--cached", "--name-only").splitlines() if l.strip()]   # files, not folders
            if changed:
                _git("commit", "-q", "-m", f"Notes backup {clock.uk_now():%Y-%m-%d %H:%M} ({reason}, {len(changed)} files)")
            _git("push", "-q", "origin", "HEAD:main", timeout=180)
        except Exception as e:
            _state["error"] = str(e)
            raise
        _state.update(error="", dirty=False, last=clock.uk_now().strftime("%a %d %b %H:%M"))
        return f"{len(changed)} file{'s' if len(changed) != 1 else ''} backed up" if changed else "Already up to date"


def loop():
    """Background thread: back up when notes have changed, at most every BACKUP_EVERY."""
    while True:
        time.sleep(60)
        if enabled() and _state["dirty"] and time.time() - _state["last_try"] >= BACKUP_EVERY:
            try:
                run("notes changed")
            except Exception:
                pass            # reported through status(); retried next interval


def start():
    threading.Thread(target=loop, daemon=True, name="backup").start()
