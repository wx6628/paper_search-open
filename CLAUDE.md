@AGENTS.md

## Claude Code

- Slash commands in `.claude/commands/` (`/find`, `/fresh`, `/enrich`, `/queue`, `/verify`, `/bib`, `/library`, `/venues`, `/dblp-update`) are thin wrappers: each one opens the matching file in `docs/workflows/` and follows it.
- Start long-running processes (the GUI server, `dblp_build.py`) with `run_in_background`.
- If a paper-reading skill is installed, it may be used for close reading, but `docs/精读笔记模板.md` decides the note format.
