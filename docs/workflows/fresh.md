# Workflow: fresh — papers dblp hasn't indexed yet

Input: keywords, optionally `--since YYYY-MM-DD` and `--under-review`.

Finds recent papers in the priority venues (`config/venues.yaml`) that the local dblp hasn't indexed yet.

1. If the request is in Chinese, turn it into 2–5 core English keywords. Then run:
   `python -I ps/fresh.py "<keywords>" [--since ...] [--under-review]`
   Each run is saved to `data/runs/<ts>_fresh.json` as the candidate pool for importing.
2. Summarise in Chinese, grouped by venue:
   - title and one-line gist, based on the abstract;
   - status: published / accepted (OpenReview) / under review / arXiv claim (unverified).
3. Ask whether the user wants these added to the library. If yes, import the kept ones:
   `python -I ps/library.py import <run.json> --keep <numbers> --group "<group>"`
   Import deletes the consumed run file. If the user wants to import into several groups from the same run, pass `--keep-run` on all but the last import. Runs that are never imported expire automatically after 7 days.
4. Finish with `python -I ps/scratch.py clean`.
