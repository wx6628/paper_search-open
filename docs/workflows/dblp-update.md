# Workflow: dblp-update — build or refresh the local dblp

Never download from dblp.org yourself unless the user explicitly asks; the user normally downloads the dump by hand. Never query dblp.org's search or API.

1. Check that the dump is in `data/dblp/`: a `*.xml.gz` file, ideally with its `.md5`, next to `dblp.dtd` (shipped with this repo; the dump's DTD must sit in the same folder).
   - Monthly release (fast mirror, recommended): https://drops.dagstuhl.de/entities/collection/10.4230/dblp.xml → latest `dblp-YYYY-MM-DD.xml.gz` (+ `.md5`). If the release ships its own `.dtd`, rename it to `dblp.dtd` and replace the old one.
   - Daily dump: https://dblp.org/xml/dblp.xml.gz (+ `.md5`, and `dblp.dtd`).
2. Build (about 10 minutes, needs ~10 GB free disk). Ask the user to close the GUI first:
   `python -I ps/dblp_build.py --dump data/dblp/<file>.xml.gz --db data/dblp.sqlite`
   The new database is written to a temp file and swapped in only on success, so a failed build leaves the old one intact. The personal library is not affected.
   This is a long job: run it in the background if your tool supports that, otherwise tell the user to run the command in a terminal.
3. Check: `python -I ps/dblp_query.py stats`.
4. Re-apply tiers to library papers (in case `config/venues.yaml` changed meanwhile): `python -I ps/library.py recompute-tiers`.
5. The old dump can be deleted once the build succeeded (ask the user first). Finish with `python -I ps/scratch.py clean`.
