"""Search orchestration behind the /find command (two steps, Claude screens in between).

Step 1 — gather candidates (deterministic, no LLM):
  python -I ps/search.py gather --q "variant 1" --q "variant 2" ... [--tiers 1,2,3] [--per-tier 40]
         [--others 20] [--fresh-queries 2] [--since YYYY-MM-DD] [--year 2020-] [--abstracts 80] [--no-fresh]
  -> data/runs/<ts>_search.json, plus a compact numbered listing (cand #) for screening.
  Order: local dblp per tier (tier 1 -> 2 -> 3), dblp gap (fresh.py) for priority venues,
  then other venues (non-priority, published only). Candidates hit by several query variants
  rank higher. Missing abstracts of the top --abstracts candidates are fetched from OpenAlex by DOI (free).

Step 2 — keep the relevant ones, import into the library, write the report + BibTeX:
  python -I ps/search.py finalize <run.json> --keep 3,7,12 [--reasons reasons.json] [--group G]
         [--title "研究问题"]
  reasons.json: {"3": "why this paper is relevant", ...} (shown in the report and the library);
                write it under data/tmp/. After a successful finalize the run file and the reasons file are
                deleted (--keep-run keeps the run file). gather's internal fresh.py run files are merged and deleted.
  -> library papers + search record, reports/<date>_<slug>.md, exports/<date>_<slug>.bib
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dblp_query  # noqa: E402
import enrich  # noqa: E402
import library  # noqa: E402
import netlib  # noqa: E402
import scratch  # noqa: E402

ROOT = HERE.parent
RUNS = ROOT / "data" / "runs"
REPORTS = ROOT / "reports"
EXPORTS = ROOT / "exports"


BAND_QUOTA = {1: 0.5, 2: 0.3, 3: 0.2}   # share of each tier's slots per year band (recent first, classics kept)


def dblp_hits(q: str, tiers: list[int] | None, limit: int, year: str | None, others: bool) -> list[dict]:
    """Query the local dblp (relevance order), then pick by year-band quotas so that recent papers
    dominate but highly relevant older work is not crowded out. Unused quota flows to other bands."""
    ns = argparse.Namespace(query=q, venue=None, tier=[str(t) for t in tiers] if tiers else None, year=year,
                            type=None, no_preprints=others, sort="relevance", limit=limit * 6, pool=limit * 6,
                            raw=False, json=True, keep_duplicates=False)
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        dblp_query.cmd_search(ns)
    rows = json.loads(buf.getvalue())["results"]
    if others:
        return [r for r in rows if not r["tier"]][:limit]
    picked, by_band = [], {}
    for r in rows:
        by_band.setdefault(r.get("year_band") or 3, []).append(r)
    for b, share in BAND_QUOTA.items():
        picked += by_band.get(b, [])[: round(limit * share)]
    rest = [r for r in rows if r not in picked]
    picked += rest[: max(0, limit - len(picked))]
    return picked


def to_cand(r: dict, origin: str) -> dict:
    if "key" in r and "venue_key" in r:      # dblp record
        rec = library.rec_from_dblp(r)
        rec["origin"] = origin
    else:                                     # fresh.py record
        rec = library.rec_from_fresh(r)
        rec["origin"] = "fresh:" + ",".join(r.get("sources") or [])
    rec["hits"] = 1
    rec["queries"] = []
    return rec


def merge(cands: list[dict]) -> list[dict]:
    index, out = {}, []
    for c in cands:
        keys = [k for k in (f"doi:{c['doi']}" if c.get("doi") else None, f"arxiv:{c['arxiv']}" if c.get("arxiv") else None,
                            f"t:{library.norm_title(c['title'])}") if k]
        hit = next((index[k] for k in keys if k in index), None)
        if hit is None:
            out.append(c)
            for k in keys:
                index[k] = c
            continue
        hit["queries"] = sorted(set(hit["queries"]) | set(c["queries"]))
        hit["hits"] = len(hit["queries"])
        better = library.RANK.get(c["pub_status"], 0) < library.RANK.get(hit["pub_status"], 0) or (
            (c.get("tier") or 99) < (hit.get("tier") or 99))
        for k, v in c.items():
            if k in ("queries", "hits", "origin"):
                continue
            if v not in (None, "") and (hit.get(k) in (None, "") or (better and k in (
                    "venue", "venue_short", "venue_key", "tier", "year_band", "pub_status", "dblp_key", "url", "year"))):
                hit[k] = v
        for k in keys:
            index[k] = hit
    return out


def cmd_gather(a):
    tiers_all = sorted({v.get("tier") for v in dblp_query.load_config().get("venues") or [] if v.get("tier")})
    tiers = [int(t) for t in a.tiers.split(",")] if a.tiers else tiers_all
    cands: list[dict] = []
    for q in a.q:
        for t in tiers:
            for r in dblp_hits(q, [t], a.per_tier, a.year, others=False):
                c = to_cand(r, f"dblp:tier{t}"); c["queries"] = [q]; cands.append(c)
        if a.others:
            for r in dblp_hits(q, None, a.others * 3, a.year, others=True)[: a.others]:
                c = to_cand(r, "dblp:other"); c["queries"] = [q]; cands.append(c)
    fresh_runs = []
    if not a.no_fresh:
        for q in a.q[: a.fresh_queries]:
            cmd = [sys.executable, "-I", str(HERE / "fresh.py"), q, "--json", "--no-enrich", "--limit", str(a.fresh_limit)]
            for t in tiers:
                cmd += ["--tier", str(t)]
            if a.since:
                cmd += ["--since", a.since]
            p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
            if p.returncode != 0:
                print(f"warning: fresh.py failed for {q!r}: {p.stderr[-300:]}", file=sys.stderr)
                continue
            data = json.loads(p.stdout)
            if data["summary"].get("saved_to"):   # merged into this search run below -> the fresh run file is redundant
                scratch.consume_run(ROOT / data["summary"].pop("saved_to"))
            fresh_runs.append(data["summary"])
            for r in data["results"]:
                c = to_cand(r, "fresh"); c["queries"] = [q]; cands.append(c)
    merged = merge(cands)
    merged.sort(key=lambda c: (c.get("tier") or 99, -c["hits"], c.get("year_band") or 99))

    # abstracts for screening: free OpenAlex single-work lookups for the top candidates with a DOI
    fetched = 0
    for c in merged:
        if fetched >= a.abstracts:
            break
        if c.get("abstract") or not c.get("doi"):
            continue
        try:
            w = enrich.openalex_work(doi=c["doi"])
        except (netlib.QuotaExceeded, netlib.SourceDisabled) as e:
            print(f"warning: {e}", file=sys.stderr); break
        except Exception:
            w = None
        fetched += 1
        if w:
            c["abstract"] = enrich.inverted_to_text(w.get("abstract_inverted_index"))
            c["abstract_source"] = "openalex" if c["abstract"] else None
            c["citations"] = w.get("cited_by_count")
            c["openalex_id"] = (w.get("id") or "").rsplit("/", 1)[-1]
    for i, c in enumerate(merged, 1):
        c["cand"] = i

    RUNS.mkdir(parents=True, exist_ok=True)
    scratch.prune_runs()   # candidate pools that were never finalized expire after RUN_RETENTION_DAYS
    f = RUNS / f"{dt.datetime.now():%Y%m%d-%H%M%S}_search.json"
    summary = {"queries": a.q, "tiers": tiers, "year": a.year, "since": a.since, "candidates": len(merged),
               "by_origin": {}, "fresh_runs": fresh_runs, "abstract_lookups": fetched,
               "dblp_snapshot": dict(dblp_query.connect().execute("SELECT k, v FROM meta").fetchall()).get("dump_mtime", "")[:10]}
    for c in merged:
        o = c["origin"].split(":")[0] + (":" + c["origin"].split(":")[1] if c["origin"].startswith("dblp") else "")
        summary["by_origin"][o] = summary["by_origin"].get(o, 0) + 1
    f.write_text(json.dumps({"summary": summary, "results": merged}, ensure_ascii=False, indent=1), encoding="utf-8")

    print(json.dumps({**summary, "file": str(f.relative_to(ROOT))}, ensure_ascii=False))
    lab = {"published": "", "accepted": " [accepted]", "under_review": " [under review]", "claimed": " [arXiv claim]",
           "preprint": " [preprint]"}
    for c in merged:
        tier = f"T{c['tier']}" if c.get("tier") else "--"
        ab = (c.get("abstract") or "").replace("\n", " ")
        print(f"#{c['cand']} [{tier}/Y{c.get('year_band') or '-'}] x{c['hits']} {c['title']} | "
              f"{c.get('venue_short') or c.get('venue')} {c.get('year')}{lab.get(c['pub_status'], '')}")
        if ab:
            print(f"    {ab[:a.snippet]}")


def slugify(s: str) -> str:
    s = re.sub(r"[^\w一-鿿]+", "-", s.lower()).strip("-")
    return s[:50] or "search"


def cmd_finalize(a):
    data = json.loads(Path(a.file).read_text(encoding="utf-8"))
    summ, cands = data["summary"], data["results"]
    keep = set(library.ids_arg(a.keep))
    reasons = {}
    if a.reasons:
        reasons = {int(k): v for k, v in json.loads(Path(a.reasons).read_text(encoding="utf-8")).items()}
    title = a.title or summ["queries"][0]
    con = library.connect()
    sid = con.execute("INSERT INTO searches (query, params, kind, run_at, n_candidates) VALUES (?,?,?,?,?)",
                      (title, json.dumps(summ, ensure_ascii=False), "find", library.now(), len(cands))).lastrowid
    kept = []
    for c in cands:
        if c["cand"] not in keep:
            continue
        rec = {k: c.get(k) for k in library.PAPER_FIELDS}
        rec["found_by"] = c["origin"]
        pid, _ = library.upsert(con, rec)
        con.execute("INSERT OR REPLACE INTO search_hits VALUES (?,?,?,?)", (sid, pid, c["cand"], reasons.get(c["cand"])))
        kept.append((pid, c))
    if a.group and kept:
        library.add_to_group(con, [p for p, _ in kept], a.group)
    slug = f"{dt.date.today():%Y%m%d}_{slugify(title)}"
    REPORTS.mkdir(exist_ok=True); EXPORTS.mkdir(exist_ok=True)
    rpt, bib = REPORTS / f"{slug}.md", EXPORTS / f"{slug}.bib"
    con.execute("UPDATE searches SET n_kept=?, report_path=? WHERE id=?", (len(kept), str(rpt), sid))
    con.commit()
    bib.write_text(library.export_bib(con, [p for p, _ in kept]) + "\n", encoding="utf-8")
    rpt.write_text(render_report(title, summ, kept, reasons, len(cands), bib, sid, a.group), encoding="utf-8")
    # intermediates are consumed: the search record, report and BibTeX keep everything worth keeping
    cleaned = []
    if not a.keep_run and scratch.consume_run(a.file):
        cleaned.append(Path(a.file).name)
    if a.reasons and (scratch.remove_if_inside(a.reasons, scratch.TMP) or scratch.consume_run(a.reasons)):
        cleaned.append(Path(a.reasons).name)
    print(json.dumps({"search_id": sid, "kept": len(kept), "report": str(rpt.relative_to(ROOT)),
                      "bib": str(bib.relative_to(ROOT)), "group": a.group, "intermediates_removed": cleaned},
                     ensure_ascii=False))


def render_report(title, summ, kept, reasons, n_cands, bib, sid, group) -> str:
    bands = {1: "2024 年及以后", 2: "2021–2023", 3: "2021 年以前"}
    lab = {"accepted": "已录用（会议论文集待出）", "under_review": "审稿中", "claimed": "arXiv 声明录用（未核实）",
           "preprint": "预印本"}
    L = [f"# 文献检索报告：{title}", "",
         f"- 检索时间：{dt.datetime.now():%Y-%m-%d %H:%M}　· 检索编号：{sid}" + (f"　· 已加入分组：{group}" if group else ""),
         f"- 检索式：" + "；".join(f"`{q}`" for q in summ["queries"]),
         f"- 范围：出处层级 {summ['tiers']}" + (f"，年份 {summ['year']}" if summ.get("year") else "")
         + f"；本地 dblp 快照 {summ.get('dblp_snapshot')} + dblp 未收录的新论文（OpenAlex/Crossref/OpenReview/arXiv）",
         f"- 结果：候选 {n_cands} 篇，经标题/摘要筛选保留 **{len(kept)}** 篇", ""]
    groups: dict = {}
    for pid, c in kept:
        groups.setdefault((c.get("tier") or 99, c.get("year_band") or 99), []).append((pid, c))
    for (tier, band), items in sorted(groups.items()):
        head = f"Tier {tier}" if tier != 99 else "其他出处（非优先）"
        L += [f"## {head} · {bands.get(band, '年份未知')}（{len(items)} 篇）", ""]
        for pid, c in items:
            authors = c.get("authors") or ""
            if authors.count(";") >= 3:
                authors = authors.split(";")[0].strip() + " 等"
            links = []
            if c.get("doi"):
                links.append(f"[DOI](https://doi.org/{c['doi']})")
            if c.get("arxiv"):
                links.append(f"[arXiv](https://arxiv.org/abs/{c['arxiv']})")
            if c.get("openreview_id"):
                links.append(f"[OpenReview](https://openreview.net/forum?id={c['openreview_id']})")
            if c.get("dblp_key"):
                links.append(f"[dblp](https://dblp.org/rec/{c['dblp_key']})")
            st = lab.get(c.get("pub_status"))
            L.append(f"### {c['title']}")
            L.append(f"*{authors}* — **{c.get('venue_short') or c.get('venue')} {c.get('year') or ''}**"
                     + (f"（{st}）" if st else "") + f"　{' · '.join(links)}　`库内 #{pid}`")
            if reasons.get(c["cand"]):
                L.append(f"> **相关性**：{reasons[c['cand']]}")
            if c.get("abstract"):
                ab = c["abstract"]
                L.append(f"\n{ab[:600]}{'…' if len(ab) > 600 else ''}")
            L.append("")
    L += ["---", f"BibTeX：`{bib.relative_to(ROOT).as_posix()}`（有 dblp 记录的使用 dblp 规范条目）", ""]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gather")
    g.add_argument("--q", action="append", required=True, help="query variant (repeatable)")
    g.add_argument("--tiers", help="e.g. 1 or 1,2 (default: all tiers in venues.yaml)")
    g.add_argument("--per-tier", type=int, default=40)
    g.add_argument("--others", type=int, default=15, help="non-priority venue results per query (0 = none)")
    g.add_argument("--year", help="dblp year filter, e.g. 2021-")
    g.add_argument("--since", help="fresh: earliest publication date (default 12 months ago)")
    g.add_argument("--fresh-queries", type=int, default=2, help="how many variants also go to fresh.py")
    g.add_argument("--fresh-limit", type=int, default=25)
    g.add_argument("--no-fresh", action="store_true")
    g.add_argument("--abstracts", type=int, default=80, help="free OpenAlex abstract lookups for screening")
    g.add_argument("--snippet", type=int, default=220)
    g.set_defaults(fn=cmd_gather)
    f = sub.add_parser("finalize")
    f.add_argument("file"); f.add_argument("--keep", required=True); f.add_argument("--reasons")
    f.add_argument("--group"); f.add_argument("--title")
    f.add_argument("--keep-run", action="store_true", help="keep the run file (default: delete it once finalized)")
    f.set_defaults(fn=cmd_finalize)
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    a.fn(a)


if __name__ == "__main__":
    main()
