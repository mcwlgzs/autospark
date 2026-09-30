# 归档：v2.2 / v3.0 时代的文档

这个目录里的文档**全部已过时**，只作历史记录保留，不要照着做。

它们描述的是本仓库早期那两代架构：

- **v2.2**：多用户 + 数据库（`models.py` / `dal.py` / `sqlite`）
- **v3.0**：Flask 多用户版（`app_v3.py` + JWT + 卡密 + 二维码登录 + `spark.db`）

那一整套代码（`app_v3.py`、`api_user.py`、`api_admin.py`、`api_qrcode.py`、`auth.py`、
`models_user.py`、`dal_user.py`、`init_db_v3.py`、`cookie_monitor.py`、`templates/`、
`frontend/`、`sql/upgrade_v3.0_multi_user.sql`）已经删除，
仓库回到**单用户版本**：只有一个面板账号 `admin`，登录态放在
`chrome-profile/`，任务与记账放在 `data/state.json`，后端入口是 `backend.py`。

所以这些文档里的命令一律**跑不通**，典型如：

| 文档里的写法 | 现在的真实情况 |
| --- | --- |
| `python app_v3.py` / `python init_db_v3.py` | 文件不存在，入口是 `python backend.py` |
| `JWT_SECRET_KEY=...`、`SECRET_KEY=...` | 没有 JWT，会话 token 是后端内存表 + 72 小时空闲过期 |
| `gunicorn -c gunicorn_config.py app_v3:app` | 没有 gunicorn，后端由 uvicorn 自己监听 `127.0.0.1:9844` |
| `sqlite3 spark.db ...`、`pip install alembic sqlalchemy` | 运行期没有任何数据库 |
| `templates/*.html`、`/api/user/login`、`/api/admin/*` | 前端是 Vue（`src/` + `dist/`），后端路由是 `/Api/*`、`/Time/*` |
| `autospark.service`、`deploy.sh`、`verify_system.sh` | 都不存在；systemd 单元是 `deploy/spark-web.service` |

**现在该看哪几份**：

- 本机怎么跑 → [`LOCAL_RUN.md`](../../LOCAL_RUN.md)
- 项目总览 / 配置 / 接口 / 测试 → [`README.md`](../../README.md)
- 服务器部署（含宝塔） → [`deploy/baota-deploy.md`](../../deploy/baota-deploy.md)
  与 [`deploy/README.md`](../../deploy/README.md)
- 忘记面板密码 → `README.md` 的「忘记面板密码怎么办」一节，或直接 `python reset_password.py --show`

（仓库根目录的 `spark.db`、`database_schema.sql`、`sql/`、`migrations/`、
`models.py`、`dal.py`、`dal_async.py`、`models_async.py`、`security.py`、
`monitoring.py`、`browser_manager.py`、`playwright_manager.py`、
`device_fingerprint.py`、`protocol_sender.py`、`migrate_state.py`、
`create_test_db.py`、`upgrade_to_v2.2.py`、`test_v2.2_modules.py`、
`douyin_huohua/` 同属这两代遗留，运行期没有任何入口引用它们。）
