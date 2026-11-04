"""Local web UI for the paper library.  Start:  python -I ps/server.py [--port 8765]
Then open http://127.0.0.1:8765  (listens on 127.0.0.1 only).

Safety: binds to localhost; rejects foreign Host headers (DNS rebinding); every write needs the
X-PS header (blocks cross-site form posts); files are served only for paths registered in the library.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import re
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dblp_query  # noqa: E402
import enrich  # noqa: E402
import library  # noqa: E402
import scratch  # noqa: E402

ROOT = HERE.parent
WEB = ROOT / "web"
JOBS: dict[int, dict] = {}
_job_seq = 0
_job_lock = threading.Lock()


def run_job(kind: str, args: list[str]) -> int:
    """Run a workstation script in the background (enrichment / PDF download go through netlib limits)."""
    global _job_seq
    with _job_lock:
        _job_seq += 1
        jid = _job_seq
    JOBS[jid] = {"id": jid, "kind": kind, "status": "running", "started": time.time(), "output": ""}

    def work():
        p = subprocess.run([sys.executable, "-I", *args], capture_output=True, text=True, encoding="utf-8", cwd=ROOT)
        JOBS[jid].update(status="done" if p.returncode == 0 else "failed",
                         output=(p.stdout or "")[-2000:] + (p.stderr or "")[-1000:], finished=time.time())
    threading.Thread(target=work, daemon=True).start()
    return jid


class Handler(BaseHTTPRequestHandler):
    server_version = "PaperLibrary/0.1"

    # ------------------------------------------------------------ plumbing
    def log_message(self, fmt, *args):
        if "/api/" in (args[0] if args else ""):
            sys.stderr.write("%s\n" % (fmt % args))

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")

    def _send(self, code: int, body, ctype="application/json; charset=utf-8", extra: dict | None = None):
        data = body if isinstance(body, bytes) else (
            body.encode("utf-8") if isinstance(body, str) else json.dumps(body, ensure_ascii=False).encode("utf-8"))
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _json_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def _file(self, path: Path, ctype: str | None = None):
        if not path.exists() or not path.is_file():
            return self._send(404, {"error": "file not found", "path": str(path)})
        ctype = ctype or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        data = path.read_bytes()
        self._send(200, data, ctype, {"Content-Disposition": "inline", "Cache-Control": "no-cache"})

    def _dispatch(self, method: str):
        if not self._host_ok():
            return self._send(403, {"error": "bad host"})
        if method != "GET" and self.headers.get("X-PS") != "1":
            return self._send(403, {"error": "missing X-PS header"})
        u = urllib.parse.urlparse(self.path)
        qs = {k: v[-1] for k, v in urllib.parse.parse_qs(u.query).items()}
        try:
            for pat, fn in ROUTES.get(method, []):
                m = re.fullmatch(pat, u.path)
                if m:
                    return fn(self, qs, *m.groups())
            if method == "GET":
                return self._static(u.path)
            self._send(404, {"error": "no route"})
        except SystemExit as e:
            self._send(400, {"error": str(e)})
        except Exception as e:
            traceback.print_exc()
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _static(self, path: str):
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        f = (WEB / rel).resolve()
        if WEB.resolve() not in f.parents and f != WEB.resolve():
            return self._send(403, {"error": "outside web root"})
        self._file(f)

    # ------------------------------------------------------------ papers
    def papers(self, qs):
        con = library.connect()
        # group = group id (a big group includes its sub-groups); unassigned=1 -> big-group papers in no sub-group
        rows = library.list_papers(con, group=qs.get("group") or None, status=qs.get("status"), starred=qs.get("starred") == "1",
                                   min_level=qs.get("level"), q=qs.get("q"), tier=qs.get("tier"),
                                   limit=int(qs.get("limit", 1000)), offset=int(qs.get("offset", 0)),
                                   sort=qs.get("sort", "priority"), unassigned=qs.get("unassigned") == "1")
        if qs.get("search"):
            ids = {r[0] for r in con.execute("SELECT paper_id FROM search_hits WHERE search_id=?", (int(qs["search"]),))}
            rows = [r for r in rows if r["id"] in ids]
        if qs.get("exclude_hidden", "1") == "1" and not qs.get("status"):
            rows = [r for r in rows if r["status"] != "excluded"]
        for r in rows:
            r.pop("enrich_log", None)
            r["abstract"] = (r["abstract"] or "")[:300]
        self._send(200, rows)

    def paper(self, qs, pid):
        self._send(200, library.show(library.connect(), int(pid)))

    def paper_patch(self, qs, pid):
        b = self._json_body()
        con = library.connect()
        allowed = {"status", "starred", "title", "authors", "venue", "venue_short", "year", "doi", "arxiv", "abstract", "url"}
        upd = {k: v for k, v in b.items() if k in allowed}
        if "status" in upd and upd["status"] not in library.STATUSES:
            raise SystemExit("bad status")
        if upd:
            if "abstract" in upd:   # hand-edited: never treat it as PDF-derived (unlinking a PDF won't clear it)
                upd["abstract_source"] = "manual" if (upd["abstract"] or "").strip() else None
            upd["updated_at"] = library.now()
            con.execute(f"UPDATE papers SET {', '.join(f'{k}=?' for k in upd)} WHERE id=?", [*upd.values(), int(pid)])
            con.commit()
        self._send(200, library.show(con, int(pid)))

    def paper_delete(self, qs, pid):
        con = library.connect()
        con.execute("DELETE FROM papers WHERE id=?", (int(pid),)); con.commit()
        self._send(200, {"deleted": int(pid)})

    def bulk(self, qs):
        b = self._json_body()
        ids = [int(i) for i in b.get("ids") or []]
        act, val = b.get("action"), b.get("value")
        con = library.connect()
        res = {"ids": ids, "action": act}
        if act == "status":
            if val not in library.STATUSES:
                raise SystemExit("bad status")
            con.executemany("UPDATE papers SET status=?, updated_at=? WHERE id=?", [(val, library.now(), i) for i in ids])
        elif act in ("star", "unstar"):
            con.executemany("UPDATE papers SET starred=? WHERE id=?", [(1 if act == "star" else 0, i) for i in ids])
        elif act == "group_add":          # value: group id or path; sub-group also adds the big group
            res["group"] = library.group_path(con, library.add_to_group(con, ids, val))
        elif act == "group_remove":       # big group: also leaves its sub-groups; sub-group: stays in big group
            res["group"] = library.group_path(con, library.remove_from_group(con, ids, val))
        elif act == "group_move":         # into exactly this sub-group (leaves sibling sub-groups)
            res["group"] = library.group_path(con, library.move_to_subgroup(con, ids, val))
        elif act == "delete":
            con.executemany("DELETE FROM papers WHERE id=?", [(i,) for i in ids])
        elif act == "enrich_meta":
            res["job"] = run_job("补全摘要", [str(HERE / "enrich.py"), "meta", "--ids", ",".join(map(str, ids))])
        elif act == "abstract_pdf":       # local PDFs -> abstract (offline, no API quota)
            res["job"] = run_job("从 PDF 提取摘要", [str(HERE / "enrich.py"), "abstract-pdf", "--ids", ",".join(map(str, ids))])
        elif act == "fetch_pdf":
            res["job"] = run_job("下载 PDF", [str(HERE / "enrich.py"), "pdf", "--ids", ",".join(map(str, ids))])
        elif act == "queue_read":
            con.executemany("INSERT INTO queue (paper_id, task, created_at) VALUES (?,?,?)",
                            [(i, "read", library.now()) for i in ids])
        elif act == "bib":
            con.commit()
            return self._send(200, {"bib": library.export_bib(con, ids)})
        else:
            raise SystemExit(f"unknown action {act}")
        con.commit()
        self._send(200, res)

    # ------------------------------------------------------------ notes / pdf / ai notes
    def note_get(self, qs, pid):
        self._send(200, {"text": library.read_note(int(pid))})

    def note_put(self, qs, pid):
        library.write_note(int(pid), self._json_body().get("text", ""))
        self._send(200, {"saved": True})

    def pdf_link(self, qs, pid):
        b = self._json_body()
        con = library.connect()
        path = library.attach_pdf(con, int(pid), b["path"].strip().strip('"'), copy=bool(b.get("copy")))
        con.commit()
        self._send(200, {"pdf": path, **self._pdf_abstract(con, int(pid))})

    @staticmethod
    def _pdf_abstract(con, pid: int) -> dict:
        """After a PDF is attached: fill a missing abstract from it (local, fast, no API)."""
        try:
            ab, info = enrich.abstract_from_pdf(con, pid, replace=True)   # also replaces an abstract from a previous PDF
            con.commit()
        except Exception as e:  # never fail the attach because of the extraction
            ab, info = None, f"{type(e).__name__}: {e}"
        return {"abstract_from_pdf": bool(ab), "abstract_info": info}

    def pdf_upload(self, qs, pid):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > 200e6:
            raise SystemExit("empty or too large upload")
        data = self.rfile.read(n)
        if not data.startswith(b"%PDF-"):
            raise SystemExit("not a PDF")
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", urllib.parse.unquote(qs.get("name", "upload.pdf")))[:80]
        dst = library.PDF_DIR / f"{int(pid)}_{name if name.lower().endswith('.pdf') else name + '.pdf'}"
        dst.write_bytes(data)
        con = library.connect()
        con.execute("UPDATE papers SET pdf_path=?, pdf_kind='managed', updated_at=? WHERE id=?", (str(dst), library.now(), int(pid)))
        con.commit()
        self._send(200, {"pdf": str(dst), **self._pdf_abstract(con, int(pid))})

    def pdf_unlink(self, qs, pid):
        con = library.connect()
        out = library.detach_pdf(con, int(pid), delete_copy=qs.get("delete_copy") == "1")
        con.commit()
        self._send(200, out)

    def pdf_serve(self, qs, pid):
        r = library.connect().execute("SELECT pdf_path FROM papers WHERE id=?", (int(pid),)).fetchone()
        if not r or not r[0]:
            return self._send(404, {"error": "no PDF"})
        self._file(Path(r[0]), "application/pdf")

    def ainote(self, qs, nid):
        r = library.connect().execute("SELECT path FROM ai_notes WHERE id=?", (int(nid),)).fetchone()
        if not r:
            return self._send(404, {"error": "no note"})
        p = Path(r[0])
        self._send(200, {"path": str(p), "text": p.read_text(encoding="utf-8", errors="replace") if p.exists() else "(文件不存在)"})

    def ainote_link(self, qs, pid):
        con = library.connect()
        path = library.link_note(con, int(pid), self._json_body()["path"].strip().strip('"'))
        con.commit()
        self._send(200, {"note": path})

    def ainote_unlink(self, qs, nid):
        con = library.connect()
        con.execute("DELETE FROM ai_notes WHERE id=?", (int(nid),)); con.commit()
        self._send(200, {"unlinked": True})

    def ai_asset(self, qs):
        """Serve a file referenced by an AI note (e.g. a local image), only if it sits next to a registered note."""
        nid, rel = int(qs["note"]), qs["path"]
        r = library.connect().execute("SELECT path FROM ai_notes WHERE id=?", (nid,)).fetchone()
        if not r:
            return self._send(404, {"error": "no note"})
        base = Path(r[0]).parent.resolve()
        f = (base / urllib.parse.unquote(rel)).resolve()
        if base not in f.parents:
            return self._send(403, {"error": "outside note folder"})
        self._file(f)

    def scan_notes(self, qs):
        con = library.connect()
        out = library.scan_notes(con); con.commit()
        self._send(200, {"linked": out})

    # ------------------------------------------------------------ relations / graph
    def rel_add(self, qs, pid):
        b = self._json_body()
        con = library.connect()
        con.execute("INSERT OR REPLACE INTO relations (src_id, dst_id, rel_type, note, created_at) VALUES (?,?,?,?,?)",
                    (int(pid), int(b["dst_id"]), b["rel_type"].strip(), b.get("note"), library.now()))
        con.commit()
        self._send(200, library.show(con, int(pid)))

    def rel_delete(self, qs, rid):
        con = library.connect()
        con.execute("DELETE FROM relations WHERE id=?", (int(rid),)); con.commit()
        self._send(200, {"deleted": int(rid)})

    def graph(self, qs):
        con = library.connect()
        if qs.get("group"):
            ids = {r[0] for r in con.execute("SELECT paper_id FROM paper_groups WHERE group_id=?",
                                             (library.group_id(con, qs["group"]),))}
        else:
            ids = {r[0] for r in con.execute("SELECT src_id FROM relations UNION SELECT dst_id FROM relations")}
        edges = [dict(r) for r in con.execute("SELECT id, src_id, dst_id, rel_type, note FROM relations")
                 if r["src_id"] in ids or r["dst_id"] in ids]
        ids |= {e["src_id"] for e in edges} | {e["dst_id"] for e in edges}
        nodes = [dict(r) for r in con.execute(
            f"SELECT id, title, venue_short, year, tier, status, starred FROM papers WHERE id IN ({','.join('?' * len(ids))})",
            list(ids))] if ids else []
        self._send(200, {"nodes": nodes, "edges": edges})

    # ------------------------------------------------------------ groups
    def groups(self, qs):
        """Tree: big groups (with `children` sub-groups, `n`, `unassigned`)."""
        self._send(200, library.group_tree(library.connect()))

    def group_create(self, qs):
        b = self._json_body()
        con = library.connect()
        name = library.check_name(b.get("name", ""))
        path = f"{library.group_path(con, int(b['parent_id']))}/{name}" if b.get("parent_id") else name
        try:
            con.execute("SAVEPOINT g")
            exists = True
            try:
                library.group_id(con, path)
            except SystemExit:
                exists = False
            if exists:
                raise SystemExit(f"分组已存在：{path}")
            gid = library.group_id(con, path, create=True)
            if b.get("description"):
                con.execute("UPDATE groups SET description=? WHERE id=?", (b["description"], gid))
            con.execute("RELEASE g")
        finally:
            con.commit()
        self._send(200, {"id": gid, "path": library.group_path(con, gid)})

    def group_patch(self, qs, gid):
        b = self._json_body()
        con = library.connect()
        gid = int(gid)
        if "parent_id" in b:   # re-parent: null -> becomes a big group; id -> becomes a sub-group of that big group
            library.reparent_group(con, gid, b["parent_id"] if b["parent_id"] else None)
        upd = {k: v for k, v in b.items() if k in ("name", "description", "color")}
        if "name" in upd:
            upd["name"] = library.check_name(upd["name"])
        if upd:
            try:
                con.execute(f"UPDATE groups SET {', '.join(f'{k}=?' for k in upd)} WHERE id=?", [*upd.values(), gid])
            except sqlite3.IntegrityError:
                raise SystemExit("同一层级下已有同名分组")
        con.commit()
        self._send(200, {"updated": gid, "path": library.group_path(con, gid)})

    def group_delete(self, qs, gid):
        con = library.connect()
        subs = con.execute("SELECT COUNT(*) FROM groups WHERE parent_id=?", (int(gid),)).fetchone()[0]
        con.execute("DELETE FROM groups WHERE id=?", (int(gid),)); con.commit()
        self._send(200, {"deleted": int(gid), "sub_groups_deleted": subs})

    # ------------------------------------------------------------ dblp catalog
    def dblp_search(self, qs):
        if not qs.get("q", "").strip():
            return self._send(200, {"total_matches": 0, "results": []})
        ns = argparse.Namespace(query=qs["q"], venue=None, tier=[qs["tier"]] if qs.get("tier") else None,
                                year=qs.get("year") or None, type=None, no_preprints=qs.get("no_preprints") == "1",
                                sort=qs.get("sort", "priority"), limit=int(qs.get("limit", 50)), pool=300, raw=False,
                                json=True, keep_duplicates=False)
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            dblp_query.cmd_search(ns)
        data = json.loads(buf.getvalue())
        con = library.connect()
        for r in data["results"]:
            hit = con.execute("SELECT id FROM papers WHERE dblp_key=?", (r["key"],)).fetchone() or (
                con.execute("SELECT id FROM papers WHERE doi=?", (r["doi"],)).fetchone() if r.get("doi") else None)
            r["in_library"] = hit[0] if hit else None
        self._send(200, data)

    def dblp_add(self, qs):
        b = self._json_body()
        con = library.connect()
        ids = []
        for key in b.get("keys") or []:
            d = library.lookup_dblp(key)
            if d:
                ids.append(library.upsert(con, library.rec_from_dblp(d))[0])
        if b.get("group") and ids:
            library.add_to_group(con, ids, b["group"])
        con.commit()
        self._send(200, {"paper_ids": ids})

    # ------------------------------------------------------------ misc
    def searches(self, qs):
        con = library.connect()
        self._send(200, [dict(r) for r in con.execute(
            "SELECT id, query, kind, run_at, n_candidates, n_kept, report_path FROM searches ORDER BY id DESC LIMIT 200")])

    def report(self, qs, sid):
        r = library.connect().execute("SELECT report_path FROM searches WHERE id=?", (int(sid),)).fetchone()
        if not r or not r[0]:
            return self._send(404, {"error": "no report"})
        p = Path(r[0])
        self._send(200, {"path": str(p), "text": p.read_text(encoding="utf-8") if p.exists() else "(报告文件不存在)"})

    def queue(self, qs):
        con = library.connect()
        self._send(200, [dict(r) for r in con.execute("""SELECT q.*, p.title FROM queue q JOIN papers p ON p.id=q.paper_id
            ORDER BY q.status='pending' DESC, q.id DESC LIMIT 300""")])

    def queue_delete(self, qs, qid):
        con = library.connect()
        con.execute("DELETE FROM queue WHERE id=?", (int(qid),)); con.commit()
        self._send(200, {"deleted": int(qid)})

    def guide(self, qs):
        f = ROOT / "docs" / "操作指南.md"
        self._send(200, {"path": str(f), "text": f.read_text(encoding="utf-8") if f.exists() else "（指南文件不存在）"})

    def jobs(self, qs):
        self._send(200, sorted(JOBS.values(), key=lambda j: -j["id"])[:30])

    def stats(self, qs):
        con = library.connect()
        papers = [library.paper_dict(con, r) for r in con.execute("SELECT * FROM papers")]
        self._send(200, {
            "papers": len(papers), "starred": sum(p["starred"] for p in papers),
            "by_level": {f"L{i}": sum(p["level"] == i for p in papers) for i in range(4)},
            "by_status": {s: sum(p["status"] == s for p in papers) for s in library.STATUSES},
            "pending_queue": con.execute("SELECT COUNT(*) FROM queue WHERE status='pending'").fetchone()[0]})


ROUTES = {
    "GET": [(r"/api/papers", Handler.papers), (r"/api/papers/(\d+)", Handler.paper),
            (r"/api/papers/(\d+)/note", Handler.note_get), (r"/pdf/(\d+)", Handler.pdf_serve),
            (r"/api/ainote/(\d+)", Handler.ainote), (r"/api/ai-asset", Handler.ai_asset),
            (r"/api/groups", Handler.groups), (r"/api/graph", Handler.graph), (r"/api/dblp/search", Handler.dblp_search),
            (r"/api/searches", Handler.searches), (r"/api/searches/(\d+)/report", Handler.report),
            (r"/api/queue", Handler.queue), (r"/api/jobs", Handler.jobs), (r"/api/stats", Handler.stats),
            (r"/api/guide", Handler.guide)],
    "POST": [(r"/api/papers/bulk", Handler.bulk), (r"/api/papers/(\d+)/pdf", Handler.pdf_link),
             (r"/api/papers/(\d+)/pdf/upload", Handler.pdf_upload), (r"/api/papers/(\d+)/ainote", Handler.ainote_link),
             (r"/api/papers/(\d+)/relations", Handler.rel_add), (r"/api/groups", Handler.group_create),
             (r"/api/dblp/add", Handler.dblp_add), (r"/api/scan-notes", Handler.scan_notes)],
    "PATCH": [(r"/api/papers/(\d+)", Handler.paper_patch), (r"/api/groups/(\d+)", Handler.group_patch)],
    "PUT": [(r"/api/papers/(\d+)/note", Handler.note_put)],
    "DELETE": [(r"/api/papers/(\d+)", Handler.paper_delete), (r"/api/papers/(\d+)/pdf", Handler.pdf_unlink),
               (r"/api/ainote/(\d+)", Handler.ainote_unlink), (r"/api/relations/(\d+)", Handler.rel_delete),
               (r"/api/groups/(\d+)", Handler.group_delete), (r"/api/queue/(\d+)", Handler.queue_delete)],
}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    library.connect().close()
    try:  # leftovers of interrupted sessions (scratch older than 24 h, stray partial downloads, expired runs)
        scratch.clean(older_than_hours=24)
    except Exception as e:
        print(f"warning: scratch clean-up skipped: {e}", file=sys.stderr)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"论文库界面已启动： http://127.0.0.1:{a.port}   （Ctrl+C 停止）", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
