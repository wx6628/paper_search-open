"""Personal paper library (library/library.sqlite): papers you have touched, enriched over time.

Completeness levels: L0 metadata · L1 abstract · L2 local PDF · L3 AI analysis or own note.

CLI (all output is JSON unless noted):
  add <id>... [--group G] [--status S]          add by dblp key / DOI / arXiv id (local dblp first)
  import <file.json> [--keep 1,3,5] [--group G] [--search-id N] [--keep-run]
                                                import fresh.py / search.py results; the data/runs file is
                                                deleted afterwards (consumed) unless --keep-run
  show <pid>                                    one paper with groups, notes, relations
  list [--group G] [--status S] [--starred] [--level 0..3] [--q text] [--limit N]
  set <pid>... [--status unread|reading|read|excluded] [--star|--unstar]
  group list                                    tree of big groups (大组) and sub-groups (小组)
  group create "大组" | "大组/小组" [--description D]
  group add "大组/小组" 1,2,3                    adding to a sub-group also adds to its big group
  group remove "大组" 1,2   |  group remove "大组/小组" 1,2   (big group: also leaves its sub-groups)
  group move "大组/小组" 1,2                      put into this sub-group, leaving sibling sub-groups
  group rename "大组/小组" 新名字  ·  group delete "大组"  (sub-groups deleted too; papers are kept)
  group reparent "某大组" "另一大组"  (becomes a sub-group)  ·  group reparent "大组/小组" -  (becomes big)
  relate <pid> <pid> <type> [--note TEXT]       relation between two papers
  attach-pdf <pid> <path> [--copy]              link an existing local PDF (default) or copy it into library/pdfs;
                                                then (re)extracts the abstract from it unless it came from elsewhere
  detach-pdf <pid> [--delete-copy]              unlink the PDF; a PDF-extracted abstract is cleared too;
                                                --delete-copy also deletes a managed copy in library/pdfs
  link-note <pid> <file.md>                     register an AI analysis note
  scan-notes                                    auto-link Markdown notes in library/ai_notes to papers
  note <pid> [--set TEXT | --file F]            own plain-text note (library/notes/<pid>.txt)
  bib [--group G | --ids 1,2 | --starred]       BibTeX (dblp version when available)
  queue [list|add <pid> <task>|done <qid>|fail <qid>] [--message M]
                                                tasks for Claude (read = AI analysis, enrich, fetch_pdf);
                                                done/fail also removes the task scratch dir data/tmp/queue-<qid>
  stats
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dblp_query  # noqa: E402
import scratch  # noqa: E402

ROOT = HERE.parent
LIB = ROOT / "library"
DB = LIB / "library.sqlite"
PDF_DIR = LIB / "pdfs"
AI_DIR = LIB / "ai_notes"
NOTE_DIR = LIB / "notes"

SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL, norm_title TEXT, authors TEXT,
    year INTEGER, pub_date TEXT,
    venue TEXT, venue_short TEXT, venue_key TEXT, tier INTEGER, year_band INTEGER,
    pub_status TEXT DEFAULT 'published',        -- published / accepted / under_review / claimed / preprint
    doi TEXT, arxiv TEXT, dblp_key TEXT, openalex_id TEXT, openreview_id TEXT,
    abstract TEXT, abstract_source TEXT, citations INTEGER, oa_url TEXT, url TEXT,
    status TEXT DEFAULT 'unread',               -- unread / reading / read / excluded
    starred INTEGER DEFAULT 0,
    pdf_path TEXT, pdf_kind TEXT,               -- managed (in library/pdfs) | linked (elsewhere on disk)
    found_by TEXT, added_at TEXT, updated_at TEXT, enriched_at TEXT, enrich_log TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_papers_doi ON papers(doi) WHERE doi IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_papers_dblp ON papers(dblp_key) WHERE dblp_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_papers_arxiv ON papers(arxiv);
CREATE INDEX IF NOT EXISTS ix_papers_norm ON papers(norm_title);

-- two-level groups: parent_id NULL = big group (大组); otherwise a sub-group (小组) of a big group.
-- Names are unique among siblings, so different big groups may each have e.g. a "方法" sub-group.
CREATE TABLE IF NOT EXISTS groups (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL,
    parent_id INTEGER REFERENCES groups(id) ON DELETE CASCADE,
    description TEXT, color TEXT, created_at TEXT, sort INTEGER DEFAULT 0);
CREATE UNIQUE INDEX IF NOT EXISTS ux_groups_sibling ON groups(IFNULL(parent_id, 0), name);
CREATE TABLE IF NOT EXISTS paper_groups (
    paper_id INTEGER REFERENCES papers(id) ON DELETE CASCADE,
    group_id INTEGER REFERENCES groups(id) ON DELETE CASCADE,
    added_at TEXT, PRIMARY KEY (paper_id, group_id));
CREATE TABLE IF NOT EXISTS relations (
    id INTEGER PRIMARY KEY,
    src_id INTEGER REFERENCES papers(id) ON DELETE CASCADE,
    dst_id INTEGER REFERENCES papers(id) ON DELETE CASCADE,
    rel_type TEXT NOT NULL, note TEXT, created_at TEXT, UNIQUE (src_id, dst_id, rel_type));
CREATE TABLE IF NOT EXISTS ai_notes (
    id INTEGER PRIMARY KEY, paper_id INTEGER REFERENCES papers(id) ON DELETE CASCADE,
    path TEXT NOT NULL, title TEXT, created_at TEXT, UNIQUE (paper_id, path));
CREATE TABLE IF NOT EXISTS searches (
    id INTEGER PRIMARY KEY, query TEXT, params TEXT, kind TEXT, run_at TEXT, n_candidates INTEGER,
    n_kept INTEGER, report_path TEXT);
CREATE TABLE IF NOT EXISTS search_hits (
    search_id INTEGER REFERENCES searches(id) ON DELETE CASCADE,
    paper_id INTEGER REFERENCES papers(id) ON DELETE CASCADE,
    rank INTEGER, reason TEXT, PRIMARY KEY (search_id, paper_id));
CREATE TABLE IF NOT EXISTS queue (
    id INTEGER PRIMARY KEY, paper_id INTEGER REFERENCES papers(id) ON DELETE CASCADE,
    task TEXT NOT NULL, args TEXT, status TEXT DEFAULT 'pending', message TEXT,
    created_at TEXT, done_at TEXT);

CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts USING fts5(
    title, authors, abstract, venue, content='papers', content_rowid='id',
    tokenize='porter unicode61 remove_diacritics 2');
CREATE TRIGGER IF NOT EXISTS papers_ai AFTER INSERT ON papers BEGIN
    INSERT INTO papers_fts(rowid, title, authors, abstract, venue) VALUES (new.id, new.title, new.authors, new.abstract, new.venue);
END;
CREATE TRIGGER IF NOT EXISTS papers_ad AFTER DELETE ON papers BEGIN
    INSERT INTO papers_fts(papers_fts, rowid, title, authors, abstract, venue) VALUES ('delete', old.id, old.title, old.authors, old.abstract, old.venue);
END;
CREATE TRIGGER IF NOT EXISTS papers_au AFTER UPDATE OF title, authors, abstract, venue ON papers BEGIN
    INSERT INTO papers_fts(papers_fts, rowid, title, authors, abstract, venue) VALUES ('delete', old.id, old.title, old.authors, old.abstract, old.venue);
    INSERT INTO papers_fts(rowid, title, authors, abstract, venue) VALUES (new.id, new.title, new.authors, new.abstract, new.venue);
END;
"""

PAPER_FIELDS = ["title", "authors", "year", "pub_date", "venue", "venue_short", "venue_key", "tier", "year_band",
                "pub_status", "doi", "arxiv", "dblp_key", "openalex_id", "openreview_id", "abstract",
                "abstract_source", "citations", "oa_url", "url", "found_by"]
STATUSES = ("unread", "reading", "read", "excluded")


def now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    LIB.mkdir(parents=True, exist_ok=True)
    for d in (PDF_DIR, AI_DIR, NOTE_DIR):
        d.mkdir(exist_ok=True)
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")  # GUI server and CLI can write concurrently
    migrate(con)
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(SCHEMA)
    return con


def migrate(con: sqlite3.Connection):
    """Upgrade old single-level `groups` (UNIQUE name, no parent_id) to the two-level layout.
    Foreign keys are off here, so dropping the old table does not cascade into paper_groups."""
    cols = [r[1] for r in con.execute("PRAGMA table_info(groups)")]
    if not cols or "parent_id" in cols:
        return
    con.executescript("""
        PRAGMA foreign_keys=OFF;
        BEGIN;
        CREATE TABLE groups_v2 (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL,
            parent_id INTEGER REFERENCES groups(id) ON DELETE CASCADE,
            description TEXT, color TEXT, created_at TEXT, sort INTEGER DEFAULT 0);
        INSERT INTO groups_v2 (id, name, description, color, created_at)
            SELECT id, name, description, color, created_at FROM groups;
        DROP TABLE groups;
        ALTER TABLE groups_v2 RENAME TO groups;
        COMMIT;
    """)


def norm_title(t: str | None) -> str:
    return re.sub(r"[^\w]+", "", (t or "").lower())


def level(p: dict) -> int:
    if p.get("has_ai_note") or p.get("has_note"):
        return 3
    if p.get("pdf_path"):
        return 2
    if p.get("abstract"):
        return 1
    return 0


# ---------------------------------------------------------------- upsert / lookup


def find_existing(con, rec: dict):
    for col in ("doi", "dblp_key", "arxiv", "openreview_id"):
        v = rec.get(col)
        if v:
            r = con.execute(f"SELECT * FROM papers WHERE {col}=?", (v,)).fetchone()
            if r:
                return r
    nt = norm_title(rec.get("title"))
    if nt and len(nt) > 12:
        return con.execute("SELECT * FROM papers WHERE norm_title=?", (nt,)).fetchone()
    return None


RANK = {"published": 0, "accepted": 1, "under_review": 2, "claimed": 3, "preprint": 4}


def upsert(con, rec: dict) -> tuple[int, bool]:
    """Insert or merge a paper record. Returns (paper_id, created)."""
    rec = {k: rec.get(k) for k in PAPER_FIELDS}
    if rec["doi"]:
        rec["doi"] = re.sub(r"^https?://(dx\.)?doi\.org/", "", rec["doi"]).lower()
    if not rec["title"]:
        raise ValueError("record without title")
    old = find_existing(con, rec)
    if old is None:
        cols = PAPER_FIELDS + ["norm_title", "added_at", "updated_at"]
        vals = [rec[k] for k in PAPER_FIELDS] + [norm_title(rec["title"]), now(), now()]
        cur = con.execute(f"INSERT INTO papers ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)
        return cur.lastrowid, True
    upd = {}
    better = RANK.get(rec["pub_status"] or "published", 0) < RANK.get(old["pub_status"] or "published", 0)
    for k in PAPER_FIELDS:
        new, cur = rec.get(k), old[k]
        if new in (None, ""):
            continue
        # venue/status fields follow the more authoritative version (published > accepted > claimed > preprint)
        if k in ("venue", "venue_short", "venue_key", "tier", "year_band", "pub_status", "year", "pub_date", "url"):
            if cur in (None, "") or better:
                upd[k] = new
        elif cur in (None, ""):
            upd[k] = new
    if upd:
        upd["updated_at"] = now()
        con.execute(f"UPDATE papers SET {', '.join(f'{k}=?' for k in upd)} WHERE id=?", [*upd.values(), old["id"]])
    return old["id"], False


def rec_from_dblp(d: dict) -> dict:
    preprint = d.get("preprint")
    return {"title": d["title"], "authors": d["authors"], "year": d["year"], "venue": d["venue"],
            "venue_short": d.get("short") or d["venue"], "venue_key": d["venue_key"], "tier": d.get("tier"),
            "year_band": d.get("year_band"), "pub_status": "preprint" if preprint else "published",
            "doi": None if (d.get("doi") or "").startswith("10.48550/") else d.get("doi"),
            "arxiv": d.get("arxiv"), "dblp_key": d["key"], "url": d.get("ee") or d.get("dblp_url"),
            "found_by": "dblp"}


def rec_from_fresh(r: dict) -> dict:
    return {"title": r["title"], "authors": r.get("authors"), "year": r.get("year"), "pub_date": r.get("pub_date"),
            "venue": r.get("venue"), "venue_short": r.get("venue_short"), "tier": r.get("tier"),
            "year_band": r.get("year_band"), "pub_status": r.get("status") or "published", "doi": r.get("doi"),
            "arxiv": r.get("arxiv"), "openreview_id": r.get("openreview"), "abstract": r.get("abstract"),
            "abstract_source": ",".join(r.get("sources") or []) if r.get("abstract") else None,
            "url": r.get("url"), "found_by": ",".join(r.get("sources") or []) or "fresh"}


def lookup_dblp(ident: str) -> dict | None:
    con = dblp_query.connect()
    row = dblp_query.find_record(con, ident)
    if not row:
        return None
    d = dblp_query.row_to_dict(row, dblp_query.load_tiers())
    if not d.get("arxiv") and d.get("title"):  # dblp also indexes the arXiv (CoRR) copy: borrow its id
        toks = re.findall(r"[^\W_]+", d["title"])[:12]
        if toks:
            for t, ax in con.execute("SELECT p.title, p.arxiv FROM pubs_fts JOIN pubs p ON p.rowid=pubs_fts.rowid "
                                     "WHERE pubs_fts MATCH ? AND p.venue_key='journals/corr' LIMIT 5",
                                     ('title : "' + " ".join(toks) + '"',)):
                if ax and norm_title(t) == norm_title(d["title"]):
                    d["arxiv"] = ax
                    break
    return d


def recompute_tiers(con) -> int:
    """Re-apply config/venues.yaml (tiers, main-track regexes, year bands) to every paper."""
    dblp_query._CFG = None
    tiers = dblp_query.load_tiers()
    shorts = {v.get("short"): v for v in (dblp_query.load_config().get("venues") or [])}
    n = 0
    for p in con.execute("SELECT id, venue, venue_key, venue_short, year FROM papers").fetchall():
        t = tiers.get(p["venue_key"] or "")
        tier = t["tier"] if (t and dblp_query.venue_ok(t, p["venue"])) else None
        short = t["short"] if tier else p["venue_short"]
        if tier is None and p["venue_key"] is None and p["venue_short"] in shorts:   # non-dblp (fresh) records
            tier = shorts[p["venue_short"]].get("tier")
        con.execute("UPDATE papers SET tier=?, venue_short=?, year_band=? WHERE id=?",
                    (tier, short, dblp_query.year_band(p["year"]), p["id"]))
        n += 1
    return n


# ---------------------------------------------------------------- groups


PALETTE = ["#2f6feb", "#b42318", "#027a48", "#b54708", "#7a3fd1", "#0e7490", "#be185d", "#4b5563"]


def split_path(path: str) -> list[str]:
    """'大组/小组' -> ['大组', '小组'];  '大组' -> ['大组'].  At most two levels."""
    parts = [x.strip() for x in str(path).replace("\\", "/").split("/") if x.strip()]
    if not 1 <= len(parts) <= 2:
        raise SystemExit(f"error: group path must be '大组' or '大组/小组', got {path!r}")
    return parts


def check_name(name: str) -> str:
    name = (name or "").strip()
    if not name or "/" in name or "\\" in name:
        raise SystemExit("error: group name must be non-empty and must not contain '/'")
    return name


def _create_group(con, name: str, parent_id: int | None) -> int:
    if parent_id is None:
        n = con.execute("SELECT COUNT(*) FROM groups WHERE parent_id IS NULL").fetchone()[0]
        color = PALETTE[n % len(PALETTE)]
    else:  # sub-groups inherit the big group's colour
        color = con.execute("SELECT color FROM groups WHERE id=?", (parent_id,)).fetchone()[0]
    return con.execute("INSERT INTO groups (name, parent_id, color, created_at) VALUES (?,?,?,?)",
                       (check_name(name), parent_id, color, now())).lastrowid


def group_id(con, ref, create: bool = False) -> int:
    """Resolve a group by numeric id or by path '大组' / '大组/小组' (optionally creating it)."""
    if isinstance(ref, int) or (isinstance(ref, str) and ref.strip().isdigit()):
        r = con.execute("SELECT id FROM groups WHERE id=?", (int(ref),)).fetchone()
        if not r:
            raise SystemExit(f"error: no group with id {ref}")
        return r["id"]
    parts = split_path(ref)
    r = con.execute("SELECT id FROM groups WHERE parent_id IS NULL AND name=?", (parts[0],)).fetchone()
    if r:
        top = r["id"]
    elif create:
        top = _create_group(con, parts[0], None)
    else:
        raise SystemExit(f"error: no big group named {parts[0]!r}")
    if len(parts) == 1:
        return top
    r = con.execute("SELECT id FROM groups WHERE parent_id=? AND name=?", (top, parts[1])).fetchone()
    if r:
        return r["id"]
    if create:
        return _create_group(con, parts[1], top)
    raise SystemExit(f"error: no sub-group {parts[1]!r} in {parts[0]!r}")


def group_row(con, gid: int):
    return con.execute("SELECT * FROM groups WHERE id=?", (gid,)).fetchone()


def group_path(con, gid: int) -> str:
    g = group_row(con, gid)
    if g["parent_id"]:
        return f"{group_row(con, g['parent_id'])['name']}/{g['name']}"
    return g["name"]


def add_to_group(con, pids: list[int], ref) -> int:
    """Add papers to a group; adding to a sub-group also adds them to its big group."""
    gid = group_id(con, ref, create=True)
    parent = group_row(con, gid)["parent_id"]
    rows = [(p, gid, now()) for p in pids] + ([(p, parent, now()) for p in pids] if parent else [])
    con.executemany("INSERT OR IGNORE INTO paper_groups VALUES (?,?,?)", rows)
    return gid


def remove_from_group(con, pids: list[int], ref) -> int:
    """Remove papers from a group; removing from a big group also removes them from its sub-groups.
    Removing from a sub-group keeps them in the big group (they become '未分小组')."""
    gid = group_id(con, ref)
    ids = [gid] + [r[0] for r in con.execute("SELECT id FROM groups WHERE parent_id=?", (gid,))]
    con.executemany("DELETE FROM paper_groups WHERE paper_id=? AND group_id=?", [(p, g) for p in pids for g in ids])
    return gid


def move_to_subgroup(con, pids: list[int], ref) -> int:
    """Put papers into exactly this sub-group within its big group (leaving sibling sub-groups)."""
    gid = group_id(con, ref, create=True)
    parent = group_row(con, gid)["parent_id"]
    if not parent:
        raise SystemExit("error: move target must be a sub-group ('大组/小组')")
    sibs = [r[0] for r in con.execute("SELECT id FROM groups WHERE parent_id=? AND id!=?", (parent, gid))]
    con.executemany("DELETE FROM paper_groups WHERE paper_id=? AND group_id=?", [(p, s) for p in pids for s in sibs])
    return add_to_group(con, pids, gid)


def reparent_group(con, ref, new_parent) -> int:
    """Turn a big group into a sub-group of another big group (new_parent), or a sub-group into a
    big group (new_parent=None). Members follow along and are added to the new big group."""
    gid = group_id(con, ref)
    g = group_row(con, gid)
    if new_parent in (None, "", "-"):
        con.execute("UPDATE groups SET parent_id=NULL WHERE id=?", (gid,))
        return gid
    pid = group_id(con, new_parent)
    if group_row(con, pid)["parent_id"]:
        raise SystemExit("error: the new parent must be a big group (only two levels)")
    if pid == gid:
        raise SystemExit("error: a group cannot be its own parent")
    if con.execute("SELECT 1 FROM groups WHERE parent_id=?", (gid,)).fetchone():
        raise SystemExit("error: this big group has sub-groups; move or delete them first (only two levels)")
    if con.execute("SELECT 1 FROM groups WHERE parent_id=? AND name=?", (pid, g["name"])).fetchone():
        raise SystemExit(f"error: {group_path(con, pid)} already has a sub-group named {g['name']!r}")
    con.execute("UPDATE groups SET parent_id=? WHERE id=?", (pid, gid))
    members = [r[0] for r in con.execute("SELECT paper_id FROM paper_groups WHERE group_id=?", (gid,))]
    con.executemany("INSERT OR IGNORE INTO paper_groups VALUES (?,?,?)", [(p, pid, now()) for p in members])
    return gid


def group_tree(con) -> list[dict]:
    """Big groups with their sub-groups and counts (n = papers; unassigned = in the big group but no sub-group)."""
    rows = [dict(r) for r in con.execute("""
        SELECT g.id, g.name, g.parent_id, g.description, g.color, g.sort, COUNT(pg.paper_id) AS n
        FROM groups g LEFT JOIN paper_groups pg ON pg.group_id=g.id GROUP BY g.id ORDER BY g.sort, g.name""")]
    by_parent: dict = {}
    for r in rows:
        by_parent.setdefault(r["parent_id"], []).append(r)
    out = []
    for top in by_parent.get(None, []):
        top["children"] = by_parent.get(top["id"], [])
        for c in top["children"]:
            c["path"] = f"{top['name']}/{c['name']}"
        top["path"] = top["name"]
        top["unassigned"] = con.execute("""SELECT COUNT(*) FROM paper_groups pg WHERE pg.group_id=? AND pg.paper_id NOT IN
            (SELECT paper_id FROM paper_groups WHERE group_id IN (SELECT id FROM groups WHERE parent_id=?))""",
                                        (top["id"], top["id"])).fetchone()[0]
        out.append(top)
    return out


# ---------------------------------------------------------------- reads


def paper_dict(con, row) -> dict:
    p = dict(row)
    pid = p["id"]
    mem = [dict(r) for r in con.execute("""
        SELECT g.id, g.name, g.parent_id, g.color, pg2.name AS parent_name FROM groups g
        JOIN paper_groups pg ON pg.group_id=g.id LEFT JOIN groups pg2 ON pg2.id=g.parent_id
        WHERE pg.paper_id=? ORDER BY IFNULL(pg2.name, g.name), g.parent_id IS NOT NULL, g.name""", (pid,))]
    parents_with_child = {m["parent_id"] for m in mem if m["parent_id"]}
    # display: '大组/小组' for sub-group memberships; bare '大组' only if the paper is in none of its sub-groups
    p["group_refs"] = [{"id": m["id"], "path": f"{m['parent_name']}/{m['name']}" if m["parent_id"] else m["name"],
                        "parent_id": m["parent_id"], "color": m["color"]}
                       for m in mem if m["parent_id"] or m["id"] not in parents_with_child]
    p["groups"] = [g["path"] for g in p["group_refs"]]
    p["group_ids"] = [m["id"] for m in mem]
    p["ai_notes"] = [dict(r) for r in con.execute("SELECT id, path, title, created_at FROM ai_notes WHERE paper_id=?", (pid,))]
    note = NOTE_DIR / f"{pid}.txt"
    p["has_note"] = note.exists() and note.stat().st_size > 0
    p["has_ai_note"] = bool(p["ai_notes"])
    p["pdf_exists"] = bool(p["pdf_path"]) and Path(p["pdf_path"]).exists()
    p["level"] = level(p)
    return p


def list_papers(con, group=None, status=None, starred=False, min_level=None, q=None, tier=None,
                limit=500, offset=0, sort="priority", unassigned=False) -> list[dict]:
    """group: id or path ('大组' includes its sub-groups' papers). unassigned=True with a big group:
    only papers of that big group that are in none of its sub-groups."""
    where, args = [], []
    if group:
        gid = group_id(con, group)
        where.append("p.id IN (SELECT paper_id FROM paper_groups WHERE group_id=?)")
        args.append(gid)
        if unassigned:
            where.append("p.id NOT IN (SELECT paper_id FROM paper_groups WHERE group_id IN "
                         "(SELECT id FROM groups WHERE parent_id=?))")
            args.append(gid)
    if status:
        where.append("p.status=?"); args.append(status)
    if starred:
        where.append("p.starred=1")
    if tier:
        where.append("p.tier=?"); args.append(int(tier))
    if q:
        where.append("p.id IN (SELECT rowid FROM papers_fts WHERE papers_fts MATCH ?)")
        args.append(" ".join('"' + t + '"' for t in re.findall(r"[^\W_]+", q)) or '""')
    order = {"priority": "IFNULL(p.tier,99), IFNULL(p.year_band,99), p.year DESC, p.id DESC",
             "added": "p.id DESC", "year": "p.year DESC, p.id DESC", "title": "p.title",
             # by displayed venue name (venue_short, else venue), A→Z case-insensitive; papers without a venue last
             "venue": "(COALESCE(NULLIF(p.venue_short,''), NULLIF(p.venue,'')) IS NULL), "
                      "LOWER(COALESCE(NULLIF(p.venue_short,''), p.venue)), p.year DESC, p.id DESC"}[sort]
    sql = f"SELECT p.* FROM papers p {'WHERE ' + ' AND '.join(where) if where else ''} ORDER BY {order} LIMIT ? OFFSET ?"
    rows = [paper_dict(con, r) for r in con.execute(sql, args + [limit, offset])]
    if min_level is not None:
        rows = [r for r in rows if r["level"] >= int(min_level)]
    return rows


def show(con, pid: int) -> dict:
    r = con.execute("SELECT * FROM papers WHERE id=?", (pid,)).fetchone()
    if not r:
        raise SystemExit(f"error: no paper {pid}")
    p = paper_dict(con, r)
    note = NOTE_DIR / f"{pid}.txt"
    p["note"] = note.read_text(encoding="utf-8") if note.exists() else ""
    p["relations"] = [dict(x) for x in con.execute("""
        SELECT r.id, r.rel_type, r.note, 'out' AS dir, q.id AS other_id, q.title AS other_title FROM relations r
          JOIN papers q ON q.id=r.dst_id WHERE r.src_id=?
        UNION ALL
        SELECT r.id, r.rel_type, r.note, 'in', q.id, q.title FROM relations r
          JOIN papers q ON q.id=r.src_id WHERE r.dst_id=?""", (pid, pid))]
    p["found_in"] = [dict(x) for x in con.execute("""
        SELECT s.id, s.query, s.run_at, h.reason FROM search_hits h JOIN searches s ON s.id=h.search_id
        WHERE h.paper_id=? ORDER BY s.id DESC""", (pid,))]
    p["queue"] = [dict(x) for x in con.execute("SELECT * FROM queue WHERE paper_id=? AND status='pending'", (pid,))]
    return p


# ---------------------------------------------------------------- files: PDFs and notes


def attach_pdf(con, pid: int, path: str, copy: bool = False) -> str:
    src = Path(path).expanduser().resolve()
    if not src.exists():
        raise SystemExit(f"error: file not found: {src}")
    with open(src, "rb") as f:
        if f.read(5) != b"%PDF-":
            raise SystemExit(f"error: not a PDF: {src}")
    if copy:
        dst = PDF_DIR / f"{pid}_{re.sub(r'[^A-Za-z0-9]+', '_', src.stem)[:60]}.pdf"
        shutil.copy2(src, dst)
        con.execute("UPDATE papers SET pdf_path=?, pdf_kind='managed', updated_at=? WHERE id=?", (str(dst), now(), pid))
        return str(dst)
    con.execute("UPDATE papers SET pdf_path=?, pdf_kind='linked', updated_at=? WHERE id=?", (str(src), now(), pid))
    return str(src)


def detach_pdf(con, pid: int, delete_copy: bool = False) -> dict:
    """Unlink a paper's PDF. Everything derived from that PDF goes with it: an abstract extracted from it
    (abstract_source='pdf') is cleared, so the next PDF (or OpenAlex etc.) can fill it again.
    The file itself is kept, except a managed copy in library/pdfs when delete_copy=True
    (and no other paper uses it). Linked files elsewhere on disk are never deleted."""
    r = con.execute("SELECT pdf_path, pdf_kind, abstract_source FROM papers WHERE id=?", (pid,)).fetchone()
    if not r:
        raise SystemExit(f"error: no paper {pid}")
    out = {"unlinked": bool(r["pdf_path"]), "abstract_cleared": False, "copy_deleted": False}
    if r["abstract_source"] == "pdf":
        con.execute("UPDATE papers SET abstract=NULL, abstract_source=NULL WHERE id=?", (pid,))
        out["abstract_cleared"] = True
    con.execute("UPDATE papers SET pdf_path=NULL, pdf_kind=NULL, updated_at=? WHERE id=?", (now(), pid))
    msg = f"{now()} pdf unlinked: {r['pdf_path']}" + ("; pdf-derived abstract cleared" if out["abstract_cleared"] else "")
    if r["pdf_path"] and delete_copy and r["pdf_kind"] == "managed":
        f = Path(r["pdf_path"]).resolve()
        shared = con.execute("SELECT 1 FROM papers WHERE pdf_path=? AND id<>?", (r["pdf_path"], pid)).fetchone()
        if f.parent == PDF_DIR.resolve() and f.exists() and not shared:
            f.unlink()
            out["copy_deleted"] = True
            msg += "; library copy deleted"
    old = con.execute("SELECT enrich_log FROM papers WHERE id=?", (pid,)).fetchone()[0] or ""
    con.execute("UPDATE papers SET enrich_log=? WHERE id=?", ((old + "\n" if old else "") + msg, pid))
    return out


def note_title(path: Path) -> str:
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[:40]:
            m = re.match(r"^(?:title:\s*|#\s+)(.+)$", line.strip())
            if m:
                return m.group(1).strip().strip('"')
    except OSError:
        pass
    return path.stem


def link_note(con, pid: int, path: str) -> str:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise SystemExit(f"error: file not found: {p}")
    con.execute("INSERT OR IGNORE INTO ai_notes (paper_id, path, title, created_at) VALUES (?,?,?,?)",
                (pid, str(p), note_title(p), now()))
    return str(p)


def scan_notes(con) -> list[dict]:
    """Link Markdown files under library/ai_notes to papers by arXiv id, DOI, or title."""
    known = {r["path"] for r in con.execute("SELECT path FROM ai_notes")}
    linked = []
    for f in AI_DIR.rglob("*.md"):
        rel = f.relative_to(AI_DIR).parts
        # skip concept library (_概念/...), image folders and files starting with "_"
        if str(f.resolve()) in known or any(p.startswith("_") for p in rel) or "assets" in rel[:-1]:
            continue
        text = f.read_text(encoding="utf-8", errors="replace")[:20000]
        if "generated_by:" in text[:300]:   # auto-generated index (MOC) pages written by note tools
            continue
        pid = None
        for m in re.finditer(r"(?:arxiv\.org/(?:abs|pdf|html)/|arXiv:\s*)(\d{4}\.\d{4,5})", text, re.I):
            r = con.execute("SELECT id FROM papers WHERE arxiv=?", (m.group(1),)).fetchone()
            if r:
                pid = r["id"]; break
        if pid is None:
            for m in re.finditer(r"(10\.\d{4,9}/[^\s\)\]>\"']+)", text):
                r = con.execute("SELECT id FROM papers WHERE doi=?", (m.group(1).lower().rstrip(".,"),)).fetchone()
                if r:
                    pid = r["id"]; break
        if pid is None:
            r = con.execute("SELECT id FROM papers WHERE norm_title=?", (norm_title(note_title(f)),)).fetchone()
            pid = r["id"] if r else None
        if pid:
            link_note(con, pid, str(f))
            linked.append({"paper_id": pid, "note": str(f)})
    return linked


def read_note(pid: int) -> str:
    f = NOTE_DIR / f"{pid}.txt"
    return f.read_text(encoding="utf-8") if f.exists() else ""


def write_note(pid: int, text: str):
    f = NOTE_DIR / f"{pid}.txt"
    if text.strip():
        f.write_text(text, encoding="utf-8")
    elif f.exists():
        f.unlink()


# ---------------------------------------------------------------- BibTeX


def bib_for(con, p) -> str:
    if p["dblp_key"]:
        dcon = dblp_query.connect()
        r = dcon.execute(f"SELECT {dblp_query.FIELDS} FROM pubs p WHERE p.key=?", (p["dblp_key"],)).fetchone()
        if r:
            return dblp_query.bibtex(dcon, r)
    first = (p["authors"] or "anon").split(";")[0].strip().split()[-1] if p["authors"] else "anon"
    key = re.sub(r"\W", "", f"{first}{p['year'] or ''}{(norm_title(p['title']) or 'x')[:12]}")
    names = " and\n                  ".join(a.strip() for a in (p["authors"] or "").split(";") if a.strip())
    conf = (p["venue_short"] or "") in {v.get("short") for v in (dblp_query.load_config().get("venues") or [])
                                         if v.get("type") == "conference"}
    kind = "misc" if p["pub_status"] in ("claimed", "preprint", "under_review") and not p["doi"] else (
        "inproceedings" if conf else "article")
    f = {"author": names, "title": p["title"],
         ("booktitle" if kind == "inproceedings" else "journal"): p["venue"] if kind != "misc" else None,
         "year": p["year"], "doi": p["doi"], "url": p["url"]}
    if p["arxiv"] and not p["doi"]:
        f.update(eprinttype="arXiv", eprint=p["arxiv"])
    if p["pub_status"] in ("claimed", "under_review", "accepted"):
        f["note"] = {"claimed": "Accepted per arXiv comment; not yet verified",
                     "under_review": "Under review", "accepted": "Accepted; proceedings pending"}[p["pub_status"]]
    body = ",\n".join(f"  {k:<12} = {{{v}}}" for k, v in f.items() if v not in (None, ""))
    return f"@{kind}{{{key},\n{body}\n}}"


def export_bib(con, pids: list[int]) -> str:
    rows = [con.execute("SELECT * FROM papers WHERE id=?", (i,)).fetchone() for i in pids]
    return "\n\n".join(bib_for(con, r) for r in rows if r)


# ---------------------------------------------------------------- import


def import_results(con, data: dict | list, keep: set[int] | None, group: str | None, search_id: int | None,
                   query: str | None = None) -> dict:
    results = data.get("results", data) if isinstance(data, dict) else data
    if search_id is None:
        summ = data.get("summary", {}) if isinstance(data, dict) else {}
        search_id = con.execute("INSERT INTO searches (query, params, kind, run_at, n_candidates) VALUES (?,?,?,?,?)",
                                (query or summ.get("query"), json.dumps(summ, ensure_ascii=False),
                                 "import", now(), len(results))).lastrowid
    added, merged, pids = 0, 0, []
    for i, r in enumerate(results, 1):
        if keep is not None and i not in keep and r.get("cand") not in keep:
            continue
        rec = rec_from_dblp(r) if "key" in r and "venue_key" in r else rec_from_fresh(r)
        pid, created = upsert(con, rec)
        added += created; merged += not created
        pids.append(pid)
        con.execute("INSERT OR IGNORE INTO search_hits VALUES (?,?,?,?)", (search_id, pid, i, r.get("reason")))
    if group and pids:
        add_to_group(con, pids, group)
    con.execute("UPDATE searches SET n_kept=? WHERE id=?", (len(pids), search_id))
    return {"search_id": search_id, "added": added, "merged_into_existing": merged, "paper_ids": pids}


# ---------------------------------------------------------------- CLI


def ids_arg(s: str) -> list[int]:
    return [int(x) for x in re.split(r"[,\s]+", s.strip()) if x]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("add"); s.add_argument("ids", nargs="+"); s.add_argument("--group"); s.add_argument("--status")
    s = sub.add_parser("import"); s.add_argument("file"); s.add_argument("--keep"); s.add_argument("--group")
    s.add_argument("--search-id", type=int); s.add_argument("--query")
    s.add_argument("--keep-run", action="store_true", help="keep the data/runs file (default: delete it after import)")
    s = sub.add_parser("show"); s.add_argument("pid", type=int)
    s = sub.add_parser("list"); s.add_argument("--group"); s.add_argument("--status"); s.add_argument("--starred", action="store_true")
    s.add_argument("--level", type=int); s.add_argument("--q"); s.add_argument("--tier"); s.add_argument("--limit", type=int, default=100)
    s.add_argument("--unassigned", action="store_true", help="with a big group: only papers in none of its sub-groups")
    s = sub.add_parser("set"); s.add_argument("pids", nargs="+", type=int); s.add_argument("--status", choices=STATUSES)
    s.add_argument("--star", action="store_true"); s.add_argument("--unstar", action="store_true")
    g = sub.add_parser("group", help="two-level groups: '大组' or '大组/小组'")
    g.add_argument("action", choices=["create", "rename", "delete", "list", "add", "remove", "move", "reparent"])
    g.add_argument("name", nargs="?", help="group path '大组' or '大组/小组' (or numeric id)")
    g.add_argument("arg", nargs="?", help="rename: new name · add/remove/move: paper ids · reparent: new big group or '-'")
    g.add_argument("--description")
    s = sub.add_parser("relate"); s.add_argument("src", type=int); s.add_argument("dst", type=int); s.add_argument("type")
    s.add_argument("--note")
    s = sub.add_parser("attach-pdf"); s.add_argument("pid", type=int); s.add_argument("path"); s.add_argument("--copy", action="store_true")
    s = sub.add_parser("detach-pdf"); s.add_argument("pid", type=int); s.add_argument("--delete-copy", action="store_true")
    s = sub.add_parser("link-note"); s.add_argument("pid", type=int); s.add_argument("path")
    sub.add_parser("scan-notes")
    s = sub.add_parser("note"); s.add_argument("pid", type=int); s.add_argument("--set"); s.add_argument("--file")
    s = sub.add_parser("bib"); s.add_argument("--group"); s.add_argument("--ids"); s.add_argument("--starred", action="store_true")
    s.add_argument("-o", "--out")
    q = sub.add_parser("queue"); q.add_argument("action", nargs="?", default="list", choices=["list", "add", "done", "fail"])
    q.add_argument("a1", nargs="?"); q.add_argument("a2", nargs="?"); q.add_argument("--message")
    sub.add_parser("recompute-tiers")
    sub.add_parser("stats")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    con = connect()
    out = None

    if a.cmd == "add":
        added, missing = [], []
        for ident in a.ids:
            d = lookup_dblp(ident)
            if not d:
                missing.append(ident); continue
            pid, created = upsert(con, rec_from_dblp(d))
            added.append({"paper_id": pid, "created": created, "title": d["title"]})
            if a.status:
                con.execute("UPDATE papers SET status=? WHERE id=?", (a.status, pid))
        if a.group and added:
            add_to_group(con, [x["paper_id"] for x in added], a.group)
        out = {"added": added, "not_in_local_dblp": missing,
               "hint": "use import with a fresh.py/search.py result file for papers dblp lacks" if missing else None}
    elif a.cmd == "import":
        data = json.loads(Path(a.file).read_text(encoding="utf-8"))
        out = import_results(con, data, set(ids_arg(a.keep)) if a.keep else None, a.group, a.search_id, a.query)
        con.commit()
        if not a.keep_run:  # the candidate pool is consumed; the search record keeps the provenance
            out["run_file_removed"] = scratch.consume_run(a.file)
    elif a.cmd == "show":
        out = show(con, a.pid)
    elif a.cmd == "list":
        rows = list_papers(con, a.group, a.status, a.starred, a.level, a.q, a.tier, a.limit, unassigned=a.unassigned)
        for p in rows:
            badge = f"T{p['tier']}" if p["tier"] else "-"
            star = "*" if p["starred"] else " "
            print(f"{p['id']:>5}{star} L{p['level']} [{badge}] {p['title'][:90]} | {p['venue_short']} {p['year']} | {p['status']}"
                  + (f" | groups: {', '.join(p['groups'])}" if p["groups"] else ""))
        print(f"({len(rows)} papers)")
    elif a.cmd == "set":
        if a.status:
            con.executemany("UPDATE papers SET status=?, updated_at=? WHERE id=?", [(a.status, now(), p) for p in a.pids])
        if a.star or a.unstar:
            con.executemany("UPDATE papers SET starred=? WHERE id=?", [(1 if a.star else 0, p) for p in a.pids])
        out = {"updated": a.pids}
    elif a.cmd == "group":
        if a.action == "list":
            for top in group_tree(con):
                print(f"[{top['id']:>3}] {top['name']}  ({top['n']} 篇，未分小组 {top['unassigned']})"
                      + (f"  — {top['description']}" if top["description"] else ""))
                for c in top["children"]:
                    print(f"      [{c['id']:>3}] └ {c['name']}  ({c['n']} 篇)" + (f"  — {c['description']}" if c["description"] else ""))
        elif a.action == "create":
            gid = group_id(con, a.name, create=True)
            if a.description:
                con.execute("UPDATE groups SET description=? WHERE id=?", (a.description, gid))
            out = {"group_id": gid, "path": group_path(con, gid)}
        elif a.action == "rename":
            gid = group_id(con, a.name)
            con.execute("UPDATE groups SET name=? WHERE id=?", (check_name(a.arg), gid))
            out = {"renamed": group_path(con, gid)}
        elif a.action == "delete":
            gid = group_id(con, a.name)
            subs = con.execute("SELECT COUNT(*) FROM groups WHERE parent_id=?", (gid,)).fetchone()[0]
            con.execute("DELETE FROM groups WHERE id=?", (gid,))
            out = {"deleted": a.name, "sub_groups_deleted": subs, "papers_kept": True}
        elif a.action == "add":
            gid = add_to_group(con, ids_arg(a.arg), a.name); out = {"group": group_path(con, gid), "added": ids_arg(a.arg)}
        elif a.action == "remove":
            gid = remove_from_group(con, ids_arg(a.arg), a.name); out = {"group": group_path(con, gid), "removed": ids_arg(a.arg)}
        elif a.action == "move":
            gid = move_to_subgroup(con, ids_arg(a.arg), a.name); out = {"moved_into": group_path(con, gid), "papers": ids_arg(a.arg)}
        elif a.action == "reparent":
            gid = reparent_group(con, a.name, a.arg); out = {"now": group_path(con, gid)}
    elif a.cmd == "relate":
        con.execute("INSERT OR REPLACE INTO relations (src_id, dst_id, rel_type, note, created_at) VALUES (?,?,?,?,?)",
                    (a.src, a.dst, a.type, a.note, now()))
        out = {"related": [a.src, a.dst, a.type]}
    elif a.cmd == "attach-pdf":
        out = {"pdf": attach_pdf(con, a.pid, a.path, a.copy)}
        con.commit()
        import enrich   # lazy: enrich imports this module
        ab, info = enrich.abstract_from_pdf(con, a.pid, replace=True)
        out.update(abstract_from_pdf=bool(ab), abstract_info=info)
    elif a.cmd == "detach-pdf":
        out = detach_pdf(con, a.pid, a.delete_copy)
    elif a.cmd == "link-note":
        out = {"note": link_note(con, a.pid, a.path)}
    elif a.cmd == "scan-notes":
        out = {"linked": scan_notes(con)}
    elif a.cmd == "note":
        if a.set is not None or a.file:
            write_note(a.pid, a.set if a.set is not None else Path(a.file).read_text(encoding="utf-8"))
        print(read_note(a.pid))
    elif a.cmd == "bib":
        if a.ids:
            pids = ids_arg(a.ids)
        else:
            pids = [p["id"] for p in list_papers(con, group=a.group, starred=a.starred, limit=100000)]
        text = export_bib(con, pids)
        if a.out:
            Path(a.out).write_text(text + "\n", encoding="utf-8"); out = {"written": a.out, "entries": len(pids)}
        else:
            print(text)
    elif a.cmd == "queue":
        if a.action == "list":
            out = [dict(r) for r in con.execute("""SELECT q.*, p.title FROM queue q JOIN papers p ON p.id=q.paper_id
                   WHERE q.status='pending' ORDER BY q.id""")]
        elif a.action == "add":
            out = {"queue_id": con.execute("INSERT INTO queue (paper_id, task, created_at) VALUES (?,?,?)",
                                           (int(a.a1), a.a2, now())).lastrowid}
        else:
            con.execute("UPDATE queue SET status=?, message=?, done_at=? WHERE id=?",
                        ("done" if a.action == "done" else "failed", a.message, now(), int(a.a1)))
            out = {a.action: int(a.a1), "scratch_removed": scratch.remove_task(f"queue-{int(a.a1)}")}
    elif a.cmd == "recompute-tiers":
        out = {"updated": recompute_tiers(con)}
    elif a.cmd == "stats":
        papers = [paper_dict(con, r) for r in con.execute("SELECT * FROM papers")]
        out = {"papers": len(papers),
               "by_level": {f"L{i}": sum(p["level"] == i for p in papers) for i in range(4)},
               "by_status": {s: sum(p["status"] == s for p in papers) for s in STATUSES},
               "by_tier": {str(t): sum(p["tier"] == t for p in papers) for t in sorted({p["tier"] for p in papers}, key=lambda x: x or 99)},
               "groups": con.execute("SELECT COUNT(*) FROM groups").fetchone()[0],
               "pending_queue": con.execute("SELECT COUNT(*) FROM queue WHERE status='pending'").fetchone()[0]}
    con.commit()
    if out is not None:
        print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
