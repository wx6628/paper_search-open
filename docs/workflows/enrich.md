# Workflow: enrich — abstracts, citation counts, OA links (L1) and OA PDFs (L2)

Input: a group name, a list of paper IDs, or `all`; optionally `pdf`.

- Metadata (L1): `python -I ps/enrich.py meta --group "<group>"`, or `--ids 1,2,3`, or `--all-missing`.
  This uses OpenAlex single-paper lookups by DOI (free), Springer, and arXiv. Add `--title-search` for papers with neither a DOI nor an arXiv ID; it costs $0.001 per paper.
  `meta` falls back to extracting the abstract from the local PDF (offline) when online sources have none.
- Abstract from local PDFs only (offline, no API quota): `python -I ps/enrich.py abstract-pdf --group "<group>"` (`--dry-run` to preview). Only papers without an abstract are touched.
- PDF (L2), only if the user asked for it: `python -I ps/enrich.py pdf --group "<group>"`. This downloads open-access versions only.

Report in Chinese: how many were filled in, and which papers couldn't get an abstract or PDF and why. Elsevier journals don't make abstracts available online; if the user has linked the PDF, the abstract comes from the PDF instead. Paywalled PDFs have to be downloaded by the user and linked in the GUI.
Finish with `python -I ps/scratch.py clean`.
