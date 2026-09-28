#!/usr/bin/env bash
# ============================================================
# 抖音火花助手 —— Linux（Debian / Ubuntu）后端启动脚本
#
# 做四件事：确认 python3 可用 -> 准备 Linux 结构的 .venv
#        -> 装依赖 -> 在「有显示环境」的前提下启动 backend.py。
#
# 为什么一定要有显示环境：这个分支刻意用「可见」Chrome，
# 目的是让用户自己在浏览器窗口里过扫码 / 短信验证。
# 服务器上没有 $DISPLAY 时 Chrome 根本起不来（或起成一个连不上去的僵尸进程），
# 所以下面在没有 $DISPLAY 时会自动套一层 xvfb-run。
# ============================================================
set -euo pipefail

# 相对路径以脚本所在目录为准，避免依赖调用者的当前目录。
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

log()  { printf '[spark-web] %s\n' "$*"; }
warn() { printf '[spark-web] %s\n' "$*" >&2; }
die()  { printf '[spark-web] %s\n' "$*" >&2; exit 1; }

# ---------- 1. 确认 python3 可用 ----------
# 判据是「真的能执行 + 版本够」，而不是 command -v 有结果就算数。
#
# 允许用 SPARK_PYTHON 指定解释器：宝塔 / aaPanel 这类面板装的 Python
# 通常不在系统 PATH 里（例如 /www/server/pyporject_evn/<版本>/bin/python3），
# 系统自带的 python3 又常是 3.6/3.8，直接判为「版本过低」。
# 有了这个变量，面板里填 `SPARK_PYTHON=/www/server/.../bin/python3` 即可，不用改脚本。
PYTHON_BIN="${SPARK_PYTHON:-python3}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  die "没有找到 $PYTHON_BIN。Debian/Ubuntu 上请先安装：sudo apt update && sudo apt install -y python3 python3-venv python3-pip
      （用面板装的 Python 时，请设 SPARK_PYTHON=/面板里的/python3 绝对路径）"
fi

# 命令存在还不够，得真的能执行（PATH 里可能有坏掉的 shebang 包装脚本）。
if ! "$PYTHON_BIN" -c 'import sys' >/dev/null 2>&1; then
  die "$PYTHON_BIN 存在但无法执行，请检查安装是否完整（dpkg -l | grep python3）。"
fi

# 3.10 起才有本项目用到的写法（如 X | Y 类型标注、match 等），低于此版本直接拒绝，
# 免得跑到一半才抛出 SyntaxError。
if ! "$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 10) else 1)' 2>/dev/null; then
  die "$PYTHON_BIN 版本过低：$("$PYTHON_BIN" -V 2>&1)。本项目需要 Python 3.10 及以上。"
fi
log "使用 Python $("$PYTHON_BIN" -V 2>&1)（$PYTHON_BIN）"

# ---------- 2. 准备虚拟环境 ----------
# 判据是 .venv/bin/python 是否存在，而不是 .venv 目录是否存在：
# 目录存在但结构不对（例如从 Windows 拷来的、或建到一半失败的）
# 会让「跳过创建」变成「继续用一个坏环境」。
VENV_DIR="$SCRIPT_DIR/.venv"
VENV_PY="$VENV_DIR/bin/python"

if [ -d "$VENV_DIR" ] && [ ! -x "$VENV_PY" ]; then
  warn ".venv 目录存在，但 $VENV_PY 不存在或不可执行 —— 这不是可用的 Linux 虚拟环境。"
  warn "  常见原因："
  warn "    1. 这个 .venv 是在 Windows 上创建的（里面是 Scripts/python.exe，没有 bin/python）；"
  warn "    2. 之前创建到一半失败，或者被拷贝时丢了软链接和可执行权限。"
  warn "  处理办法（二选一）："
  warn "    A. 删掉后重跑本脚本（脚本会自动重建）：rm -rf .venv"
  warn "    B. 手动重建：python3 -m venv .venv"
  die  "已停止，请先处理 .venv。"
fi

if [ ! -d "$VENV_DIR" ]; then
  log "正在创建虚拟环境 .venv ..."
  if ! "$PYTHON_BIN" -m venv "$VENV_DIR"; then
    die "创建虚拟环境失败。Debian/Ubuntu 上通常是缺少 python3-venv：sudo apt install -y python3-venv"
  fi
else
  log "复用已有的虚拟环境 .venv"
fi

# ---------- 3. 安装依赖 ----------
[ -f "$SCRIPT_DIR/requirements.txt" ] || die "找不到 requirements.txt（期望位置：$SCRIPT_DIR/requirements.txt）"

log "正在安装依赖（首次会比较慢，需要能访问 PyPI）..."
"$VENV_PY" -m pip install -r "$SCRIPT_DIR/requirements.txt" \
  || die "依赖安装失败，请检查网络、代理或 pip 源后重试。"

# ---------- 4. 启动 ----------
export PORT="${PORT:-9844}"

# 需要图形环境：见文件头说明。有 $DISPLAY 就直接用；
# 没有的话优先借用 xvfb-run（它会自己挑一个空闲的虚拟屏号并设置 $DISPLAY）。
USE_XVFB=0
if [ -z "${DISPLAY:-}" ]; then
  if command -v xvfb-run >/dev/null 2>&1; then
    USE_XVFB=1
    log "当前没有 \$DISPLAY，改用 xvfb-run -a 在虚拟显示里启动（适合服务器 / systemd）。"
    warn "  提醒：Xvfb 是「看不见」的屏幕，扫码 / 短信验证时你在终端里看不到画面。"
    warn "  需要人工过验证时，要么用带桌面的机器跑，要么另开 x11vnc 连上去看。"
  else
    warn "当前没有 \$DISPLAY，也没有找到 xvfb-run。"
    warn "  本项目的 Chrome 必须跑在图形环境里，否则大概率启动失败。解决办法："
    warn "    sudo apt update && sudo apt install -y xvfb   # 装完重跑本脚本，会自动走 xvfb-run"
    warn "  或者直接在带桌面的机器上运行（那里通常已经有 \$DISPLAY）。"
    warn "  现在仍然继续尝试启动，失败信息见下面的日志。"
  fi
else
  log "检测到 DISPLAY=$DISPLAY，直接使用当前显示环境。"
fi

log "正在启动后端：http://127.0.0.1:$PORT"
log "按 Ctrl+C 可退出。"

if [ "$USE_XVFB" = "1" ]; then
  exec xvfb-run -a "$VENV_PY" "$SCRIPT_DIR/backend.py"
else
  exec "$VENV_PY" "$SCRIPT_DIR/backend.py"
fi
