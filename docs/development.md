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

Swagger UI 文档页（`/docs`）在默认功能之外提供三项增强（SPEC 9.6）：

- **接口搜索** — 页面顶部过滤栏，按模块 tag / 路径 / 摘要过滤接口。
- **单个 API 文档复制** — 每个接口卡片上的"复制文档"按钮，生成该接口的 Markdown 文档（参数表、请求体示例、响应说明）并复制到剪贴板。
- **全局参数设置** — 顶栏"⚙ 全局参数"按钮，维护全局 header / query 参数，所有 "Try it out" 请求自动携带，配置保存在浏览器 localStorage。

文档页从 CDN 加载 swagger-ui-dist 静态资源，受限网络可通过环境变量 `APEX_SWAGGER_CDN_BASE` 切换源（默认 jsdelivr）。

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

### 4.1 数据初始化 — 开发与生产分离（SPEC 8.5）

开发演示数据和生产初始化数据使用不同命令与数据源，禁止混用。

#### 生产初始化命令

| 命令 | 用途 | 数据源 |
| --- | --- | --- |
| `uv run python -m app.cli db upgrade` | 执行数据库结构迁移，不创建业务数据（SPEC 25.1） | Alembic 迁移脚本 |
| `uv run python -m app.cli auth create-admin --username <用户名>` | 安全创建首个管理员（密码经标准输入传入） | 运维交互输入 |
| `uv run python -m app.cli auth sync-permissions` | 幂等同步各模块声明的权限点到权限目录 | 各模块 ModuleDefinition 声明 |
| `uv run python -m app.cli auth rotate-token-keys` | 生成 Token HMAC 密钥轮换配置 | CSPRNG 随机生成 |
| `uv run python -m app.cli admin sync-seeds` | 幂等同步基础菜单和字典种子（SPEC 25.3） | 各模块种子初始化器声明 |

生产环境部署流程：
1. `db upgrade` — 执行迁移
2. `auth sync-permissions` — 同步权限点目录
3. `admin sync-seeds` — 同步基础菜单与字典种子
4. `auth create-admin` — 创建首个管理员
5. （可选）`auth rotate-token-keys` — 轮换 Token 密钥

#### 后台管理命令（SPEC 25.3）

| 命令 | 用途 | 说明 |
| --- | --- | --- |
| `uv run python -m app.cli admin sync-seeds` | 幂等同步基础菜单和字典种子 | 连续执行不产生重复编码 |
| `uv run python -m app.cli data check` | 检查数据完整性 | 健康库退出码 0；发现问题退出码非 0 |
| `uv run python -m app.cli files reconcile --dry-run` | 只报告文件状态和物理文件不一致 | 默认行为 |
| `uv run python -m app.cli files reconcile --apply` | 按确定性规则恢复或标记文件 | 记录审计日志 |
| `uv run python -m app.cli audit cleanup --dry-run` | 只报告将清理的日志 | 默认行为 |
| `uv run python -m app.cli audit cleanup --apply` | 执行日志保留清理 | 记录执行结果 |

#### 修复类命令 dry-run 约定（SPEC 25.3）

SPEC 25.3: "所有修复命令默认 dry-run；实际修改必须使用显式 `--apply`
并记录审计或运维日志"。

此约定统一适用于全部修复类命令:

- **默认 dry-run**: 不带 `--apply` 标志时，命令只报告将执行的操作，
  不修改任何数据。
- **显式 `--apply`**: 只有显式传入 `--apply` 时才执行实际修改。
- **审计/运维日志**: `--apply` 执行修改后，操作结果写入审计日志
  （如 `audit cleanup --apply` 记录清理结果）或运维日志
  （如 `files reconcile --apply` 记录恢复结果）。

当前遵循此约定的修复类命令:

| 命令 | 默认行为 | `--apply` 行为 | 日志 |
| --- | --- | --- | --- |
| `files reconcile` | 报告不一致 | 执行恢复/标记 | 审计日志（file.reconcile） |
| `audit cleanup` | 报告将清理的日志 | 执行删除 | 记录执行结果 |

> 注: `admin sync-seeds` 和 `auth sync-permissions` 是同步命令而非修复命令，
> 其正常操作即为幂等 upsert（新增和更新），不适用 dry-run 约定。

#### 开发演示数据命令

| 命令 | 用途 | 限制 |
| --- | --- | --- |
| `uv run python -m app.cli dev seed-demo` | 创建开发演示管理员和权限数据 | 仅限非生产环境（ENVIRONMENT != production） |

`dev seed-demo` 命令在生产环境下拒绝执行。它创建的演示数据（如 `demo-admin` 用户）
仅供本地开发调试使用，不进入生产环境。

#### 密码安全（SPEC 23.2 / 25.2）

- `auth create-admin` 的初始密码只能通过受控标准输入传入，不接受命令行参数。
- 密码不写入日志、不输出到命令行、不进入命令历史。
- 密码使用 Argon2id 哈希后存储，明文密码不持久化。

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
