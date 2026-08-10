# ADR-0003：本地 PostgreSQL 供应与测试数据库供应链

- **状态**：accepted
- **日期**：2026-08-10

## 背景

SPEC 28.2 默认使用 Testcontainers 启动 PostgreSQL 18 容器执行集成测试，
CI 环境（GitHub Actions）具有 Docker 支持，该路径无障碍。

但部分本地开发环境（如无 Docker Desktop 的 Windows 主机）无法使用
Testcontainers。SPEC 5.4 明确禁止使用 SQLite 替代 PostgreSQL，因此需要
一条额外的本地回退路径，使开发者无需安装 Docker 即可运行集成测试。

## 决策

测试数据库供应链按以下固定顺序选择（`tests/conftest.py`）：

1. **显式 URL**：环境变量 `APEX_TEST_DATABASE_URL` 指向已有 PostgreSQL 18 实例。
2. **Testcontainers**：Docker 可用时启动 PostgreSQL 18 容器（CI 默认路径）。
3. **本地二进制临时实例**：使用 `scripts/dev_db.py` 下载的 EDB 官方 PostgreSQL 18.x
   免安装二进制，在 pytest 会话内创建临时数据目录和临时端口，会话结束后自动停止和清理。

三级皆不可用时以明确指引失败，禁止回退到 SQLite。

`scripts/dev_db.py` 负责本地供应：
- 下载 EDB 官方 Windows x64 免安装二进制至 `.runtime/`（不入版本控制）。
- `initdb` 初始化数据目录，`pg_ctl` 管理 start/stop/status。
- `ensure` 命令幂等组合下载、初始化与启动；重复执行不重建数据目录。
- 端口避开 5432（默认 55432），认证为 trust 仅限 127.0.0.1。

## 理由

1. **Testcontainers 仍为默认**：CI 使用 Docker，保持与 SPEC 28.2 一致；
   本地供应脚本不进入 CI 流程（SPEC nonGoal）。
2. **免安装二进制避免系统污染**：EDB 官方 zip 解压即用，无需安装服务、
   不修改注册表、不要求管理员权限（`initdb` 本身禁止管理员执行）。
3. **三级回退保证可开发性**：无论开发者使用 Docker 还是裸机环境，
   都能以最小成本获得 PostgreSQL 18 测试实例。
4. **安全边界清晰**：临时实例仅监听 `127.0.0.1`、使用 trust 认证、
   使用非标准端口，不暴露到网络。

## 影响

- `tests/conftest.py` 是测试数据库供应的唯一入口，后续所有集成/API/安全测试
  通过 `database_url` fixture 获取连接串。
- 本地首次运行集成测试前需执行 `uv run python scripts/dev_db.py ensure` 供应二进制。
- `.runtime/` 目录已在 `.gitignore` 中排除，二进制和数据不会进入版本控制。
- 升级 PostgreSQL 18.x 小版本时，更新 `scripts/dev_db.py` 中的
  `DOWNLOAD_URL` 与 `PG_VERSION_STRING` 常量即可。
