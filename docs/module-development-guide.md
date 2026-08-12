# 新业务模块开发规范

> 对应 SPEC 30.2 — 新业务模块开发规范（G1）。

本指南以 `example` 示例模块为参照，逐条覆盖 SPEC 30.2 的全部条目，指导开发者在基座中新增业务模块。

---

## 目录

1. [模块目录模板](#1-模块目录模板)
2. [如何注册路由](#2-如何注册路由)
3. [如何定义请求和响应 Schema](#3-如何定义请求和响应-schema)
4. [如何组织应用服务和事务](#4-如何组织应用服务和事务)
5. [如何定义权限点](#5-如何定义权限点)
6. [如何注册错误码](#6-如何注册错误码)
7. [如何记录审计日志](#7-如何记录审计日志)
8. [如何编写单元测试和集成测试](#8-如何编写单元测试和集成测试)
9. [模块之间允许的依赖方式](#9-模块之间允许的依赖方式)
10. [最小示例模块](#10-最小示例模块)

---

## 1. 模块目录模板

每个业务模块位于 `src/app/modules/<module_name>/` 目录下，目录名即为模块编码。模块内部按职责划分文件，不按技术文件类型分目录。

以 `example` 模块为例：

```
src/app/modules/example/
├── __init__.py          # 模块包，说明模块用途与删除方式
├── definition.py        # ModuleDefinition — 模块接入契约（SPEC 5.5）
├── models.py            # 领域实体（frozen dataclass，不依赖 ORM）
├── events.py            # 领域事件（DomainEvent 子类）
├── port.py              # Repository Port（Application 层抽象接口）
├── errors.py            # 模块错误码常量与异常类
├── schemas.py           # 请求/响应 Pydantic Schema
├── orm.py               # ORM 模型（继承 Base）
├── adapter.py           # Repository Adapter（Infrastructure 层实现）
├── handler.py           # 事务内事件处理器
├── initializer.py       # 幂等初始化器
├── use_case.py          # Use Case — 应用服务与事务控制
├── router.py            # FastAPI APIRouter
└── migrations/
    └── 0002_example_items.py  # Alembic 迁移版本文件
```

关键约定：
- 模块编码使用小写字母、数字和下划线（如 `example`、`user_management`）。
- 每个文件职责单一，高内聚低耦合（SPEC 5.1）。
- 领域实体（`models.py`）不依赖 FastAPI、ORM 或任何基础设施类型（SPEC 5.2）。

---

## 2. 如何注册路由

路由通过 `ModuleDefinition` 的 `routers` 字段声明。Composition Root 遍历模块清单，自动将所有模块路由挂载到统一 API 前缀（如 `/api/v1`）下。

```python
# src/app/modules/example/definition.py
from app.modules.example.router import router as example_router

MODULE_DEFINITION = ModuleDefinition(
    code="example",
    routers=(example_router,),
    ...
)
```

路由文件定义 `APIRouter`，指定 `prefix` 和 `tags`（SPEC 9.1: 路由按业务模块分组）：

```python
# src/app/modules/example/router.py
router = APIRouter(prefix="/example/items", tags=["example"])

@router.post("", status_code=status.HTTP_201_CREATED, operation_id="create_example_item")
async def create_item(...): ...
```

**规则**：
- `operation_id` 必须全局唯一（SPEC 9.6 / 28.4）。
- 新增模块只需在 `composition/modules.py` 的 `MODULE_MANIFEST` 中增加一项，路由即自动注册（SPEC 5.5）。
- 路由层不得直接访问数据库（SPEC 5.2 / 32）。Router 通过依赖注入获得 Use Case。

---

## 3. 如何定义请求和响应 Schema

请求 Schema 继承 `StrictBaseModel`（`extra="forbid"`），拒绝未知字段（SPEC 9.2）。响应 Schema 继承 `ApiModel`（camelCase 序列化，SPEC 9.3）。

```python
# src/app/modules/example/schemas.py
from app.core.api.schemas import ApiModel, StrictBaseModel

class ExampleItemCreateRequest(StrictBaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)

class ExampleItemResponse(ApiModel):
    id: UUID
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime
```

**规则**：
- 创建（Create）、全量更新（Update PUT）和部分更新（Patch PATCH）请求统一 `extra="forbid"`（SPEC 9.2）。
- JSON 字段使用 `camelCase`（请求、响应和查询参数一致，Python 字段保持 snake_case，由 `ApiModel` 自动转换），时间字段使用带时区的 ISO 8601 字符串（SPEC 9.3）。
- 普通成功响应直接返回资源 Schema，不使用 `{code, message, data}` 成功信封（SPEC 9.3）。
- 创建成功返回 HTTP 201；无响应体的删除返回 HTTP 204（SPEC 9.3）。
- 字符串长度、数值范围、格式和枚举具有 Pydantic 约束。

---

## 4. 如何组织应用服务和事务

Use Case 是最外层写操作入口，控制事务边界（SPEC 5.6）。

```python
# src/app/modules/example/use_case.py
class ExampleItemUseCase:
    def __init__(self, *, uow_factory, clock, id_generator, event_handlers):
        self._uow_factory = uow_factory
        ...

    async def create_item(self, ctx, request):
        dispatcher = TransactionalEventDispatcher(self._event_handlers)
        async with self._uow_factory() as uow:   # 一个 Use Case 方法对应一个 UoW
            repo = SqlAlchemyExampleItemRepository(uow.session)
            # 业务逻辑...
            dispatcher.collect(event)
            await dispatcher.dispatch(uow.session)  # commit 前同步分发
            await uow.commit()                       # 只提交一次
            return result
```

**事务规则**（SPEC 5.6）：
- 一个最外层写 Use Case 对应一个 Unit of Work 和一个 AsyncSession。
- Use Case 负责开始、提交或回滚；Router、Repository 和被调用的模块服务不得提交事务。
- Repository Adapter 由 Composition Root（或 Use Case 内部）使用当前 UoW 的 session 构造。
- 禁止通过 ContextVar 或全局变量隐式获取数据库会话。
- 同一 AsyncSession 不得在并发协程任务间共享。
- 除显式 Savepoint 外禁止嵌套事务。

**事件规则**（SPEC 5.7）：
- 事务内事件处理器在 UoW 提交前同步执行。
- 任一处理器失败时，整个 Use Case 回滚。
- 处理器不得执行邮件、Webhook 等不可回滚副作用。

---

## 5. 如何定义权限点

权限编码使用小写三段或多段形式（SPEC 5.5: `example:item:read`）。权限点在 `ModuleDefinition` 中声明。

```python
PERMISSION_EXAMPLE_ITEM_READ = "example:item:read"
PERMISSION_EXAMPLE_ITEM_WRITE = "example:item:write"

MODULE_DEFINITION = ModuleDefinition(
    code="example",
    permission_codes=(
        PERMISSION_EXAMPLE_ITEM_READ,
        PERMISSION_EXAMPLE_ITEM_WRITE,
    ),
    ...
)
```

**规则**：
- 权限编码全局唯一，重复时应用启动与 CI 失败（SPEC 5.5）。
- 权限校验在服务端执行，不依赖前端隐藏按钮（SPEC 13.3 / 23.5）。
- G1 阶段不实现实际认证与授权逻辑；G2 阶段认证依赖填充后，Router 声明访问所需权限（SPEC 13.3）。

---

## 6. 如何注册错误码

错误码使用 `<MODULE>.<REASON>` 格式，仅大写字母、数字和下划线（SPEC 5.5: `EXAMPLE.NOT_FOUND`）。错误码在模块 `errors.py` 中定义常量、异常类并注册到框架注册表。

```python
# src/app/modules/example/errors.py
from app.core.errors.codes import default_registry
from app.core.errors.exceptions import NotFoundError, ConflictError

EXAMPLE_NOT_FOUND = "EXAMPLE.NOT_FOUND"

class ExampleItemNotFoundError(NotFoundError):
    code = EXAMPLE_NOT_FOUND

# 注册到框架注册表（导入时自动执行）
default_registry.register(
    EXAMPLE_NOT_FOUND, 404,
    meaning="示例条目不存在",
    scenario="按 ID 查询示例条目但未找到时使用",
)
```

同时在 `ModuleDefinition` 中声明，供 `modules validate` 检测全局重复：

```python
MODULE_DEFINITION = ModuleDefinition(
    code="example",
    error_codes=(EXAMPLE_NOT_FOUND, EXAMPLE_CONFLICT),
    ...
)
```

**规则**：
- 错误码全局唯一且稳定（SPEC 10.2）。
- 错误码与展示文案分离——`title`/`detail` 由 API 边界异常处理器动态生成（SPEC 10.2）。
- 异常类继承框架异常层级（`NotFoundError` → `ApplicationError`），覆写 `code` 为模块专属编码。
- API 边界统一将异常转换为 RFC 9457 `application/problem+json` 响应（SPEC 9.3 / 10.1）。

---

## 7. 如何记录审计日志

审计动作在 `ModuleDefinition` 中声明（SPEC 18.2）。

```python
AUDIT_ITEM_CREATE = "example.item.create"
AUDIT_ITEM_UPDATE = "example.item.update"
AUDIT_ITEM_DELETE = "example.item.delete"

MODULE_DEFINITION = ModuleDefinition(
    code="example",
    audit_actions=(AUDIT_ITEM_CREATE, AUDIT_ITEM_UPDATE, AUDIT_ITEM_DELETE),
    protected_resource_types=("example_item",),
    ...
)
```

**审计规则**（SPEC 18.2）：
- 成功操作的核心审计由 Use Case 显式调用审计 Port，与业务事务共同提交（SPEC 5.7）。
- 审计记录包含操作者身份、操作时间、操作模块和动作、目标资源类型和标识、操作结果、Request ID。
- 审计差异使用字段白名单生成，禁止对任意对象执行反射式全字段序列化。
- 敏感字段（密码、Token、密钥）不得进入差异内容。
- 失败操作记录到独立安全日志，不得尝试写入已回滚的业务事务。
- G1 阶段声明审计动作编码供重复检测；实际审计 Port 由 G2 实现。

---

## 8. 如何编写单元测试和集成测试

测试必须同时标记门槛标记（`g1`/`g2`/`g3`/`g4`）和类型标记（`unit`/`integration`/`api`）（SPEC 28）。

### 单元测试（`@pytest.mark.unit`）

不连接网络、数据库或真实文件存储。测试领域规则、错误码、Schema 校验。

```python
@pytest.mark.g1
@pytest.mark.unit
def test_example_item_immutable():
    item = ExampleItem(id=uuid4(), name="test", ...)
    with pytest.raises(FrozenInstanceError):
        item.name = "changed"
```

### 集成测试（`@pytest.mark.integration`）

使用 Testcontainers PostgreSQL 18（`database_url` fixture），测试事务提交/回滚、约束、事件分发。

```python
@pytest.mark.g1
@pytest.mark.integration
async def test_create_commits_once(database_url):
    # 先执行迁移建表
    # 调用 Use Case create_item
    # 验证数据库中恰好一行
    ...
```

### API 契约测试（`@pytest.mark.api`）

使用 TestClient 或 httpx 对真实应用发请求，测试 HTTP 状态码、分页排序、错误结构（RFC 9457）。

```python
@pytest.mark.g1
@pytest.mark.api
def test_create_returns_201(client):
    response = client.post("/api/v1/example/items", json={"name": "test"})
    assert response.status_code == 201
```

**规则**：
- 禁止使用 SQLite 替代 PostgreSQL（SPEC 5.4 / 28.2）。
- 测试不依赖执行顺序（SPEC 28.1）。
- 测试时间、随机值和外部依赖可控（SPEC 28.1）。

---

## 9. 模块之间允许的依赖方式

SPEC 5.1 / 5.2 约定的模块间依赖规则：

| 依赖方向 | 是否允许 | 方式 |
|---|---|---|
| 模块 A → 模块 B 的公开 Application Port | ✅ 允许 | 通过 `ModuleDefinition.application_ports` 声明，通过 `required_dependencies` / `optional_dependencies` 注册依赖关系 |
| 模块 A → 模块 B 的 ORM 模型 | ❌ 禁止 | SPEC 5.1: "禁止跨模块直接操作对方的数据表、ORM 模型和内部函数" |
| 模块 A → 模块 B 的内部函数 | ❌ 禁止 | 只能通过公开 Application Port 或事件协作 |
| 跨模块数据库外键 | ❌ 默认禁止 | SPEC 5.5: 确需使用时必须通过 ADR 指定表所有权、删除语义和迁移顺序 |

**跨模块强一致操作**（SPEC 5.6）：
- 由独立的编排 Use Case 调用各模块公开 Port，共享同一个 Unit of Work。

**跨模块事件协作**（SPEC 5.7）：
- 事件只用于解除已确认的模块依赖，不作为普通函数调用的替代品。
- 跨模块事件载荷只允许稳定编码、标量值和资源 ID。

**模块依赖声明**：
- 在 `ModuleDefinition` 中声明 `required_dependencies` 和 `optional_dependencies`。
- 必需依赖未启用、依赖构成循环时，应用启动与 CI 失败（SPEC 5.5）。

---

## 10. 最小示例模块

`example` 模块是 SPEC 5.5 契约的完整样板，端到端证明以下全部接入：

| 条目 | 示例文件 | 说明 |
|---|---|---|
| Router | `router.py` | FastAPI APIRouter，CRUD 端点，分页排序 |
| Use Case | `use_case.py` | 应用服务，事务控制，事件收集与分发 |
| Port | `port.py` | Repository 抽象接口（Application 层） |
| Adapter | `adapter.py` | SQLAlchemy Repository 实现（Infrastructure 层） |
| 迁移 | `migrations/0002_example_items.py` | Alembic 迁移，down_revision 指向全局 head |
| 权限码 | `definition.py` | `example:item:read`、`example:item:write` |
| 错误码 | `errors.py` | `EXAMPLE.NOT_FOUND`、`EXAMPLE.CONFLICT` |
| 审计动作 | `definition.py` | `example.item.create` 等 |
| 初始化器 | `initializer.py` | 幂等 upsert 演示 |
| 事件 | `events.py` + `handler.py` | `EXAMPLE.ITEM_CREATED` 事件与事务内处理器 |
| 测试 | `tests/test_example_*.py` | 单元、集成、API 三类测试 |

**删除示例模块**：

派生项目可整体删除此模块：
1. 删除 `src/app/modules/example/` 目录。
2. 从 `src/app/composition/modules.py` 的 `MODULE_MANIFEST` 中移除 `EXAMPLE_MODULE` 条目。
3. 重置 Alembic head（删除迁移记录并降级，或重建 revision 图）。
4. 更新 OpenAPI 快照（`tests/snapshots/openapi.json`）。

示例模块不携带任何业务演示数据进入派生项目（SPEC 30.2）。
