"""Prepare a local PDF for AI close reading (used by /queue when a paper has no arXiv full text).

  python -I ps/pdfread.py text <pid|file.pdf> --task queue-<qid>
      -> data/tmp/queue-<qid>/text.txt (full text, page-marked) and pages/pNN.png (page renders, 110 dpi)
         Scratch only: removed automatically by `library.py queue done|fail <qid>`.
  python -I ps/pdfread.py figures <pid|file.pdf> --out <note folder>/assets --prefix <MethodName>
         [--names fig1_overview,fig2_x,...]
      -> one PNG per embedded figure, rendered from the page at 200 dpi on a white background
         (keeps the figure exactly as printed; transparent images do not turn black). These are kept.

Run with `python -I` (the PDF is downloaded data).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import scratch  # noqa: E402


def _open(path: str):
    try:
        import pymupdf
    except ImportError:
        import fitz as pymupdf
    return pymupdf, pymupdf.open(path)


def resolve_pdf(ref: str) -> str:
    if ref.isdigit():
        import library
        r = library.connect().execute("SELECT pdf_path FROM papers WHERE id=?", (int(ref),)).fetchone()
        if not r or not r[0]:
            raise SystemExit(f"paper {ref} has no linked PDF")
        return r[0]
    return ref


def dump_text(pdf: str, task: str, render: bool = True) -> dict:
    pm, doc = _open(pdf)
    out = scratch.task_dir(task)
    with open(out / "text.txt", "w", encoding="utf-8") as f:
        for i, page in enumerate(doc):
            f.write(f"\n\n===== PAGE {i + 1} =====\n{page.get_text('text')}")
    if render:
        (out / "pages").mkdir(exist_ok=True)
        for i, page in enumerate(doc):
            page.get_pixmap(dpi=110).save(str(out / "pages" / f"p{i + 1:02d}.png"))
    figs = [(i + 1, [round(x) for x in info["bbox"]]) for i, page in enumerate(doc)
            for info in page.get_image_info() if _is_figure(info["bbox"])]
    return {"dir": str(out), "pages": len(doc), "text": str(out / "text.txt"), "figures_found": len(figs),
            "figures": [{"page": p, "bbox": b} for p, b in figs]}


def _is_figure(bbox) -> bool:
    return (bbox[2] - bbox[0]) >= 60 and (bbox[3] - bbox[1]) >= 40


def crop_figures(pdf: str, out_dir: str, prefix: str, names: list[str] | None) -> list[str]:
    pm, doc = _open(pdf)
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    saved, k = [], 0
    for i, page in enumerate(doc):
        for info in page.get_image_info():
            if not _is_figure(info["bbox"]):
                continue
            k += 1
            name = names[k - 1] if names and k <= len(names) else f"fig{k}_p{i + 1}"
            fn = out / f"{prefix}_{name}.png"
            page.get_pixmap(dpi=200, clip=pm.Rect(info["bbox"]), alpha=False).save(str(fn))
            saved.append(str(fn))
    return saved


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("text"); s.add_argument("pdf"); s.add_argument("--task", required=True)
    s.add_argument("--no-render", action="store_true")
    s = sub.add_parser("figures"); s.add_argument("pdf"); s.add_argument("--out", required=True)
    s.add_argument("--prefix", required=True); s.add_argument("--names")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    pdf = resolve_pdf(a.pdf)
    if a.cmd == "text":
        res = dump_text(pdf, a.task, not a.no_render)
    else:
        res = {"saved": crop_figures(pdf, a.out, a.prefix, a.names.split(",") if a.names else None)}
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
