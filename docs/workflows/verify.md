# Workflow: verify — check a reference list

Input: a path to a `.bib` file or a `.txt` file with one reference per line (or references pasted into the chat).

1. If the user pasted references into the chat instead of giving a file, save them to `data/tmp/refs_<timestamp>.txt` first, one per line (scratch; removed in step 5).
2. Run `python -I ps/verify.py <file> --out exports/verified_<timestamp>.bib`
3. Report in Chinese:
   - counts of ✅ verified / ⚠️ needs checking / ❌ not found;
   - each ⚠️ entry with what's wrong (title, year, first author, DOI) and the correct value;
   - each ❌ entry flagged as possibly fabricated or very obscure. Suggest the user confirm it manually.
   Never fill in or "fix" a reference from your own memory. Only use what the tools found.
4. Give the path to the corrected BibTeX. It uses dblp entries where available.
5. Run `python -I ps/scratch.py clean` to remove the scratch files of this run.
