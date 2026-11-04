"""Fill in library papers progressively: L0 -> L1 (abstract, citations, OA link) -> L2 (open-access PDF).

Usage:
  python -I ps/enrich.py meta         [--ids 1,2 | --group G | --all-missing] [--max N] [--title-search]
  python -I ps/enrich.py pdf          [--ids 1,2 | --group G | --all-missing] [--max N]
  python -I ps/enrich.py abstract-pdf [--ids 1,2 | --group G | --all-missing] [--max N] [--dry-run]

meta: OpenAlex single-work lookup by DOI (free) -> abstract, citation count, OA link, arXiv id;
      arXiv API (batch) for arXiv-only papers; Springer Meta API for Springer DOIs lacking abstracts.
      Papers still without an abstract but with a local PDF fall back to abstract-pdf (offline).
      --title-search: for papers with neither DOI nor arXiv id, one OpenAlex search each ($0.001).
pdf:  open-access only: arXiv -> OpenAlex/Unpaywall OA PDF link. Saved to library/pdfs (managed).
      Paywalled PDFs: download them yourself, then `library.py attach-pdf <id> <path>`.
      A freshly downloaded PDF also fills a missing abstract (abstract-pdf).
abstract-pdf: extract the abstract from the paper's local PDF (PyMuPDF, no network, no API quota).
      Only papers without an abstract are touched; abstract_source = 'pdf'. --dry-run prints without saving.
--group accepts '大组' (includes its sub-groups) or '大组/小组'.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import library  # noqa: E402
import netlib  # noqa: E402
import pdfabstract  # noqa: E402
from fresh import inverted_to_text, clean  # noqa: E402

NO_ABSTRACT = "(p.abstract IS NULL OR p.abstract='')"
HAS_PDF = "(p.pdf_path IS NOT NULL AND p.pdf_path!='')"
NEED = {"meta": NO_ABSTRACT, "pdf": f"NOT {HAS_PDF}", "abstract-pdf": f"{NO_ABSTRACT} AND {HAS_PDF}"}


def targets(con, a, need: str) -> list:
    if a.ids:
        rows = [con.execute("SELECT * FROM papers WHERE id=?", (i,)).fetchone() for i in library.ids_arg(a.ids)]
        return [r for r in rows if r]
    q = "SELECT p.* FROM papers p"
    args = []
    if a.group:  # big-group membership already covers its sub-groups (adding to a sub-group adds the big group)
        q += " JOIN paper_groups pg ON pg.paper_id=p.id WHERE pg.group_id=?"
        args.append(library.group_id(con, a.group))
    else:
        q += " WHERE 1"
    q += f" AND {NEED[need]} AND p.status != 'excluded' ORDER BY IFNULL(p.tier,99), p.id LIMIT ?"
    return con.execute(q, args + [a.max]).fetchall()


def log(con, pid: int, msg: str):
    old = con.execute("SELECT enrich_log FROM papers WHERE id=?", (pid,)).fetchone()[0] or ""
    con.execute("UPDATE papers SET enrich_log=? WHERE id=?", ((old + "\n" if old else "") + msg, pid))


def set_fields(con, pid: int, **kw):
    kw = {k: v for k, v in kw.items() if v not in (None, "")}
    if kw:
        kw["enriched_at"] = library.now()
        con.execute(f"UPDATE papers SET {', '.join(f'{k}=COALESCE({k}, ?)' if k not in ('citations', 'enriched_at') else f'{k}=?' for k in kw)} WHERE id=?",
                    [*kw.values(), pid])


def openalex_work(doi: str | None = None, title: str | None = None) -> dict | None:
    sel = "id,doi,ids,cited_by_count,open_access,best_oa_location,abstract_inverted_index,title"
    key = netlib.env("OPENALEX_API_KEY")
    if doi:
        try:
            return netlib.call("openalex", f"https://api.openalex.org/works/doi:{doi}", {"select": sel, "api_key": key})
        except RuntimeError as e:
            if "HTTP 404" in str(e):
                return None
            raise
    d = netlib.call("openalex", "https://api.openalex.org/works", {"search": title, "per_page": 3, "select": sel, "api_key": key})
    for w in d.get("results", []):
        if library.norm_title(w.get("title")) == library.norm_title(title):
            return w
    return None


def arxiv_batch(ids: list[str]) -> dict[str, dict]:
    if not ids:
        return {}
    text = netlib.call("arxiv", "https://export.arxiv.org/api/query", {"id_list": ",".join(ids), "max_results": len(ids)})
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = {}
    for e in ET.fromstring(text).findall("a:entry", ns):
        aid = re.sub(r"v\d+$", "", e.findtext("a:id", namespaces=ns).rsplit("/abs/", 1)[-1])
        out[aid] = {"abstract": clean(e.findtext("a:summary", namespaces=ns))}
    return out


def springer_abstract(doi: str) -> str | None:
    d = netlib.call("springer_meta", "https://api.springernature.com/meta/v2/json",
                    {"q": f"doi:{doi}", "api_key": netlib.env("SPRINGER_META_API_KEY")})
    ab = ((d.get("records") or [{}])[0]).get("abstract")
    if isinstance(ab, dict):
        ab = " ".join(str(x) for x in ab.values())
    return clean(str(ab)) if ab else None


def abstract_from_pdf(con, pid: int, dry_run: bool = False, replace: bool = False) -> tuple[str | None, str]:
    """Fill a missing abstract from the paper's local PDF. Returns (abstract or None, info).
    replace=True (used right after a PDF is attached): an abstract that was itself extracted from a PDF
    (abstract_source='pdf', i.e. possibly from a previous, wrong PDF) is re-extracted from the new one.
    Abstracts from OpenAlex/Springer/arXiv/manual edits are never overwritten."""
    p = con.execute("SELECT abstract, abstract_source, pdf_path FROM papers WHERE id=?", (pid,)).fetchone()
    redo = replace and p and p["abstract_source"] == "pdf"
    if not p or ((p["abstract"] or "").strip() and not redo):
        return None, "已有摘要"
    if not p["pdf_path"]:
        return None, "没有本地 PDF"
    ab, info = pdfabstract.extract(p["pdf_path"])
    if not dry_run:
        if ab:
            con.execute("UPDATE papers SET abstract=?, abstract_source='pdf', enriched_at=? "
                        "WHERE id=? AND (abstract IS NULL OR abstract='' OR abstract_source='pdf')", (ab, library.now(), pid))
        elif redo:   # new PDF yields nothing: don't keep the old PDF's abstract
            con.execute("UPDATE papers SET abstract=NULL, abstract_source=NULL WHERE id=?", (pid,))
        log(con, pid, f"{library.now()} pdf-abstract {'re-' if redo else ''}{'ok ' + info if ab else 'failed: ' + info}")
    return ab, info


def enrich_abstract_pdf(con, rows, dry_run: bool = False) -> dict:
    stats = {"abstract_added": 0, "failed": 0, "skipped": 0, "failures": []}
    for p in rows:
        ab, info = abstract_from_pdf(con, p["id"], dry_run)
        if ab:
            stats["abstract_added"] += 1
            if dry_run:
                print(f"#{p['id']} {p['title'][:70]}\n  [{info}] {ab}\n", file=sys.stderr)
        elif info in ("已有摘要", "没有本地 PDF"):
            stats["skipped"] += 1
        else:
            stats["failed"] += 1
            stats["failures"].append({"id": p["id"], "title": p["title"][:80], "reason": info})
        con.commit()
    return stats


def enrich_meta(con, rows, title_search: bool) -> dict:
    stats = {"openalex": 0, "arxiv": 0, "springer": 0, "abstract_added": 0, "skipped": 0, "errors": 0}
    arx_only = []
    for p in rows:
        pid = p["id"]
        try:
            w = None
            if p["doi"]:
                w = openalex_work(doi=p["doi"]); stats["openalex"] += 1
            elif not p["arxiv"] and title_search:
                w = openalex_work(title=p["title"]); stats["openalex"] += 1
            if w:
                oa = (w.get("best_oa_location") or {}).get("pdf_url") or (w.get("open_access") or {}).get("oa_url")
                arx = ((w.get("ids") or {}).get("arxiv") or "").rsplit("/", 1)[-1] or None
                m = re.search(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})", oa or "")
                arx = arx or (m.group(1) if m else None)
                ab = inverted_to_text(w.get("abstract_inverted_index"))
                set_fields(con, pid, abstract=ab, abstract_source="openalex" if ab else None,
                           citations=w.get("cited_by_count"), oa_url=oa, arxiv=arx,
                           openalex_id=(w.get("id") or "").rsplit("/", 1)[-1])
                stats["abstract_added"] += bool(ab and not p["abstract"])
                log(con, pid, f"{library.now()} openalex ok{' +abstract' if ab else ' (no abstract)'}")
            cur = con.execute("SELECT abstract, arxiv, doi FROM papers WHERE id=?", (pid,)).fetchone()
            if not cur["abstract"] and (cur["doi"] or "").startswith("10.1007/"):
                ab = springer_abstract(cur["doi"]); stats["springer"] += 1
                if ab:
                    set_fields(con, pid, abstract=ab, abstract_source="springer"); stats["abstract_added"] += 1
                log(con, pid, f"{library.now()} springer {'+abstract' if ab else 'no abstract'}")
            cur = con.execute("SELECT abstract, arxiv FROM papers WHERE id=?", (pid,)).fetchone()
            if not cur["abstract"] and cur["arxiv"]:
                arx_only.append((pid, cur["arxiv"]))
            elif not w and not cur["abstract"]:
                stats["skipped"] += 1
        except (netlib.QuotaExceeded, netlib.SourceDisabled) as e:
            print(f"warning: {e}", file=sys.stderr)
            break
        except Exception as e:  # keep going on single-paper failures
            stats["errors"] += 1
            log(con, pid, f"{library.now()} error {str(e)[:150]}")
        con.commit()
    for i in range(0, len(arx_only), 50):  # arXiv: one call per 50 papers
        chunk = arx_only[i:i + 50]
        try:
            got = arxiv_batch([a for _, a in chunk]); stats["arxiv"] += 1
        except Exception as e:
            print(f"warning: arxiv: {e}", file=sys.stderr)
            break
        for pid, aid in chunk:
            ab = (got.get(aid) or {}).get("abstract")
            if ab:
                set_fields(con, pid, abstract=ab, abstract_source="arxiv"); stats["abstract_added"] += 1
        con.commit()
    # last resort, offline: abstract from the local PDF (Elsevier journals, very new papers, ...)
    stats["pdf"] = 0
    for p in rows:
        if con.execute(f"SELECT 1 FROM papers p WHERE id=? AND {NEED['abstract-pdf']}", (p["id"],)).fetchone():
            ab, _ = abstract_from_pdf(con, p["id"])
            if ab:
                stats["pdf"] += 1; stats["abstract_added"] += 1
            con.commit()
    return stats


def enrich_pdf(con, rows) -> dict:
    stats = {"downloaded": 0, "no_oa_source": 0, "failed": 0}
    for p in rows:
        pid = p["id"]
        cands = []
        if p["arxiv"]:
            cands.append(("arxiv", f"https://arxiv.org/pdf/{p['arxiv']}"))
        if p["oa_url"]:
            cands.append(("unpaywall" if "arxiv.org" not in p["oa_url"] else "arxiv", p["oa_url"]))
        if not cands and p["doi"] and netlib.env("UNPAYWALL_EMAIL"):
            try:
                d = netlib.call("unpaywall", f"https://api.unpaywall.org/v2/{p['doi']}", {"email": netlib.env("UNPAYWALL_EMAIL")})
                url = ((d.get("best_oa_location") or {}).get("url_for_pdf"))
                if url:
                    cands.append(("unpaywall", url))
                    con.execute("UPDATE papers SET oa_url=COALESCE(oa_url, ?) WHERE id=?", (url, pid))
            except Exception as e:
                log(con, pid, f"{library.now()} unpaywall {str(e)[:100]}")
        if not cands:
            stats["no_oa_source"] += 1
            continue
        slug = re.sub(r"[^A-Za-z0-9]+", "_", p["title"])[:60].strip("_")
        dest = library.PDF_DIR / f"{pid}_{slug}.pdf"
        ok = False
        for src, url in cands:
            try:
                netlib.download(src, url, dest)
                con.execute("UPDATE papers SET pdf_path=?, pdf_kind='managed', updated_at=? WHERE id=?",
                            (str(dest), library.now(), pid))
                log(con, pid, f"{library.now()} pdf ok via {src}")
                ok = True
                if abstract_from_pdf(con, pid)[0]:
                    stats["abstract_from_pdf"] = stats.get("abstract_from_pdf", 0) + 1
                break
            except (netlib.QuotaExceeded, netlib.SourceDisabled) as e:
                print(f"warning: {e}", file=sys.stderr)
            except Exception as e:
                log(con, pid, f"{library.now()} pdf {src} failed: {str(e)[:120]}")
        stats["downloaded" if ok else "failed"] += 1
        con.commit()
    return stats


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=["meta", "pdf", "abstract-pdf"])
    ap.add_argument("--ids"); ap.add_argument("--group"); ap.add_argument("--all-missing", action="store_true")
    ap.add_argument("--max", type=int, default=200)
    ap.add_argument("--title-search", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="abstract-pdf: print the extracted abstracts, save nothing")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    if not (a.ids or a.group or a.all_missing):
        ap.error("choose --ids, --group or --all-missing")
    con = library.connect()
    rows = targets(con, a, a.what)
    if a.what == "meta":
        stats = enrich_meta(con, rows, a.title_search)
    elif a.what == "pdf":
        stats = enrich_pdf(con, rows)
    else:
        stats = enrich_abstract_pdf(con, rows, a.dry_run)
    con.commit()
    print(json.dumps({"papers": len(rows), **stats}, ensure_ascii=False))


if __name__ == "__main__":
    main()
