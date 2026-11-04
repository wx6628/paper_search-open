"""Find recent papers in priority venues that the local dblp snapshot has not indexed yet.

Usage:
  python -I ps/fresh.py "<query>" [--tier 1] [--since 2026-01-01] [--limit 25] [--json]
                        [--under-review] [--no-enrich] [--sources openalex,crossref,openreview,arxiv]

Per-venue sources come from config/venues.yaml `fresh:` lists:
  openalex   one call covering every venue with an OpenAlex source id      (~$0.001)
  crossref   one call for journals (ISSN filter) + one per LNCS conference  (polite pool)
  openreview accepted papers of <venue>/<year>/Conference (+ submissions under review with --under-review)
  arxiv      one call: preprints whose comments claim a tier venue (e.g. "Accepted to NeurIPS 2026")
  springer   (abstract enrichment for 10.1007 DOIs via the Springer Meta API)
Every call goes through netlib (rate limits, budgets, ledger). Results already present in the
local dblp library (by DOI or normalised title) are dropped, so the output is the dblp gap.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import netlib  # noqa: E402
import scratch  # noqa: E402

ROOT = netlib.ROOT
DBLP_DB = ROOT / "data" / "dblp.sqlite"
RUNS = ROOT / "data" / "runs"
TODAY = dt.date.today()

# ---------------------------------------------------------------- config / helpers


def load_venues(tiers: set[int]) -> list[dict]:
    import yaml
    cfg = yaml.safe_load((ROOT / "config" / "venues.yaml").read_text(encoding="utf-8")) or {}
    bands = cfg.get("year_priority") or []
    vs = [v for v in cfg.get("venues") or [] if v.get("tier") in tiers]
    for v in vs:
        v["_bands"] = bands
    return vs


def year_band(year, bands) -> int | None:
    for i, b in enumerate(bands, 1):
        if year and (b.get("from") is None or year >= b["from"]) and (b.get("to") is None or year <= b["to"]):
            return i
    return None


def norm_title(t: str | None) -> str:
    return re.sub(r"[^\w]+", "", (t or "").lower())


def clean(s: str | None) -> str | None:
    if not s:
        return None
    s = re.sub(r"<[^>]+>", " ", s)            # Crossref JATS tags
    return re.sub(r"\s+", " ", html.unescape(s)).strip() or None


def inverted_to_text(inv: dict | None) -> str | None:
    if not inv:
        return None
    pos = {}
    for word, idx in inv.items():
        for i in idx:
            pos[i] = word
    return " ".join(pos[i] for i in sorted(pos))


def query_tokens(q: str) -> list[str]:
    stop = {"a", "an", "the", "of", "for", "and", "in", "on", "with", "to", "via", "by"}
    return [t for t in re.findall(r"[^\W_]+", q.lower()) if t not in stop]


def lexical_score(rec: dict, toks: list[str]) -> float:
    """Cheap cross-source relevance: title hits count 3x, abstract hits 1x (prefix match ~ stemming)."""
    title = (rec.get("title") or "").lower()
    abstract = (rec.get("abstract") or "").lower()
    score = 0.0
    for t in toks:
        stem = t[:6] if len(t) > 6 else t
        score += 3 * (stem in title) + (stem in abstract)
    return score / max(len(toks), 1)


def new_rec(**kw) -> dict:
    base = dict(title=None, authors=None, year=None, pub_date=None, venue=None, venue_short=None, tier=None,
                doi=None, arxiv=None, openreview=None, abstract=None, url=None, status="published",
                sources=[])
    base.update(kw)
    if base["doi"]:
        base["doi"] = re.sub(r"^https?://(dx\.)?doi\.org/", "", base["doi"]).lower()
    return base


# ---------------------------------------------------------------- sources


def from_openalex(q: str, venues: list[dict], since: dt.date, limit: int) -> list[dict]:
    vs = {v["openalex"]: v for v in venues if v.get("openalex") and "openalex" in (v.get("fresh") or [])}
    if not vs:
        return []
    d = netlib.call("openalex", "https://api.openalex.org/works", {
        "search": q, "per_page": min(limit, 100), "sort": "relevance_score:desc",
        "filter": f"primary_location.source.id:{'|'.join(vs)},from_publication_date:{since},type:article|preprint|review",
        "select": "id,doi,title,publication_date,publication_year,primary_location,ids,abstract_inverted_index,authorships",
        "api_key": netlib.env("OPENALEX_API_KEY")})
    out = []
    for w in d.get("results", []):
        src = ((w.get("primary_location") or {}).get("source") or {}).get("id", "").rsplit("/", 1)[-1]
        v = vs.get(src)
        if not v:
            continue
        arx = ((w.get("ids") or {}).get("arxiv") or "").rsplit("/", 1)[-1] or None
        out.append(new_rec(
            title=clean(w.get("title")), year=w.get("publication_year"), pub_date=w.get("publication_date"),
            authors="; ".join(a["author"]["display_name"] for a in (w.get("authorships") or [])[:30]) or None,
            venue=v["name"], venue_short=v["short"], tier=v["tier"], doi=w.get("doi"), arxiv=arx,
            abstract=inverted_to_text(w.get("abstract_inverted_index")), url=w.get("doi") or w.get("id"),
            sources=["openalex"]))
    return out


def _crossref_items(params: dict) -> list[dict]:
    params = {**params, "mailto": netlib.env("CROSSREF_MAILTO"),
              "select": "DOI,title,author,container-title,published,abstract,type,ISSN"}
    return netlib.call("crossref", "https://api.crossref.org/works", params)["message"]["items"]


def _crossref_rec(it: dict, v: dict) -> dict:
    parts = (it.get("published") or {}).get("date-parts") or [[None]]
    p = parts[0]
    authors = "; ".join(" ".join(x for x in (a.get("given"), a.get("family")) if x) for a in (it.get("author") or [])[:30])
    return new_rec(title=clean((it.get("title") or [None])[0]), year=p[0],
                   pub_date="-".join(f"{x:02d}" if i else str(x) for i, x in enumerate(p) if x),
                   authors=authors or None, venue=v["name"], venue_short=v["short"], tier=v["tier"],
                   doi=it.get("DOI"), abstract=clean(it.get("abstract")),
                   url=f"https://doi.org/{it.get('DOI')}", sources=["crossref"])


def from_crossref(q: str, venues: list[dict], since: dt.date, limit: int) -> list[dict]:
    out = []
    use = [v for v in venues if "crossref" in (v.get("fresh") or [])]
    journals = [v for v in use if v.get("issn")]
    if journals:
        by_issn = {i: v for v in journals for i in v["issn"]}
        flt = ",".join(f"issn:{i}" for i in by_issn) + f",from-pub-date:{since}"
        for it in _crossref_items({"query.bibliographic": q, "filter": flt, "rows": min(limit, 100)}):
            v = next((by_issn[i] for i in it.get("ISSN") or [] if i in by_issn), None)
            if v:
                out.append(_crossref_rec(it, v))
    # LNCS-style proceedings (e.g. ECCV): match the book title, one call per venue
    for v in use:
        if v.get("issn") or v.get("type") != "conference" or "springer" not in (v.get("fresh") or []):
            continue
        items = _crossref_items({"query.bibliographic": q, "query.container-title": v["name"],
                                 "filter": f"type:book-chapter,from-pub-date:{since}", "rows": min(limit, 50)})
        for it in items:
            ct = " ".join(it.get("container-title") or []).lower()
            if v["short"].lower() in ct or v["name"].lower()[:30] in ct:
                out.append(_crossref_rec(it, v))
    return out


def from_openreview(q: str, venues: list[dict], since: dt.date, limit: int, under_review: bool) -> list[dict]:
    out = []
    for v in venues:
        if not v.get("openreview") or "openreview" not in (v.get("fresh") or []):
            continue
        for year in range(since.year, TODAY.year + 2):
            group = f"{v['openreview']}/{year}/Conference"
            try:
                d = netlib.call("openreview", "https://api2.openreview.net/notes/search",
                                {"term": q, "group": group, "limit": min(limit, 100), "type": "terms"})
            except netlib.SourceDisabled as e:
                print(f"warning: {e}", file=sys.stderr)
                return out
            for n in d.get("notes", []):
                c = n.get("content") or {}
                g = lambda k: (c.get(k) or {}).get("value") if isinstance(c.get(k), dict) else c.get(k)
                vid = g("venueid") or ""
                if vid == group:
                    status = "accepted"
                elif under_review and vid == f"{group}/Submission":
                    status = "under_review"
                else:
                    continue  # rejected / withdrawn / workshop
                out.append(new_rec(
                    title=clean(g("title")), year=year, authors="; ".join(g("authors") or []) or None,
                    venue=g("venue") or v["name"], venue_short=v["short"], tier=v["tier"],
                    openreview=n.get("forum") or n.get("id"), abstract=clean(g("abstract")),
                    url=f"https://openreview.net/forum?id={n.get('forum') or n.get('id')}",
                    status=status, sources=["openreview"]))
    return out


def from_arxiv(q: str, venues: list[dict], since: dt.date, limit: int) -> list[dict]:
    confs = [v for v in venues if v.get("type") == "conference"]
    if not confs:
        return []
    toks = query_tokens(q)[:6]
    if not toks:
        return []
    terms = " AND ".join(f"(ti:{t} OR abs:{t})" for t in toks)
    names = " OR ".join(f"co:{v['short']}" for v in confs)
    window = f"submittedDate:[{since.strftime('%Y%m%d')}0000 TO {TODAY.strftime('%Y%m%d')}2359]"
    text = netlib.call("arxiv", "https://export.arxiv.org/api/query", {
        "search_query": f"({terms}) AND ({names}) AND {window}", "max_results": min(limit, 100),
        "sortBy": "relevance"})
    ns = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
    out = []
    for e in ET.fromstring(text).findall("a:entry", ns):
        comment = (e.findtext("x:comment", default="", namespaces=ns) or "")
        v, yr = None, None
        for cand in confs:  # the comment must name the venue *and* a recent year, e.g. "NeurIPS 2026"
            m = re.search(rf"\b{re.escape(cand['short'])}\W{{0,3}}('?\d{{2,4}})", comment, re.I)
            if m and re.search(r"accept|appear|to be published|camera|conference", comment, re.I):
                y = int(m.group(1).lstrip("'"))
                y = y + 2000 if y < 100 else y
                if y >= since.year:
                    v, yr = cand, y
                    break
        if not v:
            continue
        aid = e.findtext("a:id", namespaces=ns).rsplit("/abs/", 1)[-1]
        out.append(new_rec(
            title=clean(e.findtext("a:title", namespaces=ns)), year=yr,
            pub_date=(e.findtext("a:published", namespaces=ns) or "")[:10],
            authors="; ".join(a.findtext("a:name", namespaces=ns) for a in e.findall("a:author", ns)) or None,
            venue=f"{v['short']} {yr} (per arXiv comment)", venue_short=v["short"], tier=v["tier"],
            arxiv=re.sub(r"v\d+$", "", aid), abstract=clean(e.findtext("a:summary", namespaces=ns)),
            url=f"https://arxiv.org/abs/{aid}", status="claimed", sources=["arxiv"]))
    return out


def enrich_springer(recs: list[dict], max_calls: int) -> int:
    n = 0
    for r in recs:
        if n >= max_calls:
            break
        if r["abstract"] or not (r["doi"] or "").startswith("10.1007/"):
            continue
        try:
            d = netlib.call("springer_meta", "https://api.springernature.com/meta/v2/json",
                            {"q": f"doi:{r['doi']}", "api_key": netlib.env("SPRINGER_META_API_KEY")})
        except (netlib.QuotaExceeded, netlib.SourceDisabled) as e:
            print(f"warning: {e}", file=sys.stderr)
            break
        n += 1
        rec = (d.get("records") or [{}])[0]
        ab = rec.get("abstract")
        if isinstance(ab, dict):
            ab = " ".join(str(x) for x in ab.values())
        if ab:
            r["abstract"] = clean(str(ab))
            r["sources"].append("springer")
    return n


# ---------------------------------------------------------------- merge / dblp gap


def merge(recs: list[dict]) -> list[dict]:
    by_key: dict[str, dict] = {}
    order = []
    for r in recs:
        keys = [f"doi:{r['doi']}"] if r["doi"] else []
        keys.append(f"t:{norm_title(r['title'])}")
        hit = next((by_key[k] for k in keys if k in by_key), None)
        if hit is None:
            order.append(r)
            for k in keys:
                by_key[k] = r
            continue
        for f in ("authors", "pub_date", "doi", "arxiv", "openreview", "abstract", "year"):
            hit[f] = hit[f] or r[f]
        # a real publication beats an arXiv venue claim
        if hit["status"] == "claimed" and r["status"] != "claimed":
            hit.update(venue=r["venue"], status=r["status"], url=r["url"])
        hit["sources"] = sorted(set(hit["sources"]) | set(r["sources"]))
        for k in keys:
            by_key[k] = hit
    return order


def drop_known_to_dblp(recs: list[dict]) -> tuple[list[dict], int]:
    if not DBLP_DB.exists():
        return recs, 0
    con = sqlite3.connect(f"file:{DBLP_DB.as_posix()}?mode=ro", uri=True)
    kept, dropped = [], 0
    for r in recs:
        found = False
        if r["doi"]:
            found = con.execute("SELECT 1 FROM pubs WHERE doi=? LIMIT 1", (r["doi"],)).fetchone() is not None
        if not found and r["arxiv"]:
            found = con.execute("SELECT 1 FROM pubs WHERE arxiv=? AND venue_key!='journals/corr' LIMIT 1",
                                (r["arxiv"],)).fetchone() is not None
        if not found and r["title"]:
            toks = re.findall(r"[^\W_]+", r["title"])[:12]
            if toks:
                fts = 'title : "' + " ".join(toks) + '"'
                for (t,) in con.execute("SELECT p.title FROM pubs_fts JOIN pubs p ON p.rowid=pubs_fts.rowid "
                                        "WHERE pubs_fts MATCH ? AND p.venue_key!='journals/corr' LIMIT 5", (fts,)):
                    if norm_title(t) == norm_title(r["title"]):
                        found = True
                        break
        if found:
            dropped += 1
        else:
            kept.append(r)
    return kept, dropped


def dblp_snapshot_date() -> str:
    try:
        con = sqlite3.connect(f"file:{DBLP_DB.as_posix()}?mode=ro", uri=True)
        return dict(con.execute("SELECT k, v FROM meta").fetchall()).get("dump_mtime", "")[:10]
    except sqlite3.Error:
        return ""


# ---------------------------------------------------------------- main


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query")
    ap.add_argument("--tier", action="append", default=None, help="tiers to cover (default 1)")
    ap.add_argument("--since", help="earliest publication date (default: 12 months ago)")
    ap.add_argument("--limit", type=int, default=25, help="results requested per source call")
    ap.add_argument("--sources", default="openalex,crossref,openreview,arxiv")
    ap.add_argument("--under-review", action="store_true", help="include OpenReview submissions under review")
    ap.add_argument("--no-enrich", action="store_true", help="skip Springer abstract enrichment")
    ap.add_argument("--keep-dblp", action="store_true", help="keep results that dblp already has")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    tiers = {int(t) for t in (a.tier or ["1"])}
    since = dt.date.fromisoformat(a.since) if a.since else TODAY - dt.timedelta(days=365)
    venues = load_venues(tiers)
    srcs = set(a.sources.split(","))
    recs, errors = [], {}
    steps = [("openalex", lambda: from_openalex(a.query, venues, since, a.limit)),
             ("crossref", lambda: from_crossref(a.query, venues, since, a.limit)),
             ("openreview", lambda: from_openreview(a.query, venues, since, a.limit, a.under_review)),
             ("arxiv", lambda: from_arxiv(a.query, venues, since, a.limit))]
    counts = {}
    for name, fn in steps:
        if name not in srcs:
            continue
        try:
            got = fn()
            counts[name] = len(got)
            recs += got
        except Exception as e:  # one failing source must not sink the run
            errors[name] = str(e)[:200]
            print(f"warning: {name}: {errors[name]}", file=sys.stderr)

    merged = merge(recs)
    gap, in_dblp = (merged, 0) if a.keep_dblp else drop_known_to_dblp(merged)
    enriched = 0 if a.no_enrich else enrich_springer(gap, max_calls=20)
    toks = query_tokens(a.query)
    for r in gap:
        r["year_band"] = year_band(r["year"], venues[0]["_bands"] if venues else [])
        r["score"] = round(lexical_score(r, toks), 2)
    gap.sort(key=lambda r: (r["tier"] or 99, r["year_band"] or 99, -r["score"]))

    summary = {"query": a.query, "tiers": sorted(tiers), "since": str(since), "dblp_snapshot": dblp_snapshot_date(),
               "per_source": counts, "merged": len(merged), "already_in_dblp": in_dblp, "gap": len(gap),
               "springer_enriched_calls": enriched, "errors": errors}
    RUNS.mkdir(parents=True, exist_ok=True)
    scratch.prune_runs()   # runs never imported expire after RUN_RETENTION_DAYS; imported ones are deleted on import
    run_file = RUNS / f"{dt.datetime.now():%Y%m%d-%H%M%S}_fresh.json"
    run_file.write_text(json.dumps({"summary": summary, "results": gap}, ensure_ascii=False, indent=1), encoding="utf-8")
    summary["saved_to"] = str(run_file.relative_to(ROOT))

    if a.json:
        print(json.dumps({"summary": summary, "results": gap}, ensure_ascii=False, indent=1))
        return
    print(json.dumps(summary, ensure_ascii=False))
    mark = {"published": "", "accepted": " [accepted]", "under_review": " [under review]", "claimed": " [arXiv claim]"}
    for i, r in enumerate(gap, 1):
        ident = f"doi:{r['doi']}" if r["doi"] else (f"arXiv:{r['arxiv']}" if r["arxiv"] else r["url"])
        au = r["authors"] or ""
        au = au.split(";")[0] + " et al." if au.count(";") >= 3 else au
        print(f"{i:>3}. [T{r['tier']}/Y{r['year_band']}] {r['title']}")
        print(f"      {au} | {r['venue_short']} {r['pub_date'] or r['year']}{mark[r['status']]} | {ident}"
              f" | abs:{'y' if r['abstract'] else 'n'} | via {','.join(r['sources'])}")


if __name__ == "__main__":
    main()
