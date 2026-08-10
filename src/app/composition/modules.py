"""模块版本目录注册表 — SPEC 5.5 / 8.2.

各业务模块通过 ``ModuleDefinition`` 声明其 Alembic 迁移版本目录，
Composition Root 收集所有已启用模块的 version_locations，
Alembic env.py 从此注册表构建全局单头 revision 图。

当前 G1 阶段无业务模块，注册表为空。初始迁移存放在默认
``alembic/versions/`` 目录。后续 TASK 添加业务模块时，
在此列表中追加模块的版本目录路径。

SPEC 8.2:
  - "所有启用模块必须组成一个全局单头 revision 图"
  - "每个新 revision 的 down_revision 必须指向生成时的全局 head"
"""

from __future__ import annotations

#: 已启用模块的 Alembic 迁移版本目录列表。
#:
#: 每个条目为相对于项目根的路径字符串（如 ``"src/app/modules/user/migrations"``）。
#: env.py 和 migrations.py 从此列表收集 version_locations。
MODULE_VERSION_LOCATIONS: list[str] = []
