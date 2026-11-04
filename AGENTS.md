# Paper search workstation (paper_search-open)

A paper-search workstation built around an offline dblp dump. dblp's venue coverage defines what counts as a
trusted venue, and fresher API sources fill the gap between dblp snapshots. Found papers go into a personal
library with a local web GUI. Library papers are enriched over time: abstract, PDF, AI analysis, notes,
groups and relations.

This file is the single source of rules for every AI coding agent (Claude Code, Codex, Gemini CLI, Cursor, ...).
`CLAUDE.md` only imports it.

- **Write replies to the user in Chinese.**
- The user may be a beginner. Explain results plainly, give file paths, and say what to do next.
- User guide: `docs/操作指南.md`. Beginner tutorial and extension guide: `docs/新手教程与扩展指南.md`. Design: `docs/DESIGN.md`. Keep the guides up to date when features change.

## Hard rules

- **Never access dblp.org online.** No web fetch, no curl to its search or API, no dblp source in any third-party search tool. The site blocks automated access with human-verification checks. All dblp lookups go through the local database (`ps/dblp_query.py`). The only allowed dblp traffic is downloading the dump file, which the user usually does by hand.
- **Respect `config/api_limits.yaml`.** Workstation scripts enforce it through `ps/netlib.py`, which keeps a ledger in `data/usage.sqlite`. Any ad-hoc request you make must stay within the limits as well. On a 429, wait for `Retry-After` and retry at most once. On a human-verification challenge, stop using that source and never try to bypass it. OpenReview lookups by note ID trigger a challenge, so take OpenReview data only from search results.
- **Do not scrape Google Scholar.**
- **Keys live only in `.env` in the project root** (template: `.env.example`; environment variables also work). Never print, log or commit them. Never ask the user to paste keys into chat; tell them to edit `.env` themselves.
- Every paper you report must come from a tool result. Never cite from memory.
- Run Python that reads downloaded data with `python -I`.
- **AI close-reading notes follow `docs/精读笔记模板.md`** (for the queue workflow and for direct requests like "精读库里第 N 篇"). It overrides any reading template your tool ships with: no 关键公式 / 关键图表 sections, no figure extraction, no 关键缺口与适用边界, plain-text formulas only (concept notes too), and a 创新思路主线 section linking 背景 → 问题 → Idea → 机制 → Contributions.
- **Leave no intermediate data behind.** Put scratch files only in `data/tmp/<task>/` (use `ps/scratch.py dir <task>` / `ps/pdfread.py`), never in system temp folders or ad-hoc locations. Consumed run files are deleted by `import` / `finalize`; drop unused ones with `ps/scratch.py drop`. End every command or multi-step task with `python -I ps/scratch.py clean`.

## Workflows (`docs/workflows/`)

When the user asks for one of these (by name, as a slash command, or in plain words), open the workflow file and follow it.

| Name | User says, e.g. | Workflow file | Purpose |
|---|---|---|---|
| find | `/find <question> [group]`, "帮我检索 xxx 方向的论文" | `docs/workflows/find.md` | Full search: local dblp by tier and year band, plus the dblp gap. Screen, import into the library, write a report and BibTeX |
| fresh | `/fresh <keywords>`, "最近有什么新论文" | `docs/workflows/fresh.md` | Only papers in priority venues that dblp hasn't indexed yet |
| enrich | `/enrich <group or ids>`, "补全摘要" | `docs/workflows/enrich.md` | Abstracts, citation counts and OA links (L1), OA PDFs (L2) |
| queue | `/queue`, "处理队列" | `docs/workflows/queue.md` | AI tasks queued from the GUI (close reading → md note linked to the paper) |
| verify | `/verify <file>`, "核验参考文献" | `docs/workflows/verify.md` | Check a reference list for real existence and correct details; corrected BibTeX |
| bib | `/bib <group or ids or starred>`, "导出 BibTeX" | `docs/workflows/bib.md` | Export BibTeX, dblp entries where available |
| library | `/library`, "打开论文库" | `docs/workflows/library.md` | Start the GUI at http://127.0.0.1:8765 |
| venues | `/venues <list>`, "配置优先出处" | `docs/workflows/venues.md` | Add / change priority venues in `config/venues.yaml` |
| dblp-update | `/dblp-update`, "更新 dblp" | `docs/workflows/dblp-update.md` | Build or refresh `data/dblp.sqlite` from the dump |

First-time setup: if `data/dblp.sqlite` is missing, most workflows can't run. Tell the user and point to `dblp-update`.
If `.env` is missing, scripts still work with reduced coverage (no OpenAlex key → no OpenAlex search); say so once.

## Layout

```
AGENTS.md / CLAUDE.md    rules for AI agents (CLAUDE.md imports AGENTS.md)
docs/workflows/*.md      agent-neutral workflow instructions
.claude/commands/*.md    Claude Code slash commands (thin wrappers around docs/workflows)
config/venues.yaml       priority venues (tier, dblp key, main-track regex, OpenAlex id, ISSN, fresh sources), year_priority
config/api_limits.yaml   per-API limits (user-set limit_* values)
config/seed_papers.yaml  (unused for now)
ps/dblp_build.py         build data/dblp.sqlite from data/dblp/*.xml.gz (+ dblp.dtd)
ps/dblp_query.py         local dblp: search / venue / get / bib / stats
ps/fresh.py              dblp gap: OpenAlex + Crossref + OpenReview + arXiv per venue `fresh:` list
ps/search.py             find backend: gather (candidates) -> finalize (library + report + bib)
ps/library.py            library CLI: add/import/show/list/set/group/relate/attach-pdf/link-note/scan-notes/note/bib/queue/stats/recompute-tiers
                         groups are two-level: path '大组' or '大组/小组'; adding to a sub-group also adds the big group
ps/enrich.py             L0->L1 (meta), L1->L2 (OA pdf), abstract-pdf (abstract from local PDF, offline)
ps/pdfabstract.py        PDF -> abstract extraction (PyMuPDF, first two pages, skips footnotes)
ps/pdfread.py            close-reading prep: PDF text/page renders -> data/tmp/<task>; figure crops -> note assets
ps/openalex_sources.py   look up OpenAlex source ids / ISSNs by venue name (used when configuring venues)
ps/scratch.py            intermediate-data policy: data/tmp scratch, consumed data/runs files, .part leftovers
ps/verify.py             reference verification
ps/netlib.py             keys (.env), rate limits, budgets, ledger, 429/challenge handling
ps/server.py + web/      local GUI (127.0.0.1 only; writes need the X-PS header)
library/                 created on first use: library.sqlite, pdfs/, ai_notes/, notes/<id>.txt  (personal, git-ignored)
data/                    dblp.sqlite (local dblp), dblp/ (dump), runs/ (candidate pools), tmp/ (scratch), usage.sqlite  (git-ignored)
reports/ exports/        search reports and BibTeX (git-ignored)
```

## Search policy

The order is: tier 1 → tier 2 → tier 3 in local dblp, then the dblp gap (fresh) for the same venues, then other venues, labelled as non-priority.
Year priority comes from `year_priority` in `config/venues.yaml` (default 2024+ > 2021–2023 > pre-2021). These are ranking preferences, not filters: older classics are kept through per-band quotas.
Status labels for gap papers: published / accepted (OpenReview) / under_review / claimed (arXiv comment only, unverified). Always show the label.

## Agent-specific notes

- **Claude Code**: slash commands live in `.claude/commands/` and point at the workflow files above. Start the GUI with a background Bash call.
- **Codex / other agents**: there are no project slash commands; the user types the name in plain words (e.g. "执行 find：半监督目标检测"). Look the name up in the table above. Network access may be disabled in your sandbox; the scripts need it for everything except local dblp search, so ask the user to allow network access for `python -I ps/...` commands if they fail with connection errors.
