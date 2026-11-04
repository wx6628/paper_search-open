"""Query the local dblp library (data/dblp.sqlite) — no network access.

Commands:
  search  <query>   full-text search on titles (stemmed), with venue / tier / year filters
  venue   <name>    find dblp venue keys by name or abbreviation (to fill config/venues.yaml)
  get     <id>      one record by dblp key, DOI, or arXiv id
  bib     <id>...   BibTeX in dblp style (DBLP:<key>) for one or more records
  stats             library version and size

Query syntax for `search`: words are ANDed; "quoted phrase"; OR between terms;
hyphenated words match as phrases (over-smoothing == "over smoothing"). Use --raw for
native SQLite FTS5 syntax (e.g. NEAR, parentheses, NOT).

Examples:
  python -I ps/dblp_query.py search "graph neural network over-smoothing" --year 2020-
  python -I ps/dblp_query.py search "diffusion model" --tier 1 --limit 30
  python -I ps/dblp_query.py search "vision transformer" --venue conf/cvpr --venue conf/iccv --json
  python -I ps/dblp_query.py venue "pattern analysis"
  python -I ps/dblp_query.py bib conf/cvpr/HeZRS16 10.1109/TPAMI.2016.2577031
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "dblp.sqlite"
VENUES_YAML = ROOT / "config" / "venues.yaml"

FIELDS = "p.key, p.type, p.title, p.authors, p.year, p.venue, p.venue_key, p.volume, p.number, " \
         "p.pages, p.doi, p.arxiv, p.ee, p.crossref, p.publtype"
COLS = [c.split(".")[1] for c in FIELDS.split(", ")]


# ---------------------------------------------------------------- helpers
def connect() -> sqlite3.Connection:
    if not DB.exists():
        sys.exit(f"error: {DB} not found — build it with ps/dblp_build.py first")
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    cache: dict[str, re.Pattern] = {}
    con.create_function("REGEXP", 2, lambda rx, s: s is not None and bool(
        (cache.get(rx) or cache.setdefault(rx, re.compile(rx))).fullmatch(s)), deterministic=True)
    return con


_CFG = None


def load_config() -> dict:
    global _CFG
    if _CFG is None:
        _CFG = {}
        if VENUES_YAML.exists():
            import yaml
            _CFG = yaml.safe_load(VENUES_YAML.read_text(encoding="utf-8")) or {}
    return _CFG


def load_tiers() -> dict[str, dict]:
    """venue_key -> {'tier', 'short', 'regex': whitelist of dblp venue strings (main track only) or None}"""
    out = {}
    for v in load_config().get("venues") or []:
        for k in ([v["dblp"]] if isinstance(v.get("dblp"), str) else v.get("dblp") or []):
            out[k] = {"tier": v.get("tier"), "short": v.get("short") or v.get("name"),
                      "regex": v.get("dblp_venue_regex")}
    return out


def venue_ok(t: dict, venue: str | None) -> bool:
    return not t.get("regex") or bool(re.fullmatch(t["regex"], venue or ""))


def year_band(year: int | None) -> int | None:
    """1-based index of the first matching band in venues.yaml `year_priority` (None = no band)."""
    for i, band in enumerate(load_config().get("year_priority") or [], 1):
        lo, hi = band.get("from"), band.get("to")
        if year is not None and (lo is None or year >= lo) and (hi is None or year <= hi):
            return i
    return None


def to_fts(q: str) -> str:
    """Turn a plain query into a safe FTS5 expression on the title column."""
    parts = []
    for m in re.finditer(r'"([^"]+)"|(\S+)', q):
        phrase, word = m.group(1), m.group(2)
        if word == "OR":
            parts.append("OR")
            continue
        toks = re.findall(r"[^\W_]+", phrase or word)
        if toks:
            parts.append('"' + " ".join(toks) + '"')
    while parts and parts[-1] == "OR":
        parts.pop()
    while parts and parts[0] == "OR":
        parts.pop(0)
    if not parts:
        sys.exit("error: empty query")
    return "title : (" + " ".join(parts) + ")"


def parse_years(s: str | None) -> tuple[int | None, int | None]:
    if not s:
        return None, None
    m = re.fullmatch(r"(\d{4})?\s*(-)?\s*(\d{4})?", s.strip())
    if not m:
        sys.exit(f"error: bad --year {s!r} (use 2020, 2018-2022, 2020-, -2015)")
    a, dash, b = m.groups()
    if not dash:
        return int(a), int(a)
    return (int(a) if a else None), (int(b) if b else None)


def row_to_dict(r: sqlite3.Row, tiers: dict) -> dict:
    d = {c: r[c] for c in COLS}
    t = tiers.get(d["venue_key"] or "")
    d["tier"] = t["tier"] if (t and venue_ok(t, d["venue"])) else None
    d["short"] = t["short"] if d["tier"] else None
    d["year_band"] = year_band(d["year"])
    d["preprint"] = d["publtype"] == "informal" or (d["venue_key"] == "journals/corr")
    d["dblp_url"] = f"https://dblp.org/rec/{d['key']}"
    return d


def norm_title(t: str | None) -> str:
    return re.sub(r"[^\w]+", "", (t or "").lower())


def dedupe(rows: list[dict]) -> list[dict]:
    """Merge records with the same title (typically arXiv/CoRR copy + published version).
    Keeps the best version (tiered > published > preprint) and records the others in `also`."""
    groups: dict[str, list[dict]] = {}
    for d in rows:
        groups.setdefault(norm_title(d["title"]) or d["key"], []).append(d)
    out = []
    for g in groups.values():
        g.sort(key=lambda d: (d["tier"] or 99, d["preprint"], -(d["year"] or 0)))
        best = dict(g[0])
        best["relevance_rank"] = min(d["relevance_rank"] for d in g)
        for d in g[1:]:
            best["arxiv"] = best["arxiv"] or d["arxiv"]
            best["doi"] = best["doi"] or d["doi"]
        best["also"] = [f"{d['venue']} {d['year']}" for d in g[1:]]
        out.append(best)
    return sorted(out, key=lambda d: d["relevance_rank"])


def find_record(con, ident: str):
    ident = ident.strip()
    m = re.search(r"(10\.\d{4,9}/\S+)", ident)
    if m:
        return con.execute(f"SELECT {FIELDS} FROM pubs p WHERE p.doi = ?", (m.group(1).lower().rstrip("."),)).fetchone()
    m = re.search(r"(\d{4}\.\d{4,5})(v\d+)?$", ident)
    if m and "/" not in ident.replace("arxiv.org/abs/", ""):
        return con.execute(f"SELECT {FIELDS} FROM pubs p WHERE p.arxiv = ? ORDER BY p.venue_key = 'journals/corr'",
                           (m.group(1),)).fetchone()
    key = re.sub(r"^https?://dblp\.org/rec/", "", ident).removesuffix(".html").removesuffix(".bib")
    return con.execute(f"SELECT {FIELDS} FROM pubs p WHERE p.key = ?", (key,)).fetchone()


def print_rows(rows: list[dict]):
    for i, d in enumerate(rows, 1):
        tier = f"T{d['tier']}" if d["tier"] else ("pre" if d["preprint"] else "  -")
        tier += f"/Y{d['year_band']}" if d.get("year_band") else ""
        authors = d["authors"] or ""
        if authors.count(";") >= 3:
            authors = authors.split(";")[0] + " et al."
        ident = f"doi:{d['doi']}" if d["doi"] else (f"arXiv:{d['arxiv']}" if d["arxiv"] else d["dblp_url"])
        if d["doi"] and d["arxiv"] and not d["doi"].startswith("10.48550"):
            ident += f" | arXiv:{d['arxiv']}"
        also = f"  (also: {', '.join(d['also'])})" if d.get("also") else ""
        print(f"{i:>3}. [{tier}] {d['title']}")
        print(f"      {authors} | {d['venue']} {d['year']} | {ident}{also}")


# ---------------------------------------------------------------- commands
def cmd_search(a):
    con, tiers = connect(), load_tiers()
    where, params = [], []
    where.append("pubs_fts MATCH ?")
    params.append(a.query if a.raw else to_fts(a.query))
    y0, y1 = parse_years(a.year)
    if y0: where.append("p.year >= ?"); params.append(y0)
    if y1: where.append("p.year <= ?"); params.append(y1)
    keys = list(a.venue or [])
    if a.tier:
        wanted = {int(t) for t in a.tier}
        keys += [k for k, v in tiers.items() if v["tier"] in wanted]
        if not keys:
            sys.exit(f"error: no venues with tier {sorted(wanted)} in {VENUES_YAML}")
    if keys:
        # venue key + (optional) main-track whitelist, evaluated in SQL so workshops never fill the pool
        conds = []
        for k in keys:
            rx = (tiers.get(k) or {}).get("regex")
            conds.append("(p.venue_key = ? AND p.venue REGEXP ?)" if rx else "p.venue_key = ?")
            params.extend([k, rx] if rx else [k])
        where.append("(" + " OR ".join(conds) + ")")
    if a.type:
        where.append("p.type = ?"); params.append(a.type)
    if a.no_preprints:
        where.append("p.venue_key != 'journals/corr' AND IFNULL(p.publtype,'') != 'informal'")
    where.append("p.type != 'proceedings'")
    order = {"relevance": "bm25(pubs_fts)", "priority": "bm25(pubs_fts)",
             "year": "p.year DESC, bm25(pubs_fts)"}[a.sort]
    sql = (f"SELECT {FIELDS} FROM pubs_fts JOIN pubs p ON p.rowid = pubs_fts.rowid "
           f"WHERE {' AND '.join(where)} ORDER BY {order} LIMIT ?")
    # priority sort re-ranks a larger relevance-ordered candidate pool; tier filters may drop workshop rows
    fetch = max(a.pool, a.limit) if (a.sort == "priority" or a.tier) else a.limit
    rows = [row_to_dict(r, tiers) for r in con.execute(sql, params + [fetch])]
    if a.tier:
        rows = [d for d in rows if d["tier"] in {int(t) for t in a.tier}]
    for i, d in enumerate(rows):
        d["relevance_rank"] = i + 1
    if not a.keep_duplicates:
        rows = dedupe(rows)
    if a.sort == "priority":
        rows.sort(key=lambda d: (d["tier"] or 99, d["year_band"] or 99, d["relevance_rank"]))
    rows = rows[: a.limit]
    total = con.execute(f"SELECT COUNT(*) FROM pubs_fts JOIN pubs p ON p.rowid = pubs_fts.rowid "
                        f"WHERE {' AND '.join(where)}", params).fetchone()[0]
    if a.json:
        print(json.dumps({"query": a.query, "total_matches": total, "results": rows}, ensure_ascii=False, indent=1))
    else:
        print(f"{total:,} matches (showing {len(rows)})")
        print_rows(rows)


def cmd_venue(a):
    con = connect()
    pat = f"%{a.name}%"
    rows = con.execute("""
        SELECT v.venue_key, v.venue, v.type, v.n, v.first_year, v.last_year FROM venues v
        WHERE v.venue LIKE ? OR v.venue_key LIKE ?
           OR v.venue_key IN (SELECT venue_key FROM venue_titles WHERE title LIKE ?)
        ORDER BY v.n DESC LIMIT ?""", (pat, pat, pat, a.limit)).fetchall()
    if a.json:
        print(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=1))
        return
    for r in rows:
        sample = con.execute("SELECT title FROM venue_titles WHERE venue_key=? ORDER BY year DESC LIMIT 1",
                             (r["venue_key"],)).fetchone()
        print(f"{r['venue_key']:<24} {r['venue']!r:<40} {r['type']:<14} {r['n']:>7} papers  {r['first_year']}-{r['last_year']}")
        if sample:
            print(f"{'':24} latest proceedings: {sample['title'][:110]}")


def cmd_get(a):
    con, tiers = connect(), load_tiers()
    r = find_record(con, a.id)
    if not r:
        sys.exit(f"not found in local dblp: {a.id}")
    d = row_to_dict(r, tiers)
    d["authors_detail"] = [dict(x) for x in con.execute(
        "SELECT name, orcid FROM pub_authors WHERE key=? ORDER BY pos", (d["key"],))]
    print(json.dumps(d, ensure_ascii=False, indent=1))


def bibtex(con, r) -> str:
    d = dict(zip(COLS, r))
    names = [x[0] for x in con.execute("SELECT name FROM pub_authors WHERE key=? ORDER BY pos", (d["key"],))]
    names = [re.sub(r"\s+\d{4}$", "", n) for n in names]
    kind = {"article": "article", "inproceedings": "inproceedings", "incollection": "incollection",
            "book": "book", "proceedings": "proceedings", "phdthesis": "phdthesis",
            "mastersthesis": "mastersthesis"}.get(d["type"], "misc")
    f = {"author": " and\n                  ".join(names) if names else None, "title": d["title"]}
    if kind == "article":
        f["journal"] = d["venue"]
        f.update(volume=d["volume"], number=d["number"])
    elif kind in ("inproceedings", "incollection"):
        parent = con.execute("SELECT title FROM pubs WHERE key=?", (d["crossref"],)).fetchone() if d["crossref"] else None
        f["booktitle"] = parent[0] if parent else d["venue"]
    f.update(pages=(d["pages"] or "").replace("-", "--") or None, year=d["year"], doi=d["doi"],
             url=d["ee"])
    if d["arxiv"] and d["venue_key"] == "journals/corr":
        f.update(eprinttype="arXiv", eprint=d["arxiv"])
    f["biburl"] = f"https://dblp.org/rec/{d['key']}.bib"
    body = ",\n".join(f"  {k:<12} = {{{v}}}" for k, v in f.items() if v not in (None, ""))
    return f"@{kind}{{DBLP:{d['key']},\n{body}\n}}"


def cmd_bib(a):
    con = connect()
    out, missing = [], []
    for ident in a.ids:
        r = find_record(con, ident)
        (out.append(bibtex(con, r)) if r else missing.append(ident))
    print("\n\n".join(out))
    if missing:
        print(f"\n% not found in local dblp: {', '.join(missing)}", file=sys.stderr)


def cmd_stats(a):
    con = connect()
    meta = dict(con.execute("SELECT k, v FROM meta").fetchall())
    meta["by_type"] = dict(con.execute("SELECT type, COUNT(*) FROM pubs GROUP BY type ORDER BY 2 DESC").fetchall())
    meta["latest_year_counts"] = dict(con.execute(
        "SELECT year, COUNT(*) FROM pubs WHERE year >= strftime('%Y','now') - 2 GROUP BY year").fetchall())
    meta["db_size_gb"] = round(DB.stat().st_size / 1e9, 2)
    print(json.dumps(meta, ensure_ascii=False, indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search", help="full-text title search")
    s.add_argument("query")
    s.add_argument("--venue", action="append", help="dblp venue key, e.g. conf/cvpr (repeatable)")
    s.add_argument("--tier", action="append", help="only venues of this tier in config/venues.yaml (repeatable)")
    s.add_argument("--year", help="2020 | 2018-2022 | 2020- | -2015")
    s.add_argument("--type", choices=["article", "inproceedings", "incollection", "book", "phdthesis"])
    s.add_argument("--no-preprints", action="store_true", help="drop arXiv/CoRR and other informal records")
    s.add_argument("--sort", choices=["priority", "relevance", "year"], default="priority",
                   help="priority = tier, then year band (venues.yaml year_priority), then relevance")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--pool", type=int, default=300, help="candidates re-ranked by --sort priority")
    s.add_argument("--raw", action="store_true", help="query is native FTS5 syntax")
    s.add_argument("--keep-duplicates", action="store_true", help="don't merge arXiv copies with published versions")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_search)
    v = sub.add_parser("venue", help="look up venue keys by name")
    v.add_argument("name"); v.add_argument("--limit", type=int, default=15); v.add_argument("--json", action="store_true")
    v.set_defaults(fn=cmd_venue)
    g = sub.add_parser("get", help="one record by key / DOI / arXiv id"); g.add_argument("id"); g.set_defaults(fn=cmd_get)
    b = sub.add_parser("bib", help="BibTeX for records"); b.add_argument("ids", nargs="+"); b.set_defaults(fn=cmd_bib)
    st = sub.add_parser("stats", help="library info"); st.set_defaults(fn=cmd_stats)
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    a.fn(a)


if __name__ == "__main__":
    main()
