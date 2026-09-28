# ============================================================
# 抖音火花助手 自托管面板 —— 容器镜像
#
# 设计要点（和直接在宿主机上跑的区别，都写在下面的注释里）：
#   1. 本项目刻意用「可见」Chrome（扫码 / 短信验证要人工过），
#      容器里没有物理显示器，所以必须靠 Xvfb 提供一块虚拟屏幕；
#   2. Chromium 与 selenium 是强耦合的，镜像里的 Chromium 版本跟着
#      Debian 源走，升级基础镜像前请先在本地把浏览器拉起来回归一次；
#   3. 运行期数据（state.json / 日志 / Chrome profile）全部落在 VOLUME 上，
#      否则 docker rm 一下，登录态和发送记账就全没了。
# ============================================================
FROM python:3.11-slim

# PYTHONUNBUFFERED：uvicorn 和 print 的日志不缓冲，docker logs 才能实时看到；
# PYTHONDONTWRITEBYTECODE：容器里不需要 .pyc，免得往挂载目录里写垃圾；
# PORT：backend.py 用 os.getenv('PORT') 决定监听端口。
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=9844 \
    TZ=Asia/Shanghai

# 系统依赖（--no-install-recommends 是必须的，否则会拖进一整套桌面环境）：
#   chromium / chromium-driver  —— 浏览器和驱动。Debian 下可执行文件分别是
#       /usr/bin/chromium 与 /usr/bin/chromedriver（下面用环境变量指过去）。
#   xvfb                        —— 虚拟显示，容器里跑可见 Chrome 的唯一办法。
#   xauth                       —— xvfb-run 靠它生成 X 授权文件。Debian 的 xvfb
#       只是「Recommends」xauth，而下面用了 --no-install-recommends，
#       不显式写上的话 xvfb-run 起得来但客户端会连不上（Authorization required）。
#   fonts-noto-cjk              —— 中文字体。缺了它，页面上的中文全渲染成方框，
#       验证码提示和好友昵称根本没法看。
#   tzdata                      —— 没有它 TZ 环境变量会被忽略，容器停在 UTC：
#       「每天 22:00 发送」会变成北京时间早上 6 点，日志时间也会差 8 小时。
#   curl                        —— 只给下面的 HEALTHCHECK 用。
#   ca-certificates             —— requests 推送通知走 HTTPS 需要根证书。
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      chromium \
      chromium-driver \
      xvfb \
      xauth \
      fonts-noto-cjk \
      tzdata \
      ca-certificates \
      curl \
 && rm -rf /var/lib/apt/lists/*

# backend.py 支持这几个变量（os.getenv），显式指到 Debian 的路径上，
# 这样就不必依赖 selenium-manager 联网去下载匹配的 driver ——
# 容器里经常没有到外网 443 的出口，让它在启动时才去下载必然失败。
ENV CHROME_BINARY=/usr/bin/chromium \
    CHROMEDRIVER_PATH=/usr/bin/chromedriver \
    CHROME_PROFILE_DIR=/app/chrome-profile

WORKDIR /app

# 先只拷 requirements.txt 再装依赖：这一层只随依赖变化而失效。
# 这样一来，改业务代码重建镜像时 pip 那一步直接命中缓存，不用重新下载。
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 再拷代码。这里显式列举而不是 `COPY . .`：
# 镜像里不该出现 src/、node_modules、tests/、deploy/ 这些东西。
# 以后新增 Python 模块时，记得把文件名补进这一行。
COPY backend.py spark_core.py state_store.py notifier.py ./

# 以非 root 运行。Chrome 在容器里当 root 跑需要额外放宽沙箱，
# 与其关闭安全边界，不如直接换一个普通用户（uid 1000 便于和宿主机挂载目录对齐）。
# 同时先把三个数据目录建好并授权，否则首次启动时 Chrome 写 profile 会被拒。
RUN useradd --create-home --shell /bin/bash --uid 1000 spark \
 && mkdir -p /app/data /app/logs /app/chrome-profile \
 && chown -R spark:spark /app
USER spark

# 声明运行期数据目录，配合 docker-compose 的卷映射把状态留在宿主机上：
#   /app/data           state.json（登录态、任务、发送记账），丢了要重新扫码
#   /app/logs           app.log / backend.log / shots/（失败截图）
#   /app/chrome-profile Chrome 用户目录，扫码一次就能长期复用
VOLUME ["/app/data", "/app/logs", "/app/chrome-profile"]

EXPOSE 9844

# /healthz 是 backend.py 提供的免鉴权探活接口（返回 200 JSON）。
# start-period 给得比较长：首次启动要拉 Chrome，比纯 HTTP 服务慢得多。
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/healthz" >/dev/null || exit 1

# 用 xvfb-run 起虚拟显示，再跑后端。
#
# 关于屏幕尺寸：backend.py 里 Chrome 是按 --window-size=1400,3200 启动的，
# 而 xvfb-run 默认只给 1280x1024 —— 窗口比虚拟屏还大时，
# 页面截取（二维码、失败现场截图）有可能被裁掉一块，表现为
# 「截图不完整 / 按钮点不到」。所以这里显式给一块足够大的虚拟屏幕。
#
# 这里直接调 python backend.py 而不是 start-backend.sh：
# 镜像里依赖已经装好了，start-backend.sh 会再建一个 venv 并重新下载一遍依赖，
# 启动慢且在没有外网的机器上直接失败。
#
# 【限制，务必知道】Xvfb 是「看不见」的屏幕。好在扫码/短信验证都可以在
# 面板网页里完成（登录页会直接显示二维码，验证码在设置页提交），
# 所以正常流程不需要看容器里的画面。真要看整块虚拟屏，见 deploy/README.md
# 的「需要人工过验证时怎么看到画面」一节（x11vnc）。
CMD ["xvfb-run", "-a", "-s", "-screen 0 1440x3400x24", "python", "backend.py"]
