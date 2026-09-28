# Local run

This fork uses visible local Chrome automation so SMS verification after Douyin QR login can be completed on the same machine.

## Requirements

- Python 3.10+
- Node.js 18+
- Google Chrome
- Chromedriver matching your Chrome version (Selenium 4.6+ 会自动下载；也可用 `CHROMEDRIVER_PATH` 指定)

Optional environment variables (完整列表见 README)：

```bash
CHROME_BINARY=/path/to/chrome
CHROMEDRIVER_PATH=/path/to/chromedriver
CHROME_PROFILE_DIR=./chrome-profile
PORT=9844
SPARK_PYTHON=python3          # 只给 start-backend.sh 用：指定解释器（宝塔/aaPanel 装的 Python 不在 PATH 里时用绝对路径）
SPARK_SHOW_BROWSER=1          # 0 = headless，仅适合登录态已就绪的场景
SPARK_AUTO_INIT_BROWSER=1     # 启动时若已有定时任务就自动拉起浏览器
SPARK_JITTER_MINUTES=40       # 在设定时间后随机推迟 0~40 分钟
SPARK_CATCHUP_GRACE_MINUTES=360  # 错过当天时间点后 6 小时内补跑
SPARK_RETRY_AFTER_MINUTES=45  # 失败后 45 分钟只补发失败的好友
SPARK_TOKEN_TTL_HOURS=72      # 面板登录状态空闲过期
```

## Windows

```powershell
.\start-backend.ps1
npm install
npm run dev
```

Open `http://localhost:5173`.

### 两个常见坑（脚本已经会检测并给出中文提示）

1. **必须真的装了 Python。** 只有 Microsoft Store 的 `python.exe` 占位别名时，
   `python -c "..."` 的退出码是 9009，脚本会直接停下来告诉你去 python.org 装 3.10+ 并勾选
   “Add python.exe to PATH”。
2. **`.venv` 不能是 Linux 结构。** 从 Linux 服务器把项目拷到 Windows 时，
   `.venv/` 里只有 `bin/`、`lib/`，没有 `Scripts/`，Windows 下用不了。
   脚本检测到这种情况会让你先删掉 `.venv` 再重跑（会重新创建 Windows 版虚拟环境）。

## Linux

```bash
chmod +x ./start-backend.sh
./start-backend.sh
npm install
npm run dev
```

Open `http://localhost:5173`.

If you run this on a Linux server, make sure Chrome has a usable display session.
This fork uses visible Chrome, so you need an existing desktop/X11 session or Xvfb.
脚本在检测不到 `$DISPLAY` 时会自动用 `xvfb-run -a` 启动；没有 `xvfb-run` 会给出安装提示。
后端也会尝试常见的 `:91`、`:99`、`:1`。

系统服务化部署（开机自启）见 `deploy/README.md`；容器部署见 `docker-compose.yml`。

## 测试

不装任何第三方依赖也能跑（纯逻辑层只用标准库）：

```bash
python -m unittest discover -s tests -t . -v
```

有开发依赖时还可以：

```bash
pip install -r requirements-dev.txt
python -m pytest -q
ruff check .
```

## 首次使用建议顺序

1. 打开面板 → 首页「初始化浏览器」；
2. 设置页完成扫码登录（需要二次验证时按提示做短信/刷脸/密码验证）；
3. 好友列表刷新 → 勾选要续火花的好友 → 批量创建定时任务（时间可留默认）；
4. 用「发送预检」确认能打开会话，再手动发一条；
5. 到「信息日志」确认没有异常，然后就不用管了 —— 每天会按随机窗口自动发送。

Default admin login:

```text
admin / 123456
```

**第一次登录后请立刻改掉这个密码**（设置页 → 修改密码，至少 8 位且不能是纯数字）。
