#!/bin/bash
# 把中转装成 launchd 开机自启（崩溃自动拉起）
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.opencode-go-relay"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"

mkdir -p "$HOME/Library/LaunchAgents"
sed "s#__DIR__#$DIR#g" "$DIR/$LABEL.plist" > "$TARGET"

# 先停掉可能已在运行的实例，避免端口冲突
"$DIR/stop.command" >/dev/null 2>&1 || true
launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true

launchctl bootstrap "gui/$(id -u)" "$TARGET"
launchctl kickstart -k "gui/$(id -u)/$LABEL"

sleep 1
if curl -s -m 3 http://127.0.0.1:8787/__relay/health >/dev/null; then
  echo "已安装并启动：$TARGET"
  echo "WorkBuddy 里填：http://127.0.0.1:8787/v1/chat/completions"
else
  echo "已安装，但健康检查没通过，看 $DIR/relay.err.log"
fi
