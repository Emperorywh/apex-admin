# ADR-0001：固定技术栈基线

- **状态**：accepted
- **日期**：2026-08-10

## 背景

本基座需要一个长期稳定的 Python 后端技术栈，覆盖 Web 框架、ORM、迁移、
密码哈希、包管理、测试、静态检查和依赖安全扫描。SPEC 5.4 要求在项目启动时
固定全部组件的主版本线，并通过 `pyproject.toml` + `uv.lock` 双重锁定，
使开发、CI 和生产环境始终使用经验证的精确版本组合。

固定版本线的目标不是追逐最新发布，而是确保组件间经过 CI 验证可以协同工作。
后续升级主版本或替换组件必须新增 ADR，不允许在业务开发中隐式更换。

## 决策

采用以下技术栈基线（SPEC 5.4）：

| 类别 | 固定选择 |
| --- | --- |
| Python | CPython 3.13.x |
| Web 框架 | FastAPI 0.139.x |
| ASGI Server | Uvicorn（由 `fastapi run` 启动） |
| 数据校验与配置 | Pydantic 2.13.x、pydantic-settings 2.14.x |
| ORM | SQLAlchemy 2.0.x Async ORM |
| PostgreSQL 驱动 | psycopg 3.3.x（URL 用 `postgresql+psycopg`） |
| 数据库与迁移 | PostgreSQL 18.x、Alembic 1.18.x |
| 密码哈希 | argon2-cffi 25.x（Argon2id） |
| 包与项目管理 | uv 0.11.x |
| 测试 | pytest 9.x、pytest-asyncio 1.4.x、HTTPX、Testcontainers |
| 静态检查 | Ruff 0.15.x、mypy 2.3.x strict |
| 架构检查 | import-linter |
| 依赖安全 | pip-audit |
| 模板分发 | Copier 9.17.x |

技术规则：

- 禁止用 SQLite 替代 PostgreSQL 执行集成测试。
- 禁止引入第二套 ORM、迁移、包管理、lint 或类型检查工具。
- 运行依赖、开发依赖和模板依赖分组管理。
- CI 使用 `uv sync --frozen`，禁止临时解析未锁定依赖。

## 理由

1. **单一技术栈**：每个类别只选一个组件，避免团队在多套工具间维护成本
   和认知负担。SPEC 5.4 明确禁止第二套同类工具。
2. **版本线固定**：`pyproject.toml` 声明允许范围，`uv.lock` 锁定精确版本，
   确保全环境一致性。版本线而非精确 pin 给予补丁升级空间，同时避免主版本
   破坏性变更。
3. **uv 替代 pip/poetry/pipenv**：uv 提供更快依赖解析和锁文件管理，
   并内置 Python 版本管理，降低环境搭建摩擦。
4. **Ruff 替代多工具**：Ruff 统一了 lint 与格式化，减少工具链复杂度。
5. **mypy --strict**：类型安全是基座的核心质量门禁之一，strict 模式
   从项目第一天建立零存量的类型基线。
6. **import-linter**：编译期约束分层依赖方向，防止架构腐化。

## 影响

- 所有后续开发必须在此技术栈内进行；引入新组件需先评估兼容性并通过 ADR。
- `uv.lock` 纳入版本控制，CI 以冻结模式同步。
- 升级任何组件的主版本必须重新执行 G1 至当前最高门槛的全部验收（SPEC 5.4）。
- 开发者需熟悉 uv 工作流，而非传统 pip/venv。
