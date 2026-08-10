"""事务内事件框架 — SPEC 5.7.

事件只用于解除已确认的模块依赖，不作为普通函数调用的替代品。

SPEC 5.7 关键约束:
  - Domain Event 是不依赖 FastAPI、ORM 和基础设施的不可变对象。
  - 跨模块事件载荷只允许稳定编码、标量值和资源 ID。
  - 需要与业务数据强一致的处理器作为事务内事件处理器，
    在当前 Unit of Work 提交前同步执行。
  - 任一事务内处理器失败时，整个 Use Case 回滚。
  - 事务内处理器不得执行邮件、Webhook、远程 HTTP 调用或其他不可回滚副作用。
  - 事件及处理器通过 ModuleDefinition 显式注册，
    重复事件编码或处理器编码必须使启动和 CI 失败。
  - 多处理器不得依赖执行顺序；稳定排序只用于保证测试和日志可复现。

公开 API:
  - ``DomainEvent``: 不可变领域事件基类
  - ``TransactionalEventHandler``: 事务内事件处理器抽象基类
  - ``TransactionalEventDispatcher``: 事务内事件分发器
"""
