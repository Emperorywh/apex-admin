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

4. 启动开发服务器（后续 TASK 提供完整入口）：

   ```bash
   uv run fastapi dev
   ```

详细说明见 [本地开发指南](docs/development.md)。

## 环境变量

完整配置项见 [`.env.example`](.env.example)。主要配置：

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `APP_ENV` | 运行环境（development/testing/production） | development |
| `DATABASE_URL` | PostgreSQL 异步连接 URL | — |
| `DB_POOL_SIZE` | 连接池大小（每 Worker） | 5 |
| `DB_MAX_OVERFLOW` | 连接池溢出上限 | 5 |
| `ACCESS_TOKEN_HMAC_KEY` | Access Token HMAC 密钥 | — |
| `REFRESH_TOKEN_HMAC_KEY` | Refresh Token HMAC 密钥 | — |
| `SECRET_CONFIG_KEY` | 敏感配置加密密钥 | — |

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
├── core/            # 核心基础能力（配置、日志、异常）
├── composition/     # 装配根（Composition Root）
└── modules/         # 业务模块根包
```

## 技术栈

详见 [SPEC 5.4](api/SPEC.md)。
