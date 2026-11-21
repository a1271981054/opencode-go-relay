#!/bin/sh
# 通用启动脚本（macOS / Linux 都行）
cd "$(dirname "$0")" || exit 1
exec python3 relay.py "$@"
