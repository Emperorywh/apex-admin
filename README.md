# Apex Admin

FastAPI 后台管理系统 API 基座 — 模块化单体架构。

## 快速开始

### 环境要求

- **Python 3.13.x**（开发、CI、生产保持同一小版本系列）
- **uv 0.11.x**（包与项目管理工具）
- **PostgreSQL 18.x**（本地开发可选，集成测试由 Testcontainers 自动管理）

### 安装依赖

```bash
uv sync
```

## 本地启动

1. 复制环境变量示例文件并填写真实值：

   ```bash
   cp .env.example .env
   ```

2. 启动 PostgreSQL 18 实例（可通过本地安装、Docker 或远程服务）。

3. 执行数据库迁移：

   ```bash
   uv run alembic upgrade head
   ```

4. 启动开发服务器（工厂模式，支持热重载）：

   ```bash
   uv run uvicorn app.main:create_app --factory --reload
   ```

详细说明见 [本地开发指南](docs/development.md)。

## 环境变量

完整配置项见 [`.env.example`](.env.example)。主要配置：

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `APEX_ENVIRONMENT` | 运行环境（development/testing/production） | development |
| `APEX_DATABASE_URL` | PostgreSQL 异步连接 URL，驱动固定 `postgresql+psycopg://` | `postgresql+psycopg://apex@127.0.0.1:55432/postgres` |
| `APEX_DB_POOL_SIZE` | 连接池大小（每 Worker） | 5 |
| `APEX_DB_MAX_OVERFLOW` | 连接池溢出上限 | 5 |
| `APEX_ACCESS_TOKEN_HMAC_KEY` | Access Token HMAC 密钥 | 开发自动填充，生产必填 |
| `APEX_REFRESH_TOKEN_HMAC_KEY` | Refresh Token HMAC 密钥 | 开发自动填充，生产必填 |
| `APEX_SYSCONFIG_ENCRYPTION_KEY` | 敏感配置加密密钥（Fernet） | 开发自动填充，生产必填 |

> ⚠️ 生产环境禁止使用示例中的默认密钥。

## 数据库准备与迁移

基座固定使用 Alembic 管理结构迁移（SPEC 8.2）。常用命令：

```bash
# 检查数据库连接
uv run python -m app.cli db check

# 执行迁移到最新版本
uv run python -m app.cli db upgrade
```

> 上述 CLI 命令由后续 TASK 实现；当前阶段可使用 `uv run alembic upgrade head`。

## 运行测试

```bash
# 全部测试
uv run pytest

# 按门槛标记运行（SPEC 28）
uv run pytest -m g1

# 查看已注册 marker
uv run pytest --markers

# 覆盖率报告
uv run coverage run -m pytest
uv run coverage json -o .generated/coverage.json
uv run python scripts/check_coverage.py
```

测试必须同时带门槛 marker（`g1`/`g2`/`g3`/`g4`）与类型 marker
（`unit`/`integration`/`api`/`security`/`deployment`），禁止未注册 marker（SPEC 28）。

## 静态检查

```bash
# Ruff 检查与格式校验
uv run ruff check .
uv run ruff format --check .

# mypy strict 类型检查
uv run mypy --strict src

# import-linter 架构契约检查
uv run lint-imports
```

## 常见问题

见 [常见问题排查](docs/development.md#常见问题)。

## 项目结构

```
src/app/
├── main.py          # 应用入口
├── cli/             # 管理命令
├── core/            # 核心基础能力（配置、日志、异常、安全、API 框架、指标）
├── api/             # API 层（应用工厂、中间件、端点）
├── infrastructure/  # 基础设施层（数据库引擎、Unit of Work、迁移）
├── composition/     # 装配根（Composition Root）
└── modules/         # 业务模块根包
    ├── example/     # 最小示例模块
    ├── audit/       # 审计与登录日志
    ├── identity/    # 用户管理
    ├── auth/        # 认证与会话
    ├── rbac/        # 角色与权限
    ├── org/         # 组织（部门、岗位）
    ├── menu/        # 菜单管理
    ├── sysconfig/   # 系统配置
    ├── dict/        # 数据字典
    ├── file/        # 文件管理
    └── backup/      # 备份与恢复
```

## 技术栈

详见 [SPEC 5.4](api/SPEC.md) 和 [架构决策记录](docs/adr/README.md)。

## 版本

当前版本：**v0.1.0（候选）** — 初始基座，覆盖 G1-G4 全部能力。

本地全量验收已通过（G1-G3 测试 + G4 本地子集 + 静态分析全绿）。
Docker 依赖的 G4 验收条目需在 GitHub Actions 确认通过后，方可正式标记"基座完成"。

- [变更日志](CHANGELOG.md)
- [版本策略](docs/versioning-policy.md)
- [验收证据清单](docs/evidence-checklist.md)
- [架构检查报告](docs/architecture-review.md)
- [文档核对表](docs/document-checklist.md)
