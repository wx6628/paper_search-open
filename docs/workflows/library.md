# Workflow: library — start the GUI

1. Check whether it is already running: `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8765/api/stats`. If the answer is 200, it's running already.
2. If it isn't running, start it as a background (detached) process. It is a long-running server, so don't block on it:
   `python -I ps/server.py --port 8765`
   If your environment cannot keep a background process alive (some sandboxed agents kill child processes when the turn ends), tell the user to double-click `打开论文库.bat` (Windows) or run `./start.sh` (macOS / Linux) instead.
3. Tell the user, in Chinese, to open http://127.0.0.1:8765 in a browser. Mention the current library size from `python -I ps/library.py stats`.
