"""Verify a reference list: does each cited paper really exist, and are its details right?

Usage:
  python -I ps/verify.py refs.bib            # BibTeX file
  python -I ps/verify.py refs.txt            # plain text, one reference per line (numbering is ignored)
  python -I ps/verify.py refs.bib --out exports/verified.bib --json

For each reference: DOI lookup in local dblp -> title match in local dblp (offline) -> Crossref
bibliographic query (1 call, polite pool). Verdicts:
  verified   found, title matches (>= 0.93 similarity)
  check      found a close match, but title / year / first author differ — look at the diff
  not_found  nothing similar in dblp or Crossref — possibly fabricated or very obscure
The --out file contains corrected BibTeX (dblp entries where available, else Crossref metadata).
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dblp_query  # noqa: E402
import netlib  # noqa: E402


def norm(t: str | None) -> str:
    return re.sub(r"[^\w]+", " ", (t or "").lower()).strip()


def sim(a: str | None, b: str | None) -> float:
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


def parse_bib(text: str) -> list[dict]:
    out = []
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", text):
        start, depth, i = m.end(), 1, m.end()
        while i < len(text) and depth:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        body = text[start:i - 1]
        fields = {}
        for fm in re.finditer(r"(\w+)\s*=\s*", body):
            j = fm.end()
            if j < len(body) and body[j] == "{":
                d, k = 1, j + 1
                while k < len(body) and d:
                    d += {"{": 1, "}": -1}.get(body[k], 0)
                    k += 1
                val = body[j + 1:k - 1]
            elif j < len(body) and body[j] == '"':
                k = body.find('"', j + 1)
                val = body[j + 1:k]
            else:
                val = re.match(r"[^,\n]*", body[j:]).group(0)
            fields.setdefault(fm.group(1).lower(), re.sub(r"[{}]", "", re.sub(r"\s+", " ", val)).strip())
        out.append({"key": m.group(2), "type": m.group(1).lower(), **fields})
    return out


def parse_text(text: str) -> list[dict]:
    refs = []
    for line in text.splitlines():
        line = re.sub(r"^\s*(\[\d+\]|\d+[.)])\s*", "", line).strip()
        if len(line) < 15:
            continue
        doi = re.search(r"10\.\d{4,9}/[^\s,;]+", line)
        # heuristic title: the longest segment between periods / quotes
        quoted = re.search(r"[\"“]([^\"”]{15,})[\"”]", line)
        segs = sorted(re.split(r"\.\s+", line), key=len, reverse=True)
        title = quoted.group(1) if quoted else (segs[0] if segs else line)
        year = re.search(r"\b(19|20)\d{2}\b", line)
        refs.append({"key": line[:40], "raw": line, "title": title.strip(" ."), "doi": doi.group(0).rstrip(".") if doi else None,
                     "year": year.group(0) if year else None})
    return refs


def first_author_last(authors: str | None) -> str:
    if not authors:
        return ""
    a = re.split(r"\s+and\s+|;", authors)[0].strip()
    if "," in a:
        return norm(a.split(",")[0])
    return norm(a.split()[-1]) if a.split() else ""


def check_one(con, ref: dict) -> dict:
    title, doi = ref.get("title"), (ref.get("doi") or "").lower() or None
    res = {"ref": ref.get("key"), "claimed_title": title, "verdict": "not_found", "source": None, "match": None, "issues": []}
    cand = None
    if doi:
        r = con.execute(f"SELECT {dblp_query.FIELDS} FROM pubs p WHERE p.doi=?", (doi,)).fetchone()
        if r:
            cand = ("dblp", dict(zip(dblp_query.COLS, r)))
    if cand is None and title:
        toks = re.findall(r"[^\W_]+", title)[:10]
        if toks:
            best, best_s = None, 0
            for r in con.execute(f"SELECT {dblp_query.FIELDS} FROM pubs_fts JOIN pubs p ON p.rowid=pubs_fts.rowid "
                                 f"WHERE pubs_fts MATCH ? ORDER BY bm25(pubs_fts) LIMIT 15",
                                 ("title : (" + " ".join(f'"{t}"' for t in toks) + ")",)):
                d = dict(zip(dblp_query.COLS, r))
                s = sim(title, d["title"]) + (0.01 if d["venue_key"] != "journals/corr" else 0)
                if s > best_s:
                    best, best_s = d, s
            if best and best_s >= 0.8:
                cand = ("dblp", best)
    if cand is None and (title or doi):
        try:
            params = {"rows": 3, "mailto": netlib.env("CROSSREF_MAILTO"),
                      "select": "DOI,title,author,container-title,published,type"}
            if doi:
                items = [netlib.call("crossref", f"https://api.crossref.org/works/{doi}", {"mailto": params["mailto"]})["message"]]
            else:
                params["query.bibliographic"] = title
                items = netlib.call("crossref", "https://api.crossref.org/works", params)["message"]["items"]
            best = max(items, key=lambda it: sim(title or "", (it.get("title") or [""])[0]), default=None)
            if best and (doi or sim(title, (best.get("title") or [""])[0]) >= 0.8):
                p = ((best.get("published") or {}).get("date-parts") or [[None]])[0]
                cand = ("crossref", {"title": (best.get("title") or [""])[0], "doi": (best.get("DOI") or "").lower(),
                                     "year": p[0], "venue": (best.get("container-title") or [""])[0],
                                     "authors": "; ".join(f"{a.get('given', '')} {a.get('family', '')}".strip() for a in best.get("author") or [])})
        except RuntimeError as e:
            if "HTTP 404" not in str(e):
                res["issues"].append(f"crossref error: {str(e)[:80]}")
        except (netlib.QuotaExceeded, netlib.SourceDisabled) as e:
            res["issues"].append(str(e))
    if cand is None:
        return res
    src, m = cand
    s = sim(title, m["title"]) if title else 1.0
    res.update(source=src, match={k: m.get(k) for k in ("key", "title", "authors", "year", "venue", "doi")})
    if s < 0.93:
        res["issues"].append(f"标题相似度 {s:.2f}")
    if ref.get("year") and m.get("year") and str(ref["year"])[:4] != str(m["year"]):
        res["issues"].append(f"年份 {ref['year']} ≠ {m['year']}")
    fa_claim, fa_real = first_author_last(ref.get("author")), first_author_last(m.get("authors"))
    if fa_claim and fa_real and fa_claim != fa_real and fa_claim not in norm(m.get("authors")):
        res["issues"].append(f"第一作者 {fa_claim} ≠ {fa_real}")
    if doi and m.get("doi") and doi != m["doi"]:
        res["issues"].append(f"DOI {doi} ≠ {m['doi']}")
    res["verdict"] = "verified" if not res["issues"] else "check"
    return res


def corrected_bib(con, results: list[dict]) -> str:
    out = []
    for r in results:
        m = r.get("match") or {}
        if r["source"] == "dblp" and m.get("key"):
            row = con.execute(f"SELECT {dblp_query.FIELDS} FROM pubs p WHERE p.key=?", (m["key"],)).fetchone()
            out.append(dblp_query.bibtex(con, row))
        elif r["source"] == "crossref":
            names = " and ".join(a.strip() for a in (m.get("authors") or "").split(";") if a.strip())
            out.append(f"@article{{{r['ref']},\n  author = {{{names}}},\n  title = {{{m['title']}}},\n"
                       f"  journal = {{{m.get('venue') or ''}}},\n  year = {{{m.get('year') or ''}}},\n  doi = {{{m.get('doi')}}}\n}}")
        else:
            out.append(f"% NOT FOUND: {r['ref']} — {r['claimed_title']}")
    return "\n\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("--out", help="write corrected BibTeX here")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    text = Path(a.file).read_text(encoding="utf-8", errors="replace")
    refs = parse_bib(text) if "@" in text and re.search(r"@\w+\s*\{", text) else parse_text(text)
    con = dblp_query.connect()
    results = [check_one(con, r) for r in refs]
    if a.out:
        Path(a.out).write_text(corrected_bib(con, results) + "\n", encoding="utf-8")
    summary = {k: sum(r["verdict"] == k for r in results) for k in ("verified", "check", "not_found")}
    if a.json:
        print(json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=1))
        return
    icon = {"verified": "✅", "check": "⚠️", "not_found": "❌"}
    for r in results:
        m = r.get("match") or {}
        print(f"{icon[r['verdict']]} {r['ref']}: {r['claimed_title']}")
        if m:
            print(f"     → [{r['source']}] {m.get('title')} | {m.get('venue')} {m.get('year')}" + (f" | doi:{m['doi']}" if m.get("doi") else ""))
        for i in r["issues"]:
            print(f"     ! {i}")
    print(json.dumps(summary, ensure_ascii=False) + (f"  corrected BibTeX -> {a.out}" if a.out else ""))


if __name__ == "__main__":
    main()
