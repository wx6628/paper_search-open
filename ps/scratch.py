"""Scratch space and clean-up for intermediate data.

Policy (applied automatically by the workstation scripts):
  data/tmp/<task>/   per-task scratch (PDF text dumps, page renders, figure crops before they are kept,
                     reasons files, pasted reference lists). Removed when the task ends:
                       - library.py queue done|fail <qid>  -> removes data/tmp/queue-<qid>
                       - search.py finalize                 -> removes its --reasons file
                       - scratch.py clean                   -> removes everything under data/tmp (end of a command)
                       - server start                       -> removes leftovers older than 24 h
  data/runs/*.json   candidate pools from search.py gather / fresh.py. Deleted once consumed
                     (library.py import, search.py finalize; --keep-run to retain); runs never imported
                     are pruned after RUN_RETENTION_DAYS. The search record, report and BibTeX stay.
  *.part             partial downloads are removed when a download fails.

CLI:
  python -I ps/scratch.py dir <task>        create and print data/tmp/<task>
  python -I ps/scratch.py clean [--older-than-hours H]   empty data/tmp (+ prune old runs, stray .part files)
  python -I ps/scratch.py drop <file>...    delete specific run/scratch files (only inside data/runs or data/tmp)
  python -I ps/scratch.py status            what is currently there
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = ROOT / "data" / "tmp"
RUNS = ROOT / "data" / "runs"
PDF_DIR = ROOT / "library" / "pdfs"
RUN_RETENTION_DAYS = 7


def task_dir(task: str) -> Path:
    d = TMP / safe(task)
    d.mkdir(parents=True, exist_ok=True)
    return d


def safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in str(name))[:80] or "task"


def _rm(p: Path) -> bool:
    try:
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
        else:
            return False
        return True
    except OSError as e:  # file in use (Windows) etc. -- never fail the calling command
        print(f"warning: could not remove {p}: {e}", file=sys.stderr)
        return False


def remove_task(task: str) -> bool:
    return _rm(TMP / safe(task))


def remove_if_inside(path: str | Path, base: Path) -> bool:
    """Delete a consumed intermediate file, but only if it lives under `base` (never user files)."""
    p = Path(path).resolve()
    return base.resolve() in p.parents and _rm(p)


def consume_run(path: str | Path) -> bool:
    return remove_if_inside(path, RUNS)


def prune_runs(days: float = RUN_RETENTION_DAYS) -> list[str]:
    cutoff = time.time() - days * 86400
    out = []
    if RUNS.exists():
        for p in RUNS.iterdir():
            if p.is_file() and p.stat().st_mtime < cutoff and _rm(p):
                out.append(p.name)
    return out


def clean(older_than_hours: float = 0) -> dict:
    cutoff = time.time() - older_than_hours * 3600
    removed = []
    if TMP.exists():
        for p in TMP.iterdir():
            if p.stat().st_mtime <= cutoff and _rm(p):
                removed.append(p.name)
    parts = [p.name for p in PDF_DIR.glob("*.part") if p.stat().st_mtime <= cutoff and _rm(p)] if PDF_DIR.exists() else []
    return {"tmp_removed": removed, "runs_pruned": prune_runs(), "partial_downloads_removed": parts}


def status() -> dict:
    def size(p: Path) -> int:
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.is_dir() else p.stat().st_size
    return {"tmp": {p.name: size(p) for p in TMP.iterdir()} if TMP.exists() else {},
            "runs": sorted(p.name for p in RUNS.iterdir()) if RUNS.exists() else [],
            "partial_downloads": [p.name for p in PDF_DIR.glob("*.part")] if PDF_DIR.exists() else []}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("dir"); s.add_argument("task")
    s = sub.add_parser("clean"); s.add_argument("--older-than-hours", type=float, default=0)
    s = sub.add_parser("drop"); s.add_argument("files", nargs="+")
    sub.add_parser("status")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if a.cmd == "dir":
        print(task_dir(a.task))
    elif a.cmd == "drop":   # only files inside data/runs or data/tmp can be dropped
        print(json.dumps({f: remove_if_inside(f, RUNS) or remove_if_inside(f, TMP) for f in a.files}, ensure_ascii=False))
    elif a.cmd == "clean":
        print(json.dumps(clean(a.older_than_hours), ensure_ascii=False))
    else:
        print(json.dumps(status(), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
