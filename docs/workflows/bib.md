# Workflow: bib — export BibTeX

Input: a group name, a list of paper IDs (e.g. `1,3,5`), or `starred`.

- Group name: `python -I ps/library.py bib --group "<name>" -o exports/<group>_<date>.bib`
- ID list: `python -I ps/library.py bib --ids <ids> -o exports/selected_<date>.bib`
- starred: `python -I ps/library.py bib --starred -o exports/starred_<date>.bib`

If you can't tell which group the user means, list the groups with `python -I ps/library.py group list` and confirm.
Report in Chinese: the number of entries, the file path, and which entries are not formally published, such as arXiv claims or papers under review. Those entries carry a `note` field.
