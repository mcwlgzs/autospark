# AutoSpark v3.0

## 📁 项目结构

```
autospark/
│
├── app_v3.py                  # Flask应用主入口
├── init_db_v3.py              # 数据库初始化工具
├── requirements.txt           # Python依赖列表
├── .env.example               # 配置文件示例
├── autospark.service          # Systemd服务文件
├── README.md                  # 主文档
│
├── api_user.py                # 用户API接口
├── api_admin.py               # 管理员API接口
├── auth.py                    # 认证授权模块
│
├── models_user.py             # 用户数据模型
├── dal_user.py                # 用户数据访问层
├── dal_async.py               # 异步数据访问层
│
├── protocol_sender.py         # 协议发送模块
├── browser_manager.py         # 浏览器进程管理
├── cookie_monitor.py          # Cookie监控模块
├── device_fingerprint.py      # 设备指纹模块
├── selenium_stealth.py        # Selenium隐身配置
│
├── monitoring.py              # 监控日志模块
│
├── templates/                 # 前端页面
│   ├── index.html             # 首页
│   ├── login.html             # 登录页
│   ├── register.html          # 注册页
│   └── dashboard.html         # 用户中心
│
├── sql/                       # SQL脚本
│   ├── init_database.sql      # 完整初始化脚本
│   └── upgrade_v3.0_multi_user.sql  # v3.0升级脚本
│
├── docs/                      # 文档目录
│   ├── CHANGELOG_V3.0.md      # 更新日志
│   ├── UPGRADE_V3.0.md        # 升级指南
│   ├── QUICKSTART_V3.md       # 快速开始
│   ├── DEPLOYMENT.md          # 部署指南
│   ├── COMPARISON_AND_IMPROVEMENTS.md
│   ├── INTEGRATION_GUIDE.md
│   └── UPGRADE_V2.2.md
│
├── logs/                      # 日志目录
│   └── .gitkeep
│
└── migrations/                # 数据库迁移（可选）
