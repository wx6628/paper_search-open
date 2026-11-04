"""Build the local dblp SQLite library from the official dblp XML dump.

Usage:
    python -I ps/dblp_build.py --dump data/dblp/dblp.xml.gz --db data/dblp.sqlite [--max-records N]

The dump (dblp.xml.gz) and dblp.dtd must sit in the same folder (the DTD defines the
character entities). The build writes to <db>.building first and swaps it in only on
success, so a failed or interrupted build never damages an existing library.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

from lxml import etree

RECORD_TAGS = ("article", "inproceedings", "proceedings", "book", "incollection",
               "phdthesis", "mastersthesis", "data")  # 'www' = person pages, skipped
BATCH = 20000

SCHEMA = """
CREATE TABLE pubs (
    key        TEXT PRIMARY KEY,   -- dblp record key, e.g. conf/cvpr/HeZRS16
    type       TEXT NOT NULL,      -- article / inproceedings / proceedings / ...
    title      TEXT,
    authors    TEXT,               -- '; '-joined display names (dblp homonym numbers removed)
    year       INTEGER,
    venue      TEXT,               -- journal name or booktitle as given by dblp (e.g. 'CVPR Workshops')
    venue_key  TEXT,               -- stream key, e.g. conf/cvpr, journals/pami
    volume     TEXT,
    number     TEXT,
    pages      TEXT,
    doi        TEXT,               -- lower-cased, no https://doi.org/ prefix
    arxiv      TEXT,               -- arXiv id if dblp links one
    ee         TEXT,               -- first electronic-edition link
    crossref   TEXT,               -- parent proceedings key
    publtype   TEXT,               -- e.g. informal (=preprint), encyclopedia, withdrawn
    mdate      TEXT
);
CREATE TABLE pub_authors (
    key   TEXT NOT NULL,
    pos   INTEGER NOT NULL,
    name  TEXT NOT NULL,           -- raw dblp name incl. homonym number, e.g. 'Wei Wang 0001'
    orcid TEXT
);
CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
"""

POST_SQL = """
CREATE INDEX idx_pubs_venue_year ON pubs(venue_key, year);
CREATE INDEX idx_pubs_year ON pubs(year);
CREATE INDEX idx_pubs_doi ON pubs(doi) WHERE doi IS NOT NULL;
CREATE INDEX idx_pubs_arxiv ON pubs(arxiv) WHERE arxiv IS NOT NULL;
CREATE INDEX idx_pa_key ON pub_authors(key);
CREATE INDEX idx_pa_name ON pub_authors(name);

-- one row per (stream, venue string): lets us map 'CVPR' vs 'CVPR Workshops' etc.
CREATE TABLE venues AS
    SELECT venue_key, venue, type, COUNT(*) AS n, MIN(year) AS first_year, MAX(year) AS last_year
    FROM pubs WHERE venue_key IS NOT NULL AND venue IS NOT NULL AND type != 'proceedings'
    GROUP BY venue_key, venue, type;
CREATE INDEX idx_venues_key ON venues(venue_key);

-- full proceedings titles (e.g. '2016 IEEE Conference on Computer Vision and Pattern Recognition, ...')
CREATE TABLE venue_titles AS
    SELECT venue_key, title, year FROM pubs WHERE type = 'proceedings' AND venue_key IS NOT NULL;
CREATE INDEX idx_vt_key ON venue_titles(venue_key);

CREATE VIRTUAL TABLE pubs_fts USING fts5(
    title, authors, venue,
    content='pubs', content_rowid='rowid',
    tokenize='porter unicode61 remove_diacritics 2'
);
INSERT INTO pubs_fts(pubs_fts) VALUES('rebuild');
"""

HOMONYM = re.compile(r"\s+\d{4}$")
ARXIV_EE = re.compile(r"arxiv\.org/(?:abs|pdf)/([^\s?#]+?)(?:v\d+)?(?:\.pdf)?$", re.I)
DOI_EE = re.compile(r"^https?://(?:dx\.)?doi\.org/(10\..+)$", re.I)
CORR_KEY = re.compile(r"^journals/corr/abs-(\d{4})-(\d{4,5})$")


def text_of(el) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def venue_key_of(key: str) -> str | None:
    parts = key.split("/")
    if len(parts) >= 3 and parts[0] in ("conf", "journals", "series", "reference", "books"):
        return f"{parts[0]}/{parts[1]}"
    return None


def parse_record(el):
    key = el.get("key")
    rec = {"key": key, "type": el.tag, "publtype": el.get("publtype"), "mdate": el.get("mdate"),
           "title": None, "year": None, "venue": None, "volume": None, "number": None,
           "pages": None, "doi": None, "arxiv": None, "ee": None, "crossref": None}
    authors = []
    for child in el:
        tag = child.tag
        if tag in ("author", "editor"):
            if tag == "editor" and any(a[2] == "author" for a in authors):
                continue
            authors.append((text_of(child), child.get("orcid"), tag))
        elif tag == "title":
            rec["title"] = text_of(child).rstrip(".") or None
        elif tag == "year":
            y = text_of(child)
            rec["year"] = int(y) if y.isdigit() else None
        elif tag in ("journal", "booktitle"):
            rec["venue"] = rec["venue"] or text_of(child)
        elif tag in ("volume", "number", "pages", "crossref"):
            rec[tag] = rec[tag] or text_of(child)
        elif tag == "ee":
            link = text_of(child)
            m = DOI_EE.match(link)
            if m and not rec["doi"]:
                rec["doi"] = m.group(1).lower()
            m = ARXIV_EE.search(link)
            if m and not rec["arxiv"]:
                rec["arxiv"] = m.group(1)
            rec["ee"] = rec["ee"] or link
    if not rec["arxiv"] and key:
        m = CORR_KEY.match(key)
        if m:
            rec["arxiv"] = f"{m.group(1)}.{m.group(2)}"
    if rec["doi"] and rec["doi"].startswith("10.48550/arxiv.") and not rec["arxiv"]:
        rec["arxiv"] = rec["doi"].split("arxiv.", 1)[1]
    # editors only count when there are no authors (proceedings, edited books)
    if any(a[2] == "author" for a in authors):
        authors = [a for a in authors if a[2] == "author"]
    rec["authors"] = "; ".join(HOMONYM.sub("", a[0]) for a in authors) or None
    rec["venue_key"] = venue_key_of(key) if key else None
    return rec, [(key, i, a[0], a[1]) for i, a in enumerate(authors)]


def md5_of(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dump", required=True, type=Path)
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--max-records", type=int, default=0, help="stop after N records (for testing)")
    args = ap.parse_args()

    dump = args.dump.resolve()
    if not (dump.parent / "dblp.dtd").exists():
        print(f"error: dblp.dtd not found next to {dump}", file=sys.stderr)
        return 2
    tmp = args.db.with_suffix(args.db.suffix + ".building")
    tmp.unlink(missing_ok=True)

    con = sqlite3.connect(tmp)
    con.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA temp_store=MEMORY;"
                      "PRAGMA cache_size=-400000;")
    con.executescript(SCHEMA)

    cols = ["key", "type", "title", "authors", "year", "venue", "venue_key", "volume", "number",
            "pages", "doi", "arxiv", "ee", "crossref", "publtype", "mdate"]
    ins_pub = f"INSERT OR IGNORE INTO pubs ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
    ins_auth = "INSERT INTO pub_authors (key, pos, name, orcid) VALUES (?,?,?,?)"

    t0 = time.time()
    n = 0
    pubs, auths = [], []
    with gzip.open(dump, "rb") as fh:
        # lxml resolves the DTD relative to the file name, so dblp.dtd must be alongside the dump
        ctx = etree.iterparse(fh, events=("end",), tag=RECORD_TAGS, load_dtd=True,
                              resolve_entities=True, no_network=True, huge_tree=True)
        for _, el in ctx:
            if el.getparent() is not None and el.getparent().tag != "dblp":
                continue  # nested element with a record-like name; never happens in practice
            rec, a = parse_record(el)
            pubs.append(tuple(rec[c] for c in cols))
            auths.extend(a)
            n += 1
            el.clear(keep_tail=False)
            while el.getprevious() is not None:
                del el.getparent()[0]
            if len(pubs) >= BATCH:
                con.executemany(ins_pub, pubs)
                con.executemany(ins_auth, auths)
                pubs.clear(); auths.clear()
                if n % 500000 == 0:
                    print(f"  {n:>10,} records  {time.time() - t0:7.0f}s", flush=True)
            if args.max_records and n >= args.max_records:
                break
    con.executemany(ins_pub, pubs)
    con.executemany(ins_auth, auths)
    con.commit()
    print(f"parsed {n:,} records in {time.time() - t0:.0f}s; building indexes + full-text index ...", flush=True)

    t1 = time.time()
    con.executescript(POST_SQL)
    md5_file = dump.with_name(dump.name + ".md5")
    meta = {
        "dump_file": dump.name,
        "dump_size": str(dump.stat().st_size),
        "dump_mtime": dt.datetime.fromtimestamp(dump.stat().st_mtime).isoformat(timespec="seconds"),
        "dump_md5_expected": md5_file.read_text().split()[0] if md5_file.exists() else "",
        "records": str(n),
        "partial": "1" if args.max_records else "0",
        "built_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    if not args.max_records:
        meta["dump_md5"] = md5_of(dump)
    con.executemany("INSERT INTO meta VALUES (?,?)", meta.items())
    con.commit()
    print(f"indexes built in {time.time() - t1:.0f}s", flush=True)
    con.execute("PRAGMA optimize")
    con.close()

    os.replace(tmp, args.db)
    print(f"done: {args.db}  ({args.db.stat().st_size / 1e9:.2f} GB, total {time.time() - t0:.0f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
