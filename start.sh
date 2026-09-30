#!/bin/bash
# ============================================================
# 抖音火花助手 —— 后端启动快捷入口（Linux / macOS）
#
# 真正的逻辑都在 start-backend.sh 里（建 .venv、装依赖、
# 判断 $DISPLAY 决定要不要套 xvfb-run、最后拉起 backend.py）。
# 这里只做转发，避免两个脚本各写一份、时间一长就跑偏。
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="$SCRIPT_DIR/start-backend.sh"

if [ ! -f "$TARGET" ]; then
    echo "找不到 start-backend.sh（期望位置：$TARGET）" >&2
    exit 1
fi

exec bash "$TARGET" "$@"
