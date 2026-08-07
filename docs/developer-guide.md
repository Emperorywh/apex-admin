# 开发者指南：新业务模块开发规范

> 适用范围：SPEC §30.2（G1 Core Ready）

本指南说明如何在 Apex Admin 基座中新增业务模块。以 `src/app/modules/example/`
目录下的最小示例模块为参考模板，涵盖目录结构、路由注册、Schema 定义、
服务/事务组织、权限点、错误码、审计、事件和测试。

---

## 1. 模块目录模板

每个业务模块位于 `src/app/modules/<module_name>/` 下，遵循分层架构：

```
src/app/modules/<module_name>/
├── __init__.py              # 包说明
├── definition.py            # ModuleDefinition（唯一公开声明）
├── routes.py                # FastAPI Router（API 层）
├── application/
│   ├── __init__.py
│   ├── port.py              # Application Port + Repository Port + UoW Port
│   ├── schemas.py           # 请求/响应 Schema
│   └── service.py           # Use Case / Application Service
├── domain/
│   ├── __init__.py
│   ├── model.py             # 领域实体和值对象
│   ├── policy.py            # 领域策略（业务校验）
│   └── events.py            # 领域事件
└── infrastructure/
    ├── __init__.py
    ├── models.py            # SQLAlchemy ORM 模型
    ├── repository.py        # Repository Adapter
    ├── unit_of_work.py      # 模块 UoW 实现
    ├── event_handlers.py    # 事务内事件处理器
    └── wiring.py            # 服务装配工厂
```

### 分层依赖方向

```
Router → Use Case → Domain Policy → Application Port → Infrastructure Adapter
```

- **Router** 只获得 Use Case，不得获得 UoW、AsyncSession 或提交接口。
- **Use Case** 在 `async with uow:` 上下文中编排领域策略、持久化和事件。
- **Domain Policy** 是纯 Python 校验器，不依赖应用层异常或基础设施。
- **Application Port** 定义接口，Infrastructure 层实现。
- **禁止跨模块直接操作对方的数据表、ORM 模型和内部函数。**

---

## 2. 如何注册路由

1. 在 `routes.py` 中创建 `APIRouter`，设置 `prefix` 和 `tags`：

```python
from fastapi import APIRouter

router = APIRouter(prefix="/examples", tags=["examples"])

@router.post("", summary="创建示例", status_code=201)
async def create_example(...):
    ...
```

2. 在 `definition.py` 的 `ModuleDefinition` 中声明 Router：

```python
MODULE = ModuleDefinition(
    code="example",
    ...
    routers=(router,),
    ...
)
```

3. 在 `composition_root.py` 的 `ENABLED_MODULES` 列表中增加模块：

```python
from app.modules.example.definition import MODULE as EXAMPLE_MODULE

ENABLED_MODULES = [EXAMPLE_MODULE]
```

应用工厂 `create_app` 会自动遍历 `ENABLED_MODULES`，将每个模块的 Router
挂载到 `/api/v1` 前缀下。

---

## 3. 定义请求和响应 Schema

继承共享基类（SPEC §9.2、§9.3）：

```python
from app.api.schemas import BaseRequestModel, BaseResponseModel

class CreateExampleRequest(BaseRequestModel):
    """创建请求——extra="forbid" 拒绝未知字段。"""
    name: str

class ExampleResponse(BaseResponseModel):
    """响应——snake_case，时间使用 ISO 8601。"""
    id: UUID
    name: str
    created_at: datetime
```

约定：
- 创建/更新请求 Schema 继承 `BaseRequestModel`（`extra="forbid"`）。
- 响应 Schema 继承 `BaseResponseModel`（`extra="forbid"`）。
- 字段名使用 `snake_case`。
- 时间字段使用带时区的 `datetime`（UTC）。
- 普通成功响应直接返回资源 Schema，不使用 `{code, message, data}` 信封。

---

## 4. 组织应用服务和事务

每个最外层写 Use Case 对应一个 Unit of Work 和一个 AsyncSession（SPEC §5.6）：

```python
class ExampleService(ExampleApplicationPort):
    def __init__(
        self,
        uow_factory: Callable[[], ExampleUnitOfWork],
        event_dispatcher: TransactionalEventDispatcher,
    ) -> None:
        ...

    async def create_item(self, *, name: str, current_time: datetime) -> ExampleItem:
        async with self._uow_factory() as uow:
            # 1. 领域策略校验
            ExampleNamePolicy.validate(name)
            # 2. 创建领域实体
            item = ExampleItem.new(name=name, created_at=current_time)
            # 3. 通过 Repository 持久化
            await uow.examples.add(item)
            # 4. 收集并调度领域事件（提交前同步执行）
            self._event_dispatcher.collect(ExampleItemCreated(...))
            await self._event_dispatcher.flush(uow)
            return item
        # 5. UoW 退出时统一提交（无异常）或回滚（有异常）
```

关键约束：
- Use Case 在 `async with uow:` 上下文中执行数据操作。
- 退出时由 UoW 统一提交或回滚，不在 Use Case 中手动 commit。
- 并发任务必须各自创建独立的 UoW，不得共享 AsyncSession。

### 模块 UoW 模式

模块定义自己的 UoW 端口，扩展基础 `UnitOfWork` 并增加 Repository 访问：

```python
class ExampleUnitOfWork(UnitOfWork):
    @property
    @abstractmethod
    def examples(self) -> ExampleRepository: ...
```

Infrastructure 层通过多重继承实现：

```python
class SqlAlchemyExampleUnitOfWork(SqlAlchemyUnitOfWork, ExampleUnitOfWork):
    @property
    def examples(self) -> SqlAlchemyExampleRepository:
        return SqlAlchemyExampleRepository(self.session)
```

---

## 5. 定义权限点

在 `definition.py` 的 `ModuleDefinition.permission_points` 中声明：

```python
permission_points=frozenset({
    PermissionPoint(
        code="example:item:create",
        description="创建示例项目",
    ),
}),
```

权限编码固定为小写三段或多段形式（例如 `module:resource:action`），
全局唯一，重复时应用启动和 CI 必须失败。

---

## 6. 注册错误码

在 `definition.py` 的 `ModuleDefinition.error_codes` 中声明：

```python
error_codes=frozenset({
    ErrorCode(
        code="EXAMPLE.NOT_FOUND",
        http_status=404,
        description="示例项目不存在",
    ),
}),
```

错误码固定为 `<MODULE>.<REASON>` 格式，只允许大写字母、数字和下划线。
客户端业务判断只能使用错误码，不得依赖展示文案。

在 Use Case 中使用：

```python
from app.errors import ParameterError

raise ParameterError("名称不合规", code="EXAMPLE.INVALID_NAME")
```

---

## 7. 记录审计

在 `definition.py` 的 `ModuleDefinition.audit_actions` 中声明审计动作：

```python
audit_actions=frozenset({
    AuditAction(
        code="example.item.create",
        description="创建示例项目",
    ),
}),
```

审计日志在 G2 阶段由审计模块实现。G1 阶段只需声明审计动作编码，
确保全局唯一。审计日志不通过普通业务 CRUD 修改（SPEC §18.2）。

---

## 8. 定义领域事件

1. 在 `domain/events.py` 中定义事件类：

```python
@dataclass(frozen=True)
class ExampleItemCreated(DomainEvent):
    code: ClassVar[str] = "example.item.created"
    item_id: UUID
    name: str
```

2. 在 `definition.py` 中声明事件和事件处理器：

```python
events=frozenset({
    EventDefinition(code="example.item.created", description="示例项目创建事件"),
}),
event_handlers=frozenset({
    EventHandlerDefinition(
        code="example.handler.item_created",
        event_code="example.item.created",
        description="记录示例项目创建事件",
        transactional=True,
    ),
}),
```

3. 在 `infrastructure/event_handlers.py` 中实现处理器：

```python
async def handle_example_item_created(event: DomainEvent, uow: UnitOfWork) -> None:
    if not isinstance(event, ExampleItemCreated):
        return
    _logger.info("示例项目已创建", ...)
```

4. 在 `infrastructure/wiring.py` 中注册处理器实现：

```python
handler_implementations = {
    "example.handler.item_created": handle_example_item_created,
}
```

事务内处理器在 UoW 提交前同步执行，任一失败导致整个 Use Case 回滚。
不得在事务内处理器中执行邮件、Webhook 等不可回滚副作用（SPEC §5.7）。

---

## 9. 编写测试

### 单元测试

测试领域逻辑，不依赖数据库：

```python
@pytest.mark.unit
@pytest.mark.g1
class TestExampleNamePolicy:
    def test_empty_raises(self):
        with pytest.raises(ValueError):
            ExampleNamePolicy.validate("")
```

使用内存 Fake UoW 和 Repository 测试 Use Case 编排逻辑。

### 集成测试

使用 Testcontainers PostgreSQL 18 验证完整数据流：

```python
@pytest.mark.integration
@pytest.mark.g1
class TestExampleModuleIntegration:
    async def test_create_and_list_item(self, example_engine, clean_examples):
        service = create_example_service(example_engine)
        item = await service.create_item(name="test", current_time=...)
        items, total = await service.list_items(page=1, page_size=20)
        assert total == 1
```

---

## 10. 模块依赖规则

- **禁止跨模块直接操作对方的数据表、ORM 模型和内部函数。**
- 跨模块调用只能通过公开的 Application Port 或事件协作。
- 模块间依赖通过 `ModuleDefinition.required_dependencies` 和
  `optional_dependencies` 显式声明。
- 必需依赖未启用时启动失败；可选依赖未启用时对应能力整体关闭。

### Alembic 迁移

在 `src/app/infrastructure/database/migrations/versions/` 目录中新增迁移文件，
`down_revision` 指向当前全局 head。迁移文件手写 DDL，不使用 autogenerate
（SPEC §8.2）。

```python
revision = "0002"
down_revision = "0001"

def upgrade():
    op.create_table("example_items", ...)
```

### import-linter 分层合约

代码必须通过 `uv run lint-imports`。分层依赖方向：

```
API → Application → Domain → Port
Infrastructure → Application / Domain Port
Composition Root → API + Application + Infrastructure
```

---

## 11. 模块定义完整示例

参见 `src/app/modules/example/definition.py`——它声明了模块编码、
Application Port、Router、权限点、错误码、审计动作、资源类型、事件、
事件处理器、管理命令和迁移版本目录。
