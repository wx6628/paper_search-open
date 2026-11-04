# Workflow: queue — process AI tasks queued from the GUI

Input: optionally a maximum number of tasks (default: all).

1. List the queue: `python -I ps/library.py queue list`
2. Handle each task by type:
   - **enrich**: `python -I ps/enrich.py meta --ids <pid>`
   - **fetch_pdf**: `python -I ps/enrich.py pdf --ids <pid>`
   - **read** (AI close reading):
     a. Get the paper with `python -I ps/library.py show <pid>`.
     b. Get the full text. If the paper has no local PDF (`pdf_path` empty) but has an arXiv ID or an open-access link, download it first with `python -I ps/enrich.py pdf --ids <pid>` (goes through the rate limiter). Then extract the text with the workstation tool, never with ad-hoc scripts or other temp folders:
        `python -I ps/pdfread.py text <pid> --task queue-<qid> --no-render` -> `data/tmp/queue-<qid>/text.txt` (scratch).
        Only if the architecture is unclear from the text, render pages (drop `--no-render`) and look at the framework figure if your tool can view images. Do not save or embed figures.
        If your tool has its own web access, reading the arXiv HTML/PDF directly is also fine, but keep within `config/api_limits.yaml`.
     c. Read the whole paper and write the note **exactly per `docs/精读笔记模板.md`**. If your AI tool ships its own paper-reading skill or template, you may use it for the reading, but the workstation template decides the note's structure: no 关键公式 / 关键图表 sections, no figure extraction, no 关键缺口与适用边界, formulas in plain text only (also in concept notes), and a 创新思路主线 section that links 背景 → 问题 → Idea → 机制 → Contributions. Every number and claim must come from the paper; write "未读到/未核实" otherwise.
     d. Save the note to `library/ai_notes/<大组>/<小组>/<方法名>.md` (the paper's library group; use `library/ai_notes/未分组/` if it has none). The note must contain the DOI or arXiv link.
     e. Link the note: run `python -I ps/library.py link-note <pid> <note path>`. If papers the note relies on or compares against are already in the library, add relations with `python -I ps/library.py relate <pid> <other pid> <基于/使用|对比了|相关> --note "..."` and mention them in the reply.
     f. If there is no full text at all (paywalled with no local PDF), don't fake a reading. Mark it failed and say why: `python -I ps/library.py queue fail <qid> --message "无全文：请在界面中关联 PDF 后重试"`.
3. After each task finishes, run `python -I ps/library.py queue done <qid> --message "<one-line result>"`. `done` / `fail` also delete that task's scratch folder `data/tmp/queue-<qid>/`.
4. At the end (also when stopping early), run `python -I ps/scratch.py clean` so no intermediate data is left behind.
5. Finally, summarise in Chinese: for each paper the one-line verdict, what finished, what failed and why. Remind the user that results show up in the GUI after a refresh.
