# 本地开发指南

> SPEC 30.1 — 本地开发说明。覆盖本地启动、环境变量、数据库准备与迁移、测试运行和常见问题排查。

## 1. 环境准备

### 1.1 系统要求

| 组件 | 版本 | 说明 |
| --- | --- | --- |
| Python | 3.13.x | 开发、CI、生产保持同一小版本系列 |
| uv | 0.11.x | 包与项目管理（替代 pip / poetry / pipenv） |
| PostgreSQL | 18.x | 数据库；集成测试由 Testcontainers 自动管理 |

### 1.2 安装 uv

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 1.3 安装依赖

```bash
uv sync
```

此命令按 `uv.lock` 锁定的精确版本创建虚拟环境并安装全部依赖（含开发依赖）。

## 2. 本地启动

### 2.1 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入真实的数据库地址和密钥
```

### 2.2 启动数据库

本地开发支持两种方式启动 PostgreSQL 18，按环境选择其一。

#### 方式一：Docker（推荐，需要 Docker Desktop）

```bash
docker run -d --name apex-pg \
  -e POSTGRES_USER=apex \
  -e POSTGRES_PASSWORD=apex_password \
  -e POSTGRES_DB=apex \
  -p 5432:5432 \
  postgres:18
```

#### 方式二：本地供应脚本（无 Docker 环境）

对于无法安装 Docker 的 Windows 开发环境，使用内置供应脚本
（ADR-0003）下载 EDB 官方免安装二进制：

```bash
# 幂等完成下载、初始化与启动（首次约需下载 ~358MB）
uv run python scripts/dev_db.py ensure

# 查看运行状态与连接串
uv run python scripts/dev_db.py status

# 停止服务
uv run python scripts/dev_db.py stop
```

该方式使用端口 55432（避开 5432），仅监听 127.0.0.1，认证为 trust。
二进制和数据目录位于 `.runtime/`，不进入版本控制。

### 2.3 执行迁移

```bash
uv run alembic upgrade head
```

> 后续 TASK 提供统一 CLI：`uv run python -m app.cli db upgrade`。

### 2.4 启动开发服务器

```bash
uv run fastapi dev
```

开发服务器默认监听 `http://localhost:8000`，提供自动重载和 OpenAPI 文档（`/docs`）。

## 3. 环境变量

完整配置项见仓库根目录的 `.env.example`。

### 关键配置说明

- **`APP_ENV`**：运行环境，影响配置加载和日志行为。生产环境必须设为 `production`。
- **`DATABASE_URL`**：PostgreSQL 异步连接 URL，格式固定为 `postgresql+psycopg://user:password@host:port/dbname`。
- **`DB_POOL_SIZE` / `DB_MAX_OVERFLOW`**：连接池参数。默认 API Worker 数量为 2，每 Worker `pool_size=5`、`max_overflow=5`，峰值 20 个连接（SPEC 26.1）。
- **`ACCESS_TOKEN_HMAC_KEY` / `REFRESH_TOKEN_HMAC_KEY`**：Token 摘要密钥，必须彼此不同且各至少 256 bit 熵（SPEC 12.2）。

> ⚠️ 生产环境禁止使用 `.env.example` 中的占位值。密钥不得提交到版本控制（SPEC 23.2）。

## 4. 数据库迁移

基座固定使用 Alembic 管理结构变更（SPEC 8.2）。

### 常用命令

```bash
# 迁移到最新版本
uv run alembic upgrade head

# 回滚一个版本
uv run alembic downgrade -1

# 查看当前版本
uv run alembic current

# 查看 head
uv run alembic heads
```

### 迁移规则

- 所有表结构变更必须通过迁移文件交付，禁止手动修改数据库结构。
- 所有启用模块共同组成唯一 Alembic head（SPEC 5.5）。
- CI 从空数据库执行 `alembic upgrade head`（SPEC 8.2）。
- 不要求提供自动 downgrade；不可逆迁移必须在文件中说明恢复方式。

## 5. 运行测试

### 5.1 全部测试

```bash
uv run pytest
```

### 5.2 按标记运行（SPEC 28）

```bash
# 门槛标记
uv run pytest -m g1          # G1 Core Ready
uv run pytest -m "g1 or g2"  # G1 + G2

# 类型标记
uv run pytest -m unit         # 单元测试
uv run pytest -m integration  # 集成测试（需要 Docker 运行 Testcontainers）
```

### 5.3 覆盖率（SPEC 28.1）

```bash
uv run coverage run -m pytest
uv run coverage json -o .generated/coverage.json
uv run python scripts/check_coverage.py
```

覆盖率门槛：语句覆盖率不低于 85%，分支覆盖率不低于 80%。后续门槛模块有更高要求。

### 5.4 测试标记规则

每条测试必须同时带门槛 marker 与类型 marker：

```python
@pytest.mark.g1
@pytest.mark.unit
def test_example():
    ...
```

禁止未注册的临时 marker。注册的 9 个 marker：

| 标记 | 说明 |
| --- | --- |
| `g1` / `g2` / `g3` / `g4` | 完成门槛 |
| `unit` | 单元测试 |
| `integration` | 数据库集成测试 |
| `api` | API 契约测试 |
| `security` | 安全测试 |
| `deployment` | 部署与恢复测试 |

### 5.5 集成测试前置条件

集成测试通过三级供应链（ADR-0003）自动获取 PostgreSQL 18 实例：

1. 环境变量 `APEX_TEST_DATABASE_URL` 指向已有实例时直接使用。
2. Docker 可用时使用 Testcontainers 自动启动 PostgreSQL 18 容器。
3. 无 Docker 时回退到本地供应脚本下载的免安装二进制（需先执行
   `uv run python scripts/dev_db.py ensure`）。

三级皆不可用时测试将以明确指引失败，不回退到 SQLite。

## 6. 静态检查

```bash
# Ruff 静态检查
uv run ruff check .

# Ruff 格式校验
uv run ruff format --check .

# mypy strict
uv run mypy --strict src

# import-linter 架构契约
uv run lint-imports
```

## 7. 架构契约

import-linter 契约（SPEC 5.2）：

- **分层依赖方向**：`api → application → domain`，`infrastructure` 只实现内层 Port，`composition` 可引用全部。
- **模块间隔离**：禁止跨模块直接导入对方内部实现。

## 8. 架构决策记录

所有具有持久影响的技术与架构决策记录在 `docs/adr/` 目录（SPEC 29.2）。
升级主版本、替换组件或变更安全参数时必须新增 ADR。详见
[ADR 说明](adr/README.md)。

## 9. 常见问题

### 9.1 `uv sync` 报版本不匹配

确保 `.python-version` 文件存在且值为 `3.13`，uv 会自动安装所需 Python 版本：

```bash
uv python install 3.13
```

### 9.2 集成测试报 Docker 连接失败

Testcontainers 需要 Docker 运行。检查 Docker Desktop 或 Docker 守护进程是否已启动。

### 9.3 `alembic heads` 输出多个 head

SPEC 5.5 要求全局唯一 head。检查是否存在多个迁移文件的 `down_revision` 未指向当前 head。

### 9.4 mypy 报找不到模块

确保使用 `uv run` 前缀运行，它会正确设置 `PYTHONPATH` 指向 `src/` 目录。

### 9.5 pip-audit 报已知漏洞

```bash
uv run pip-audit
```

根据报告升级受影响依赖到修复版本，升级后重新执行全部验收（SPEC 5.4）。
