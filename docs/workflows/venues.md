# Workflow: venues — configure priority venues

Input: a list of venue names (full names or common abbreviations) with a tier for each, e.g.
"tier 1: CVPR, TPAMI; tier 2: WACV, Neurocomputing". The user may also ask to remove venues or change tiers.

Requires the local dblp (`data/dblp.sqlite`). If it doesn't exist yet, stop and point the user to `docs/workflows/dblp-update.md`.

For each venue:

1. **dblp key.** `python -I ps/dblp_query.py venue "<name or abbreviation>"` lists candidate keys (`conf/xxx`, `journals/xxx`) with paper counts and year ranges. Pick the main conference / journal, not a workshop series. If unsure between candidates, ask the user.
2. **Main-track filter (conferences).** `python -I ps/dblp_query.py venue "<dblp key>" --limit 40` lists every venue string stored under that key. Workshops often share the key with the main conference (e.g. 'CVPR' vs 'CVPR Workshops'). Write a `dblp_venue_regex` that matches only the main-track strings, including volume suffixes such as 'ECCV (5)'. Journals usually don't need one.
3. **OpenAlex id and ISSN.** `python -I ps/openalex_sources.py "<full name>"` (one OpenAlex search, $0.001). Take the matching source's `openalex` id and ISSN. LNCS-style proceedings (ECCV, MICCAI, ...) often have no usable OpenAlex source; leave it out.
4. **OpenReview.** For venues that use OpenReview (ICLR, NeurIPS, ICML, ...), set `openreview: <Venue>.cc`.
5. **fresh sources** (used to find papers dblp hasn't indexed yet):
   - journals: `[openalex, crossref]`;
   - IEEE/CVF and similar conferences with an OpenAlex source: `[openalex, arxiv]` or `[openalex, crossref]`;
   - Springer LNCS conferences: `[springer, crossref]`;
   - OpenReview conferences: `[openreview]` (plus `openalex` if it has a source).
6. Write the entry to `config/venues.yaml` in the same one-line style as the existing entries, under the right tier section. Keep the file's comments.

After editing:

- Validate: `python -I -c "import yaml; yaml.safe_load(open('config/venues.yaml', encoding='utf-8'))"`.
- Re-apply tiers to papers already in the library: `python -I ps/library.py recompute-tiers`.
- Quick check: `python -I ps/dblp_query.py search "<a common word in the field>" --tier <tier> --limit 5` should return papers from the new venues.
- Report in Chinese: each venue with its dblp key, filter, OpenAlex id / ISSN and fresh sources, and anything you could not determine.

If the user changes `year_priority`, also run `recompute-tiers`.
