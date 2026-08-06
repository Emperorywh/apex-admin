"""应用端口定义（SPEC §5.6、§5.8）。

端口（Port）是 Application 和 Domain 内层定义的抽象接口，Infrastructure 层负责具体实现。
本包定义领域和应用层所需的通用端口：
- Clock Port：获取当前 UTC 时间
- ID Generator Port：生成唯一标识
- Unit of Work Port：事务生命周期管理

端口只声明接口契约，不包含任何实现逻辑或具体技术依赖。
"""

from app.ports.clock import Clock
from app.ports.id_generator import IdGenerator
from app.ports.unit_of_work import UnitOfWork

__all__ = ["Clock", "IdGenerator", "UnitOfWork"]
