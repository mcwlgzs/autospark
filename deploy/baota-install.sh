#!/usr/bin/env bash
# ============================================================
# 抖音火花助手 —— 宝塔面板一键安装脚本（Debian 12 / Ubuntu 22.04+）
#
# 把 deploy/baota-deploy.md 里**能自动化的部分**串成一条命令：
#   系统依赖(xvfb/中文字体/python3-venv) -> Google Chrome -> 可写目录
#   -> .venv + 依赖 -> dist 前端产物 -> deploy/baota-env.sh
#   -> Supervisor 守护配置（能找到目录就写） -> nginx 反代（给了站点配置才改）
#
# 它**不代替**你在宝塔面板里点的那几下（添加站点、装进程守护管理器、
# 申请 SSL），跑完会把「还剩什么要手点」列出来。
#
# 用法：
#   sudo bash deploy/baota-install.sh --dry-run          # 先看它打算干什么
#   sudo bash deploy/baota-install.sh                    # 真的装
#   sudo bash deploy/baota-install.sh --python /www/server/pyporject_evn/3.11/bin/python3 \
#        --run-user www --site-conf /www/server/panel/vhost/nginx/spark.example.com.conf
#
# 幂等：每一步都先判断再做，重复执行安全（.venv / dist / nginx 标记都会跳过）。
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
GENERATED_DIR="$SCRIPT_DIR/generated"

# ---------- 默认值（都可用参数覆盖） ----------
PORT=9844
DOMAIN=""
RUN_USER=""
PYTHON_ARG=""
SITE_CONF=""
SUPERVISOR_DIR=""
DRY_RUN=0
FORCE=0
SKIP_CHROME=0
SKIP_DIST=0
SKIP_SUPERVISOR=0
SKIP_NGINX=0

log()  { printf '\033[32m[spark-web]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[spark-web]\033[0m %s\n' "$*" >&2; }
step() { printf '\n\033[36m==> %s\033[0m\n' "$*"; }
die()  { printf '\033[31m[spark-web]\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  sed -n '2,26p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  cat <<'EOF'

选项：
  --project-dir <目录>     项目根目录（默认：本脚本的上一级）
  --python <解释器>        Python 3.10+（默认：$SPARK_PYTHON，再默认 python3）
  --run-user <用户>        以后由谁常驻（root 或 www，默认：当前用户）
  --port <端口>            后端端口（默认 9844，只监听 127.0.0.1）
  --domain <域名>          已建好的站点域名（只用于提示，不建站）
  --site-conf <路径>       宝塔站点配置文件；给了才会写反向代理（自动备份 + nginx -t）
  --supervisor-conf-dir <目录>  Supervisor 配置目录（默认自动探测宝塔的路径）
  --skip-chrome            不装 Google Chrome
  --skip-dist              不构建前端产物
  --skip-supervisor        不写 Supervisor 配置
  --skip-nginx             不碰 nginx
  --dry-run                只打印将要做的事，不改任何东西
  --force                  非 Debian/Ubuntu 也继续（不推荐）
  -h, --help               看这个帮助
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --project-dir) PROJECT_DIR="${2:?}"; shift 2 ;;
    --python)      PYTHON_ARG="${2:?}"; shift 2 ;;
    --run-user)    RUN_USER="${2:?}"; shift 2 ;;
    --port)        PORT="${2:?}"; shift 2 ;;
    --domain)      DOMAIN="${2:?}"; shift 2 ;;
    --site-conf)   SITE_CONF="${2:?}"; shift 2 ;;
    --supervisor-conf-dir) SUPERVISOR_DIR="${2:?}"; shift 2 ;;
    --skip-chrome) SKIP_CHROME=1; shift ;;
    --skip-dist)   SKIP_DIST=1; shift ;;
    --skip-supervisor) SKIP_SUPERVISOR=1; shift ;;
    --skip-nginx)  SKIP_NGINX=1; shift ;;
    --dry-run)     DRY_RUN=1; shift ;;
    --force)       FORCE=1; shift ;;
    -h|--help)     usage; exit 0 ;;
    *) die "看不懂的参数：$1（用 --help 看用法）" ;;
  esac
done

# run：真正执行命令；dry-run 时只回显。
# 回显带 '  ' 缩进，方便一眼看出脚本都动了什么。
run() {
  if [ "$DRY_RUN" = "1" ]; then
    printf '    \033[90m$ %s\033[0m\n' "$*"
    return 0
  fi
  "$@"
}

# write_target <路径>：把 stdin 写到目标文件（dry-run 时只回显内容）。
write_target() {
  local path="$1"
  if [ "$DRY_RUN" = "1" ]; then
    printf '    \033[90m> %s\033[0m\n' "$path"
    sed 's/^/      | /'
    return 0
  fi
  mkdir -p "$(dirname "$path")"
  cat > "$path"
  log "已写入 $path"
}

# ============================================================
step "0/8 环境检查"
# ============================================================
if [ "$DRY_RUN" != "1" ] && [ "$(id -u)" != "0" ]; then
  die "需要 root：sudo bash $0 ...（宝塔面板里请用「终端」以 root 执行）"
fi

OS_ID="unknown"; OS_VER=""
if [ -r /etc/os-release ]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  OS_ID="${ID:-unknown}"; OS_VER="${VERSION_ID:-}"
fi

os_ok=0
case "$OS_ID" in
  debian|ubuntu) os_ok=1 ;;
esac

if [ "$os_ok" = "1" ]; then
  log "系统：$OS_ID $OS_VER"
else
  warn "检测到的是「$OS_ID $OS_VER」，本脚本只针对 Debian 12 / Ubuntu 22.04+ 写过。"
  warn "  CentOS 7 的 yum 源已 EOL、Ubuntu 的 chromium 是 snap 过渡包，都会在浏览器那一步翻车。"
  if [ "$DRY_RUN" = "1" ]; then
    warn "  （--dry-run：继续，只打印计划）"
  elif [ "$FORCE" = "1" ]; then
    warn "  （--force：继续，但请自己核对下面的 apt 步骤是否适用）"
  else
    die "已停止。确认要硬上就加 --force。"
  fi
fi

if [ ! -f "$PROJECT_DIR/backend.py" ]; then
  if [ "$DRY_RUN" = "1" ]; then
    warn "项目目录里没看到 backend.py：$PROJECT_DIR（--dry-run 不检查存在性）"
  else
    die "$PROJECT_DIR 里没有 backend.py —— 用 --project-dir 指到项目根目录。"
  fi
fi
log "项目目录：$PROJECT_DIR"

RUN_USER="${RUN_USER:-$(id -un)}"
log "常驻用户：$RUN_USER（这条链路创建的 .venv / data / chrome-profile 都归它）"

# ============================================================
step "1/8 系统依赖（xvfb / 中文字体 / python3-venv）"
# ============================================================
APT_PKGS="xvfb fonts-noto-cjk wget curl ca-certificates python3-venv python3-pip"
log "将安装：$APT_PKGS"
run env DEBIAN_FRONTEND=noninteractive apt-get update -qq
# shellcheck disable=SC2086
run env DEBIAN_FRONTEND=noninteractive apt-get install -y $APT_PKGS

# ============================================================
step "2/8 时区（不是 Asia/Shanghai 的话，22:00 会变成早上 6 点）"
# ============================================================
WANT_TZ="Asia/Shanghai"
if ! command -v timedatectl >/dev/null 2>&1; then
  warn "没有 timedatectl，跳过时区检查；请确认 TZ=$WANT_TZ（deploy/baota-env.sh 里已经写了）。"
else
  NOW_TZ="$(timedatectl show -p Timezone --value 2>/dev/null || true)"
  if [ -z "$NOW_TZ" ]; then
    NOW_TZ="$(timedatectl 2>/dev/null | sed -n 's/.*Time zone: \([^ ]*\).*/\1/p' || true)"
  fi
  if [ "$NOW_TZ" = "$WANT_TZ" ]; then
    log "时区已经是 $WANT_TZ"
  else
    log "当前时区 ${NOW_TZ:-未知} -> 改成 $WANT_TZ"
    run timedatectl set-timezone "$WANT_TZ"
  fi
fi

# ============================================================
step "3/8 Google Chrome（项目要「可见」浏览器 + 虚拟显示）"
# ============================================================
if [ "$SKIP_CHROME" = "1" ]; then
  warn "--skip-chrome：跳过。请自行保证有浏览器，必要时在 baota-env.sh 里设 CHROME_BINARY。"
elif [ -x /opt/google/chrome/chrome ]; then
  log "已经装好了：/opt/google/chrome/chrome（这就是后端在 Linux 上的默认路径，不用配 CHROME_BINARY）"
else
  CHROME_DEB="/tmp/google-chrome-stable_current_amd64.deb"
  CHROME_URL="https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb"
  if command -v wget >/dev/null 2>&1; then
    run wget -q -O "$CHROME_DEB" "$CHROME_URL"
  else
    run curl -fsSL -o "$CHROME_DEB" "$CHROME_URL"
  fi
  run env DEBIAN_FRONTEND=noninteractive apt-get install -y "$CHROME_DEB"
  if [ "$DRY_RUN" != "1" ] && [ ! -x /opt/google/chrome/chrome ]; then
    die "Chrome 装完了但 /opt/google/chrome/chrome 不存在，请手动确认（Debian 12 也可 apt install chromium chromium-driver，然后设 CHROME_BINARY=/usr/bin/chromium）。"
  fi
fi

# ============================================================
step "4/8 可写目录（data / logs / chrome-profile）"
# ============================================================
for d in data logs chrome-profile; do
  if [ -d "$PROJECT_DIR/$d" ]; then
    log "已存在：$d/"
  else
    run mkdir -p "$PROJECT_DIR/$d"
    log "新建：$d/"
  fi
done

if [ "$RUN_USER" != "$(id -un)" ] || [ "$RUN_USER" = "www" ]; then
  if id "$RUN_USER" >/dev/null 2>&1; then
    log "把项目目录交给 $RUN_USER（否则守护进程写不了 data/state.json）"
    run chown -R "$RUN_USER:$RUN_USER" "$PROJECT_DIR"
  else
    warn "系统里没有用户 $RUN_USER，跳过 chown。"
  fi
fi

# ============================================================
step "5/8 Python 虚拟环境 + 依赖"
# ============================================================
PYTHON_BIN="$PYTHON_ARG"
[ -n "$PYTHON_BIN" ] || PYTHON_BIN="${SPARK_PYTHON:-}"
[ -n "$PYTHON_BIN" ] || PYTHON_BIN="python3"

if [ "$DRY_RUN" = "1" ]; then
  log "将使用 Python：$PYTHON_BIN"
else
  ( command -v "$PYTHON_BIN" >/dev/null 2>&1 || [ -x "$PYTHON_BIN" ] ) \
    || die "找不到 Python 解释器：$PYTHON_BIN
      Debian/Ubuntu：sudo apt install -y python3 python3-venv python3-pip
      用宝塔「Python 项目管理器」装的：加 --python /www/server/pyporject_evn/<版本>/bin/python3"
  "$PYTHON_BIN" -c 'import sys' >/dev/null 2>&1 \
    || die "$PYTHON_BIN 存在但无法执行，请检查安装是否完整。"
  "$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3,10) else 1)' \
    || die "$PYTHON_BIN 版本过低：$("$PYTHON_BIN" -V 2>&1)，本项目需要 Python 3.10 及以上。"
  log "使用 $("$PYTHON_BIN" -V 2>&1)（$PYTHON_BIN）"
fi

VENV_DIR="$PROJECT_DIR/.venv"
VENV_PY="$VENV_DIR/bin/python"

if [ -d "$VENV_DIR" ] && [ ! -x "$VENV_PY" ] && [ "$DRY_RUN" != "1" ]; then
  die "$VENV_DIR 存在，但没有 bin/python —— 这不是可用的 Linux 虚拟环境。
      （最常见的原因：这个 .venv 是在 Windows 上创建的，里面是 Scripts/python.exe）
      删掉后重跑本脚本即可：rm -rf $VENV_DIR"
fi

[ -f "$PROJECT_DIR/requirements.txt" ] || [ "$DRY_RUN" = "1" ] \
  || die "找不到 $PROJECT_DIR/requirements.txt"

if [ -d "$VENV_DIR" ]; then
  log "复用已有虚拟环境 .venv"
else
  run "$PYTHON_BIN" -m venv "$VENV_DIR"
fi
run "$VENV_PY" -m pip install --upgrade pip
run "$VENV_PY" -m pip install -r "$PROJECT_DIR/requirements.txt"

# ============================================================
step "6/8 前端产物 dist/"
# ============================================================
if [ "$SKIP_DIST" = "1" ]; then
  warn "--skip-dist：跳过。dist/ 要你自己在本地 npm run build 后传上来。"
elif [ -f "$PROJECT_DIR/dist/index.html" ]; then
  log "dist/index.html 已经存在，跳过构建"
elif ! command -v npm >/dev/null 2>&1; then
  warn "没有 npm，跳过后端无关的前端构建。
      做法一：本地 npm ci && npm run build，把 dist/ 整个目录传到 $PROJECT_DIR/
      做法二：宝塔 → 软件商店 → Node 版本管理器 装 Node 18+ 后重跑本脚本"
else
  if [ -f "$PROJECT_DIR/package-lock.json" ]; then
    run npm --prefix "$PROJECT_DIR" ci
  else
    run npm --prefix "$PROJECT_DIR" install
  fi
  run npm --prefix "$PROJECT_DIR" run build
fi

# ============================================================
step "7/8 生成 deploy/baota-env.sh（面板里 source 它就等于配好全部环境变量）"
# ============================================================
ENV_FILE="$SCRIPT_DIR/baota-env.sh"
CHROME_LINES="# Google Chrome 官方 deb 的路径就是后端默认值，不用设 CHROME_BINARY。
# 只有走 Debian 的 chromium 包时才需要打开下面两行：
# export CHROME_BINARY=/usr/bin/chromium
# export CHROMEDRIVER_PATH=/usr/bin/chromedriver"

write_target "$ENV_FILE" <<EOF
# 由 deploy/baota-install.sh 生成（$(date '+%Y-%m-%d %H:%M:%S')）。
# 宝塔「进程守护管理器」的启动命令写成下面这样，就等于把这里的环境变量都带上了：
#   bash -c 'source $ENV_FILE && exec bash $PROJECT_DIR/start-backend.sh'
# 改完记得在面板里重启一次守护进程。
export TZ=Asia/Shanghai
export PORT=$PORT
export SPARK_PYTHON=$PYTHON_BIN

# 发送节奏：计划时间后再随机推迟 0~40 分钟（降低机器特征）
export SPARK_JITTER_MINUTES=40
# 错过当天时间点后 6 小时内补跑
export SPARK_CATCHUP_GRACE_MINUTES=360
# 失败后 45 分钟只对失败好友补发一次
export SPARK_RETRY_AFTER_MINUTES=45
# 面板登录状态空闲多久失效（小时）
export SPARK_TOKEN_TTL_HOURS=72
# 启动时若有任务就自动拉起浏览器（无人值守靠它）
export SPARK_AUTO_INIT_BROWSER=1

$CHROME_LINES
EOF

# ============================================================
step "8/8 常驻守护 + 反向代理"
# ============================================================
SUPERVISOR_CANDIDATES=(
  "/www/server/panel/plugin/supervisor/profile"
  "/www/server/panel/plugin/supervisor/conf.d"
  "/etc/supervisor/conf.d"
)
if [ -n "$SUPERVISOR_DIR" ]; then
  FOUND_SUPERVISOR="$SUPERVISOR_DIR"
else
  FOUND_SUPERVISOR=""
  for d in "${SUPERVISOR_CANDIDATES[@]}"; do
    if [ -d "$d" ]; then FOUND_SUPERVISOR="$d"; break; fi
  done
fi

SUPERVISOR_CONF_BODY() {
  cat <<EOF
[program:spark-web]
directory=$PROJECT_DIR
command=/bin/bash -c 'source $ENV_FILE && exec bash $PROJECT_DIR/start-backend.sh'
user=$RUN_USER
numprocs=1
autostart=true
autorestart=true
startsecs=15
startretries=3
stopwaitsecs=30
stopasgroup=true
killasgroup=true
redirect_stderr=true
stdout_logfile=$PROJECT_DIR/logs/supervisor.out.log
stderr_logfile=$PROJECT_DIR/logs/supervisor.err.log
environment=TZ="Asia/Shanghai"
EOF
}

if [ "$SKIP_SUPERVISOR" = "1" ]; then
  warn "--skip-supervisor：跳过守护配置。"
elif [ -n "$FOUND_SUPERVISOR" ]; then
  log "Supervisor 目录：$FOUND_SUPERVISOR"
  SUPERVISOR_CONF_BODY | write_target "$FOUND_SUPERVISOR/spark-web.conf"
  if [ "$DRY_RUN" != "1" ] && command -v supervisorctl >/dev/null 2>&1; then
    run supervisorctl reread || warn "supervisorctl reread 失败，回面板点一下重启即可。"
    run supervisorctl update || warn "supervisorctl update 失败，回面板点一下重启即可。"
  else
    log "回宝塔 →「进程守护管理器」确认 spark-web 已出现，并勾上开机自启。"
  fi
else
  warn "没找到 Supervisor 的配置目录，改成把配置写到 deploy/generated/ 里，你手动粘贴。"
fi

# 无论如何都留一份参考配置 + nginx 片段，方便面板里贴。
SUPERVISOR_CONF_BODY | write_target "$GENERATED_DIR/spark-web.supervisor.conf"

NGINX_SNIPPET() {
  cat <<EOF
    # >>> spark-web >>>（由 deploy/baota-install.sh 生成，可整段删除）
    # 后端路由是 /Api/... ，前端 axios 的 baseURL 是 /api，
    # 所以必须把 /api 前缀剥掉 —— proxy_pass 结尾那个 "/" 绝对不能省：
    #   /api/Api/Logs -> http://127.0.0.1:$PORT/Api/Logs
    # 少了它就会变成 /api/Api/Logs，后端全部 404。
    location /api/ {
        proxy_pass http://127.0.0.1:$PORT/;
        proxy_http_version 1.1;
        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        # 初始化浏览器 / 扫码登录可能要几十秒，别让 nginx 先超时
        proxy_read_timeout 180s;
        proxy_send_timeout 180s;
    }

    # 免鉴权探活接口，方便宝塔监控或外部拨测
    location = /healthz {
        proxy_pass http://127.0.0.1:$PORT/healthz;
        access_log off;
    }
    # <<< spark-web <<<
EOF
}

NGINX_SNIPPET | write_target "$GENERATED_DIR/spark-web.nginx.conf"

if [ "$SKIP_NGINX" = "1" ] || [ -z "$SITE_CONF" ]; then
  warn "没有给 --site-conf，反向代理这次没写。片段已生成：
      $GENERATED_DIR/spark-web.nginx.conf
      宝塔 → 网站 → 你的站点 → 配置文件，把片段粘进 server { ... } 里保存即可。
      （别用面板的「反向代理」界面代理 /，那会把静态前端也代理到后端。）"
else
  [ -f "$SITE_CONF" ] || [ "$DRY_RUN" = "1" ] || die "找不到站点配置：$SITE_CONF"
  if grep -q '>>> spark-web >>>' "$SITE_CONF" 2>/dev/null; then
    log "$SITE_CONF 里已经有 spark-web 标记，跳过（要重写就先删掉那一段）"
  else
    run cp -a "$SITE_CONF" "$SITE_CONF.bak.$(date +%Y%m%d-%H%M%S)"
    SNIP_FILE="$GENERATED_DIR/spark-web.nginx.conf"
    if [ "$DRY_RUN" = "1" ]; then
      log "（dry-run）会把 $SNIP_FILE 插到 $SITE_CONF 最外层 } 之前"
    else
      awk -v snip="$SNIP_FILE" '
        { line[NR] = $0 }
        END {
          last = 0
          for (i = NR; i >= 1; i--) { if (line[i] ~ /^[[:space:]]*}[[:space:]]*$/) { last = i; break } }
          if (last == 0) { print "站点配置里没找到最外层的 }，请手动粘贴" > "/dev/stderr"; exit 1 }
          for (i = 1; i < last; i++) print line[i]
          while ((getline l < snip) > 0) print l
          close(snip)
          for (i = last; i <= NR; i++) print line[i]
        }' "$SITE_CONF" > "$SITE_CONF.tmp" && mv "$SITE_CONF.tmp" "$SITE_CONF"
      log "已写入反向代理（原文件已备份）"
    fi
    run nginx -t || die "nginx -t 不通过 —— 请用备份还原：$SITE_CONF.bak.*"
    run nginx -s reload || run systemctl reload nginx || true
  fi
fi

# ============================================================
step "验收"
# ============================================================
if [ "$DRY_RUN" = "1" ]; then
  log "dry-run 结束，什么都没改。确认无误后去掉 --dry-run 重跑。"
  exit 0
fi

log "探测后端 http://127.0.0.1:$PORT/healthz （最多等 30 秒）"
ok=0
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:$PORT/healthz" 2>/dev/null | grep -q '"status"'; then ok=1; break; fi
  sleep 1
done
if [ "$ok" = "1" ]; then
  log "后端已经在跑：$(curl -fsS "http://127.0.0.1:$PORT/healthz")"
else
  warn "后端还没起来 —— 正常，如果你还没在面板里启动守护进程。"
fi

log "确认只监听本机（应该是 127.0.0.1:$PORT，不是 0.0.0.0）："
ss -lntp 2>/dev/null | grep ":$PORT" || warn "  端口 $PORT 上还没有监听。"

cat <<EOF

────────────────────────────────────────────────────────────
剩下要你在宝塔面板里点的（脚本代替不了）：

 1. 软件商店 → 安装「进程守护管理器」，确认里面出现了 spark-web，
    勾上「开机自启」；没有的话用 $GENERATED_DIR/spark-web.supervisor.conf 的内容手动添加。
 2. 网站 → 添加站点：根目录 $PROJECT_DIR/dist、纯静态、伪静态选 vue。
 3. 按上面提示把反向代理片段粘进站点配置（没给 --site-conf 的话）。
 4. 网站 → SSL → Let's Encrypt，并打开「强制 HTTPS」。
 5. 安全 → 只放行 80/443，不要放行 $PORT。
 6. 计划任务 → 每天备份 data/、chrome-profile/、logs/（保留 7 天）。
    注意：不需要建「定时发送」的计划任务，后端自带调度。

第一次使用：
 浏览器打开站点 → admin / 123456 登录（然后立刻改密码）
 → 首页「初始化浏览器」→ 设置页扫码登录抖音 → 好友列表建任务 → 预检 + 手动发一条 → 看信息日志。

详细说明：$SCRIPT_DIR/baota-deploy.md
────────────────────────────────────────────────────────────
EOF
