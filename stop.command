#!/bin/bash
# 停止监听 8787 的中转进程（只杀命令行里带 relay.py 的，避免误杀）
PIDS=$(lsof -ti tcp:8787 -sTCP:LISTEN 2>/dev/null)
if [ -z "$PIDS" ]; then
  echo "8787 上没有进程在跑。"
  exit 0
fi
KILLED=""
for pid in $PIDS; do
  if ps -o command= -p "$pid" 2>/dev/null | grep -q "relay.py"; then
    kill "$pid" 2>/dev/null && KILLED="$KILLED $pid"
  fi
done
if [ -n "$KILLED" ]; then
  echo "已停止:$KILLED"
else
  echo "8787 被别的程序占用（不是 relay.py），没有动它。"
fi
