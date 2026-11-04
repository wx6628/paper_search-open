"""Shared network layer for workstation scripts: keys, rate limits, and a usage ledger.

Every online request from workstation scripts must go through `call()`:
  * loads keys from <project>/.env, falling back to environment variables (values are never printed)
  * enforces per-second / per-minute pacing from config/api_limits.yaml (limit_* values)
  * keeps a persistent ledger (data/usage.sqlite) and refuses calls past daily / weekly limits
  * handles 429 (honours Retry-After, retries at most once) and disables a source for the
    process on human-verification challenges
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"          # copy .env.example to .env and fill in; .env is git-ignored
LIMITS_FILE = ROOT / "config" / "api_limits.yaml"
LEDGER = ROOT / "data" / "usage.sqlite"
UA = "paper-search-open/0.1 (academic literature search)"

_env: dict | None = None
_limits: dict | None = None
_last_call: dict[str, float] = {}
_minute_calls: dict[str, list[float]] = {}
_disabled: dict[str, str] = {}
_remaining_usd: dict[str, float] = {}


class QuotaExceeded(RuntimeError):
    pass


class SourceDisabled(RuntimeError):
    pass


def env(name: str, default: str = "") -> str:
    global _env
    if _env is None:
        _env = {}
        if ENV_FILE.exists():
            for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    _env[k.strip()] = v.strip().strip('"').strip("'")
    return _env.get(name) or os.environ.get(name) or default


def limits(source: str) -> dict:
    global _limits
    if _limits is None:
        import yaml
        _limits = yaml.safe_load(LIMITS_FILE.read_text(encoding="utf-8")) or {}
    return _limits.get(source) or {}


def _ledger() -> sqlite3.Connection:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(LEDGER)
    con.execute("""CREATE TABLE IF NOT EXISTS calls (
        ts REAL, day TEXT, source TEXT, kind TEXT, status INTEGER, cost_usd REAL)""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_calls ON calls(source, day)")
    return con


def usage(source: str, days: int = 1, kind: str | None = None) -> tuple[int, float]:
    """(calls, usd) recorded for `source` over the last `days` calendar days (UTC)."""
    since = (dt.datetime.utcnow().date() - dt.timedelta(days=days - 1)).isoformat()
    q = "SELECT COUNT(*), IFNULL(SUM(cost_usd),0) FROM calls WHERE source=? AND day>=?"
    args = [source, since]
    if kind:
        q += " AND kind=?"; args.append(kind)
    with _ledger() as con:
        return con.execute(q, args).fetchone()


def _check_budget(source: str, kind: str | None):
    lim = limits(source)
    if source in _disabled:
        raise SourceDisabled(f"{source} disabled for this run: {_disabled[source]}")
    checks = [("limit_daily", 1, None), ("limit_weekly", 7, None)]
    if kind:
        checks += [(f"limit_weekly_{kind}", 7, kind), (f"limit_daily_{kind}", 1, kind)]
    for field, days, k in checks:
        cap = lim.get(field)
        if isinstance(cap, (int, float)):
            n, _ = usage(source, days, k)
            if n >= cap:
                raise QuotaExceeded(f"{source}: {field}={cap} reached ({n} calls)")
    cap_usd = lim.get("limit_daily_usd")
    if isinstance(cap_usd, (int, float)):
        _, usd = usage(source, 1)
        # the server-side remaining budget also counts spending by other tools that use the same key
        total = (lim.get("official") or {}).get("daily_usd")
        if source in _remaining_usd and total:
            usd = max(usd, float(total) - _remaining_usd[source])
        if usd >= cap_usd:
            raise QuotaExceeded(f"{source}: limit_daily_usd={cap_usd} reached (${usd:.4f} used today)")


def _pace(source: str):
    lim = limits(source)
    gap = 0.0
    if lim.get("limit_per_second"):
        gap = max(gap, 1.0 / float(lim["limit_per_second"]))
    if lim.get("limit_min_interval_seconds"):
        gap = max(gap, float(lim["limit_min_interval_seconds"]))
    wait = _last_call.get(source, 0) + gap - time.time()
    if wait > 0:
        time.sleep(wait)
    per_min = lim.get("limit_per_minute")
    if per_min:
        window = [t for t in _minute_calls.get(source, []) if t > time.time() - 60]
        if len(window) >= per_min:
            time.sleep(60 - (time.time() - window[0]) + 0.1)
        _minute_calls[source] = window
    _last_call[source] = time.time()
    _minute_calls.setdefault(source, []).append(time.time())


def call(source: str, url: str, params: dict | None = None, headers: dict | None = None,
         kind: str | None = None, cost_usd: float = 0.0, timeout: int = 40) -> dict | str:
    """GET `url` for `source` (a key in api_limits.yaml) with pacing + budget checks.
    `kind` selects sub-budgets such as scopus 'search'. Returns parsed JSON (or text)."""
    _check_budget(source, kind)
    params = {k: v for k, v in (params or {}).items() if v not in (None, "")}   # unset keys: omit, don't send empty
    full = url + ("?" + urllib.parse.urlencode(params) if params else "")
    for attempt in range(2):
        _pace(source)
        req = urllib.request.Request(full, headers={"User-Agent": UA, **(headers or {})})
        status, body, hdrs = 0, "", {}
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                status, hdrs, body = r.status, r.headers, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            status, hdrs, body = e.code, e.headers, e.read().decode("utf-8", "replace")
        real_cost = cost_usd
        try:  # OpenAlex reports the true cost of each call and the server-side remaining budget
            real_cost = float(hdrs.get("X-RateLimit-Cost-USD", cost_usd))
            if hdrs.get("X-RateLimit-Remaining-USD") is not None:
                _remaining_usd[source] = float(hdrs["X-RateLimit-Remaining-USD"])
        except (TypeError, ValueError):
            pass
        with _ledger() as con:
            con.execute("INSERT INTO calls VALUES (?,?,?,?,?,?)",
                        (time.time(), dt.datetime.utcnow().date().isoformat(), source, kind, status,
                         real_cost if status < 400 else 0.0))
        low = body[:2000].lower()
        if status == 403 and ("challenge" in low or "captcha" in low or "verification" in low):
            _disabled[source] = "human-verification challenge"
            raise SourceDisabled(f"{source}: human-verification challenge; source disabled (no retry)")
        if status == 429 and attempt == 0:
            retry = hdrs.get("Retry-After")
            time.sleep(min(float(retry) if retry and retry.replace(".", "").isdigit() else 5.0, 60.0))
            continue
        if status == 429:
            _disabled[source] = "repeated 429"
            raise QuotaExceeded(f"{source}: 429 twice; source disabled for this run")
        if status >= 400:
            raise RuntimeError(f"{source}: HTTP {status}: {body[:200]}")
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return body
    raise RuntimeError("unreachable")


def download(source: str, url: str, dest: Path, max_mb: int = 80, timeout: int = 90) -> Path:
    """Download a binary file (e.g. an open-access PDF) with the same pacing / budget / ledger rules.
    Only keeps the file if it really is a PDF (starts with %PDF-)."""
    _check_budget(source, "pdf")
    _pace(source)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/pdf,*/*;q=0.8"})
    status = 0
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status = r.status
            head = r.read(5)
            if head != b"%PDF-":
                raise RuntimeError(f"{source}: not a PDF (got {head!r}) from {url[:80]}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(".part")
            try:
                with open(tmp, "wb") as f:
                    f.write(head)
                    total = 5
                    while chunk := r.read(1 << 16):
                        total += len(chunk)
                        if total > max_mb * 1e6:
                            raise RuntimeError(f"{source}: PDF larger than {max_mb} MB, skipped")
                        f.write(chunk)
                tmp.replace(dest)
            except BaseException:
                tmp.unlink(missing_ok=True)   # never leave a partial download behind
                raise
    except urllib.error.HTTPError as e:
        status = e.code
        raise RuntimeError(f"{source}: HTTP {e.code} for {url[:80]}") from None
    finally:
        with _ledger() as con:
            con.execute("INSERT INTO calls VALUES (?,?,?,?,?,?)",
                        (time.time(), dt.datetime.utcnow().date().isoformat(), source, "pdf", status, 0.0))
    return dest


def report() -> str:
    """Human-readable usage summary for today (UTC) and the last 7 days."""
    with _ledger() as con:
        rows = con.execute("""SELECT source, kind,
              SUM(day = date('now')) , SUM(cost_usd * (day = date('now'))),
              SUM(day >= date('now','-6 days')) FROM calls GROUP BY source, kind ORDER BY source""").fetchall()
    lines = [f"{'source':<20}{'kind':<10}{'today':>7}{'usd today':>11}{'7 days':>8}"]
    for s, k, t, usd, w in rows:
        lines.append(f"{s:<20}{(k or ''):<10}{t or 0:>7}{(usd or 0):>11.4f}{w or 0:>8}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(report())
