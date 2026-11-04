# Workflow: find — full literature search

Input: a research question (Chinese or English), optionally followed by a group name.

Follow AGENTS.md (no online dblp, respect `config/api_limits.yaml`). Every paper must come from a tool result. Never add papers from memory.

1. **Understand the question.** Pick out the core concept, method or task, and any scope limits the user gave: years, venues, modality. Only ask first if the question is truly ambiguous. Otherwise proceed and state your interpretation in one line.
2. **Write 3–5 English query variants.** Search matches stemmed words in titles, all words ANDed, so each variant should be 2–5 core words. Cover synonyms and abbreviations (e.g. "semi-supervised" / "label-efficient", "detection" / "detector"). Don't stack too many words, or recall drops sharply.
3. **Gather candidates.**
   `python -I ps/search.py gather --q "<v1>" --q "<v2>" ... [--year ...] [--since ...]`
   By default this covers all tiers in `config/venues.yaml`. Per tier, slots go 50% / 30% / 20% to the three year bands of `year_priority`. The 2 best variants also go through the dblp-gap layer (fresh.py), and the top candidates get free OpenAlex abstract lookups. Record the `file` path from the output.
4. **Screen.** Use titles and abstracts to judge relevance to the research question, candidate by candidate. Keep only clearly relevant papers, usually 15–40. For each kept paper write a one-sentence reason in Chinese saying what it does and how it relates to the question. If a candidate has no abstract and the title is ambiguous, keep it cautiously and say "（仅凭标题判断）" in the reason.
   - If fewer than about 10 relevant papers turn up, add 1–2 new rounds of variants (gather again), at most twice.
   - `[arXiv claim]` means the authors say it was accepted but this is unverified. `[accepted]` means officially accepted. Keep both labels in your reasons.
5. **Save and import.** Write the reasons to `data/tmp/reasons_<timestamp>.json` as `{"<cand>": "<reason>"}`, then run:
   `python -I ps/search.py finalize <file> --keep <cand list> --reasons <reasons file> --group "<group>" --title "<research question>"`
   finalize deletes the consumed run file and the reasons file itself (the search record, report and BibTeX keep everything). If you gathered several rounds, finalize each run you used and delete the rounds you did not use with `python -I ps/scratch.py drop <run file>...`. Finish with `python -I ps/scratch.py clean`.
   Group name: use the second argument if the user gave one. It may be `大组` or `大组/小组` (two-level groups; a missing group is created automatically). Otherwise use a short Chinese topic name of 4–10 characters as the big group. If the library already has a matching big group, consider using `existing big group/new sub-group` (check with `python -I ps/library.py group list`).
6. **Reply to the user in Chinese.** Cover:
   - candidates found and papers kept, broken down by tier and year band;
   - 3–5 papers most worth reading first, each with one sentence;
   - recent papers from the dblp-gap layer;
   - the report path, the BibTeX path and the group name;
   - a reminder that they can browse, read and take notes in the GUI (http://127.0.0.1:8765).
