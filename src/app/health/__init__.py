"""健康检查（SPEC §6.2）。

提供存活检查和就绪检查两个端点：
- ``GET /health/live``：只验证进程事件循环可响应，数据库不可用时仍返回 HTTP 200。
- ``GET /health/ready``：通过 provider 接口检查 DB 连接和 Alembic revision 一致性；
  任一条件失败时返回 HTTP 503，恢复后无需重启即可重新就绪。

健康检查返回内容不泄露敏感配置（SPEC §6.2）。
"""

from app.health.providers import DbPoolProvider, ReadinessProbe
from app.health.routes import router

__all__ = ["DbPoolProvider", "ReadinessProbe", "router"]
