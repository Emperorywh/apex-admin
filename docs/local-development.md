# 本地开发指南

> 适用范围：SPEC §30.1（G1 Core Ready）

本文档说明如何在本地环境搭建 Apex Admin 开发环境，包括环境准备、
数据库配置、运行测试和常见问题排查。

---

## 1. 前置要求

| 工具 | 版本要求 | 说明 |
| --- | --- | --- |
| Python | 3.13.x | 项目固定 Python 3.13（`requires-python = ">=3.13,<3.14"`） |
| uv | 最新稳定版 | 包管理与虚拟环境管理 |
| PostgreSQL | 18.x | 运行时数据库（开发环境可选，测试使用 Testcontainers） |
| Docker | 最新稳定版 | 运行 Testcontainers 集成测试（SPEC §28.2） |

---

## 2. 项目设置

### 2.1 克隆并安装依赖

```bash
git clone <repository-url>
cd apex-admin

# 安装依赖（uv 会自动创建虚拟环境并锁定依赖）
uv sync --frozen
```

### 2.2 环境变量

复制环境变量示例文件并填入开发值：

```bash
cp .env.example .env
```

编辑 `.env` 文件，填入以下必需配置：

| 变量 | 说明 | 开发示例 |
| --- | --- | --- |
| `APP_ENV` | 运行环境 | `development` |
| `DATABASE_URL` | PostgreSQL 连接 URL | `postgresql+psycopg://apex:secret@localhost:5432/apex_admin` |
| `ACCESS_TOKEN_HMAC_KEY` | Access Token HMAC 密钥（64 位 hex） | 参见 `.env.example` |
| `REFRESH_TOKEN_HMAC_KEY` | Refresh Token HMAC 密钥（64 位 hex） | 参见 `.env.example` |
| `CONFIG_ENCRYPTION_KEY` | 配置加密密钥（64 位 hex） | 参见 `.env.example` |
| `FILE_STORAGE_ROOT` | 文件存储根目录 | `/tmp/apex-files` |
| `ALLOWED_ORIGINS` | CORS 允许来源（生产环境必需） | `["http://localhost:3000"]` |

密钥要求：
- 64 位十六进制字符串（32 字节，256 bit 熵）。
- 三个密钥彼此独立，不得相同。
- 生产环境拒绝退化密钥（全零等）和开发默认 CORS 来源。

---

## 3. 数据库准备和迁移

### 3.1 创建数据库

```bash
# 使用 psql 或 pgAdmin 创建数据库
createdb apex_admin
```

### 3.2 执行迁移

```bash
uv run alembic upgrade head
```

迁移文件位于 `src/app/infrastructure/database/migrations/versions/`。
每个模块的迁移通过 `down_revision` 链接到全局 head（SPEC §8.2）。

### 3.3 检查迁移状态

```bash
# 查看当前 revision
uv run alembic current

# 查看迁移图 head（应恰好一个）
uv run alembic heads
```

---

## 4. 运行测试

### 4.1 运行全部 G1 测试

```bash
uv run pytest -m g1 -v
```

G1 测试包括单元测试、API 契约测试和集成测试。

### 4.2 只运行单元测试（不依赖 Docker）

```bash
uv run pytest -m "g1 and not integration" -v
```

### 4.3 运行集成测试（需要 Docker）

```bash
uv run pytest -m "g1 and integration" -v
```

集成测试使用 Testcontainers 自动启动 PostgreSQL 18 容器，
测试结束后自动销毁容器数据（SPEC §28.2）。

### 4.4 运行覆盖率检查

```bash
uv run pytest --cov=src/app --cov-branch --cov-fail-under=80 -m g1
```

G1 门槛：语句和分支覆盖率不低于 80%（SPEC §28.1）。

### 4.5 运行静态检查

```bash
# Lint
uv run ruff check .
uv run ruff format --check .

# 类型检查
uv run mypy --strict src

# 架构依赖检查
uv run lint-imports

# 模块注册校验
uv run python -m app.cli modules validate

# 锁文件一致性
uv run uv lock --check
```

---

## 5. 启动开发服务器

### 5.1 使用 uvicorn 启动

```bash
uv run uvicorn app.app:create_app --factory --reload
```

应用默认监听 `http://localhost:8000`。

### 5.2 验证示例端点

```bash
# 存活检查
curl localhost:8000/health/live

# 就绪检查（需要数据库）
curl localhost:8000/health/ready

# 创建示例项目（需要数据库和迁移）
curl -X POST localhost:8000/api/v1/examples \
  -H "Content-Type: application/json" \
  -d '{"name": "hello"}'

# 查询示例项目列表
curl localhost:8000/api/v1/examples
```

### 5.3 交互式文档

开发环境提供 Swagger UI 和 ReDoc：

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
- OpenAPI JSON: `http://localhost:8000/openapi.json`

生产环境自动禁用全部文档端点（SPEC §9.6）。

---

## 6. 常见问题排查

### 6.1 `uv sync` 失败

```
RuntimeError: Failed to lock dependencies
```

确保使用的是项目指定的 Python 3.13 版本。运行 `python --version` 确认。

### 6.2 数据库连接失败

```
OperationalError: connection refused
```

- 确认 PostgreSQL 服务已启动。
- 确认 `DATABASE_URL` 中的主机、端口、用户名和密码正确。
- 确认数据库已创建：`psql -U apex -d apex_admin -c "SELECT 1"`。

### 6.3 Alembic 迁移失败

```
ERROR: Multiple heads are present
```

迁移图存在多个 head revision。检查 `versions/` 目录中的迁移文件，
确保所有迁移通过 `down_revision` 形成单链。

运行 `uv run alembic heads` 查看当前 head 数量（应恰好一个）。

### 6.4 集成测试失败：Docker 不可用

```
DockerException: Error while fetching server API version
```

Testcontainers 依赖 Docker。确保 Docker Desktop 或 Docker Engine 已启动。

不需要 Docker 的验证方式：

```bash
uv run pytest -m "g1 and not integration" -v
```

### 6.5 `lint-imports` 失败

```
Import-linter: Contract 'layered-architecture' broken
```

代码违反了分层依赖规则。检查导入方向：

- API 层禁止导入 Infrastructure 层。
- 低层不得依赖高层。
- Composition Root 是唯一允许同时引用接口和具体实现的位置。

参见 `.importlinter` 配置文件中的分层合约定义。

### 6.6 `modules validate` 失败

```
ModuleRegistrationError: 重复路由 POST /api/v1/examples
```

两个模块声明了相同的路由、权限点或错误码。检查所有模块的
`ModuleDefinition` 声明，确保编码全局唯一。

### 6.7 OpenAPI 快照不一致

```
AssertionError: 生成的 OpenAPI schema 与快照不一致
```

端点变更后需更新快照文件 `tests/fixtures/openapi.json`：

```bash
uv run python -c "
import json
from app.app import create_app
from app.config.settings import AppEnv, Settings
# 使用测试配置生成 schema
settings = Settings(
    _env_file=None,
    app_env=AppEnv.TESTING,
    database_url='postgresql+psycopg://x:x@localhost/x',
    access_token_hmac_key='1' * 64,
    refresh_token_hmac_key='2' * 64,
    config_encryption_key='3' * 64,
    file_storage_root='/tmp/x',
)
app = create_app(settings)
schema = app.openapi()
with open('tests/fixtures/openapi.json', 'w', encoding='utf-8') as f:
    json.dump(schema, f, ensure_ascii=False, indent=2, sort_keys=True)
"
```

---

## 7. CI 流水线

CI 流水线定义在 `.github/workflows/ci.yml`，包含以下步骤：

1. `uv lock --check` — 锁文件一致性
2. `uv sync --frozen` — 依赖安装
3. `ruff check` / `ruff format --check` — Lint 与格式化
4. `mypy --strict src` — 类型检查
5. `lint-imports` — 架构依赖检查
6. `pytest -m g1` — G1 测试（含集成测试，CI 环境原生支持 Docker）
7. `pytest --cov` — 覆盖率检查
8. `pip-audit` — 依赖安全扫描

本地提交前建议运行全部检查，确保 CI 通过。
