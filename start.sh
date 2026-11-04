#!/usr/bin/env sh
# 启动图形化论文库（macOS / Linux）。Windows 请双击 打开论文库.bat
cd "$(dirname "$0")" || exit 1
PY=python3
command -v "$PY" >/dev/null 2>&1 || PY=python
"$PY" -I ps/server.py --port 8765 &
SERVER_PID=$!
sleep 2
URL=http://127.0.0.1:8765
if command -v open >/dev/null 2>&1; then open "$URL"
elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL"
else echo "请在浏览器打开 $URL"; fi
echo "论文库已启动（按 Ctrl+C 关闭）"
wait "$SERVER_PID"
