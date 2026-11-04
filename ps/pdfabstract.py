"""Extract the abstract from a paper PDF (local, offline; uses PyMuPDF).

    python -I ps/pdfabstract.py <file.pdf> [...]      print what would be extracted (no library changes)

Works on text blocks of the first two pages in reading order:
  start  = block beginning with an abstract heading ("Abstract", "Abstract—", "Abstract.", "a b s t r a c t", "ABSTRACT")
  stop   = Keywords / Key words / Index Terms / 1. Introduction / I. INTRODUCTION / CCS Concepts / ©-line
  skip   = blocks in a smaller font than the abstract (IEEE first-page footnotes: funding, affiliations, received dates)
           and page furniture (licence lines, DOI lines, running headers)
The result is sanity-checked (length) so that a failed parse is reported instead of storing junk.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HEAD = re.compile(r"^\s*(?:a\s?b\s?s\s?t\s?r\s?a\s?c\s?t|abstract)\b\s*[.:—–\-]*\s*", re.I)
STOP = re.compile(
    r"(?:^|\n|\s)(?:Key\s*words?\s*[:—–\-.]|Index\s+Terms\s*[—–\-:]|CCS\s+Concepts|"
    r"(?:1\.?|I\.)\s+(?:INTRODUCTION|Introduction)\b|©|\(c\)\s*\d{4})")
STOP_BLOCK = re.compile(r"^\s*(?:Key\s*words?|Index\s+Terms|Introduction\s*$|1\.?\s+Introduction|I\.\s+INTRODUCTION)", re.I)
FURNITURE = re.compile(
    r"^\s*(?:Authorized licensed use|Digital Object Identifier|This article has been accepted|content may change prior|"
    r"for more information|\d{4}-\d{3}[\dX]\s*©|©|Contents lists available|journal homepage|https?://doi\.org)", re.I)
LIGATURES = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st", "­": ""}
MIN_CHARS, MAX_CHARS = 300, 5000


def _open(path: str):
    try:
        import pymupdf
    except ImportError:  # older PyMuPDF
        import fitz as pymupdf
    return pymupdf.open(path)


def _blocks(doc, pages: int = 2) -> list[tuple[str, float]]:
    """(text, dominant font size) for each text block, in the PDF's own reading order."""
    out = []
    for page in list(doc)[:pages]:
        for b in page.get_text("dict")["blocks"]:
            if b.get("type") != 0:
                continue
            lines, sizes = [], {}
            for ln in b["lines"]:
                s = "".join(sp["text"] for sp in ln["spans"])
                for sp in ln["spans"]:
                    sizes[round(sp["size"], 1)] = sizes.get(round(sp["size"], 1), 0) + len(sp["text"].strip())
                lines.append(s)
            text = "\n".join(lines)
            if text.strip():
                out.append((text, max(sizes, key=sizes.get) if sizes else 0.0))
    return out


def clean(text: str) -> str:
    for k, v in LIGATURES.items():
        text = text.replace(k, v)
    text = re.sub(r"(?<=[a-z])-\s*\n\s*(?=[a-z]+-)", "-", text)  # compound split at a hyphen: "state-\nof-the-art"
    text = re.sub(r"(?<=[a-z])-\s*\n\s*(?=[a-z])", "", text)   # de-hyphenate line breaks: "pro-\ngression"
    text = re.sub(r"-\s*\n\s*", "-", text)                    # keep real hyphens: "state-of-\nthe-art"
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract(path: str | Path) -> tuple[str | None, str]:
    """Return (abstract, info). abstract is None on failure; info then says why."""
    path = str(path)
    if not Path(path).exists():
        return None, "PDF 文件不存在"
    try:
        doc = _open(path)
    except ImportError:
        return None, "缺少 PyMuPDF（pip install pymupdf）"
    except Exception as e:
        return None, f"无法打开 PDF：{str(e)[:80]}"
    blocks = _blocks(doc)
    if not blocks:
        return None, "PDF 没有可提取的文字（可能是扫描版）"
    start = next((i for i, (t, _) in enumerate(blocks) if HEAD.match(t)), None)
    if start is None:
        return None, "前两页未找到 Abstract 标题"
    parts, size = [], None
    for i in range(start, len(blocks)):
        t, sz = blocks[i]
        if i == start:
            t = HEAD.sub("", t, count=1)
            if not t.strip():          # heading on its own line/block ("Abstract" / "a b s t r a c t")
                continue
        elif STOP_BLOCK.match(t):
            break
        elif FURNITURE.match(t):
            continue
        elif size and sz < size - 0.6:  # footnotes / affiliations in smaller type
            continue
        if size is None:
            size = sz
        m = STOP.search(t)
        if m:
            parts.append(t[:m.start()])
            break
        parts.append(t)
        if sum(len(p) for p in parts) > MAX_CHARS:
            break
    ab = clean("\n".join(parts))
    if len(ab) < MIN_CHARS:
        return None, f"提取结果过短（{len(ab)} 字符），可能版式特殊"
    if len(ab) > MAX_CHARS:
        return None, f"提取结果过长（{len(ab)} 字符），没找到摘要结尾"
    return ab, f"{len(ab)} 字符"


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for f in sys.argv[1:]:
        ab, info = extract(f)
        print(f"===== {f}\n[{info}]\n{ab or ''}\n")
