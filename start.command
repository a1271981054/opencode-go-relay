#!/bin/bash
# 双击启动 OpenCode Go 中转。终端窗口关掉 = 中转停止。
cd "$(dirname "$0")" || exit 1

if lsof -ti tcp:8787 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "8787 端口已经在跑，不用重复启动。"
  echo "WorkBuddy 里填：http://127.0.0.1:8787/v1/chat/completions"
  read -n 1 -s -r -p "按任意键关闭…"
  exit 0
fi

echo "启动中转，按 Ctrl+C 停止"
exec /usr/bin/env python3 relay.py
