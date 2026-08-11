"""Composition Root 模块清单 — SPEC 5.5.

Composition Root 是唯一同时引用接口与实现的装配位置
（SPEC 5.2）。模块清单显式列出所有已启用模块的 ``ModuleDefinition``，
应用启动时由此清单进行全量校验
（SPEC 5.5: "由 Composition Root 中的显式模块清单装配"）。

SPEC 5.5:
  - 禁止通过扫描包、导入副作用或命名约定自动发现模块。
  - 新增模块只允许新增模块自身代码，并在 Composition Root 的模块
    清单中增加一项；不得修改核心模块内部实现。
  - Alembic 可以按模块存放版本文件，但所有启用模块必须组成一个
    全局单头 revision 图。

``MODULE_VERSION_LOCATIONS`` 从清单中派生，供 ``alembic/env.py``
和 ``migrations.py`` 使用。新增模块时在 ``MODULE_MANIFEST`` 中追加对应条目。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.modules.audit.definition import MODULE_DEFINITION as AUDIT_MODULE
from app.modules.auth.definition import MODULE_DEFINITION as AUTH_MODULE
from app.modules.dict.definition import MODULE_DEFINITION as DICT_MODULE
from app.modules.example.definition import MODULE_DEFINITION as EXAMPLE_MODULE
from app.modules.file.definition import MODULE_DEFINITION as FILE_MODULE
from app.modules.identity.definition import MODULE_DEFINITION as IDENTITY_MODULE
from app.modules.menu.definition import MODULE_DEFINITION as MENU_MODULE
from app.modules.org.definition import MODULE_DEFINITION as ORG_MODULE
from app.modules.rbac.definition import MODULE_DEFINITION as RBAC_MODULE
from app.modules.sysconfig.definition import MODULE_DEFINITION as SYSCONFIG_MODULE

if TYPE_CHECKING:
    from app.core.modules.definition import ModuleDefinition

#: 显式模块清单 — 所有已启用模块的 ModuleDefinition 列表.
#:
#: SPEC 5.5: "由 Composition Root 中的显式模块清单装配。
#: 禁止通过扫描包、导入副作用或命名约定自动发现模块"。
#:
#: 新增模块时在此列表追加一项，同时新增模块自身代码。
#: 当前已注册:
#:   - example（最小示例模块，SPEC 30.2 / 34.1）
#:   - audit（审计与登录日志模块，SPEC 18.1 / 18.2）
#:   - identity（用户模块，SPEC 11.1 / 11.2 / 11.3）
#:   - auth（认证模块，SPEC 12.1 / 12.3 / 12.4 / 18.1）
#:   - rbac（RBAC 角色与权限点模块，SPEC 13.1 / 13.2 / 25.2）
#:   - org（组织模块——部门管理，SPEC 14.1）
#:   - menu（菜单管理模块，SPEC 15.1 / 15.2）
#:   - sysconfig（系统配置模块，SPEC 16.1 / 16.2）
#:   - dict（数据字典模块，SPEC 17.1 / 17.2）
#:   - file（文件管理模块，SPEC 19.1 / 19.2 / 19.3 / 19.4）
MODULE_MANIFEST: list[ModuleDefinition] = [
    EXAMPLE_MODULE,
    AUDIT_MODULE,
    IDENTITY_MODULE,
    AUTH_MODULE,
    RBAC_MODULE,
    ORG_MODULE,
    MENU_MODULE,
    SYSCONFIG_MODULE,
    DICT_MODULE,
    FILE_MODULE,
]

#: 已启用模块的 Alembic 迁移版本目录列表.
#:
#: 从 ``MODULE_MANIFEST`` 派生。env.py 和 migrations.py 从此列表
#: 收集 version_locations（SPEC 5.5 / 8.2）。
#:
#: 每个条目为相对于项目根的路径字符串。
MODULE_VERSION_LOCATIONS: list[str] = [
    m.alembic_version_dir for m in MODULE_MANIFEST if m.alembic_version_dir is not None
]


def get_module_manifest() -> list[ModuleDefinition]:
    """返回模块清单的副本.

    返回 ``MODULE_MANIFEST`` 的浅拷贝，防止外部修改原始清单。
    """

    return list(MODULE_MANIFEST)


def get_module_version_locations() -> list[str]:
    """返回 Alembic 版本目录列表的副本."""

    return list(MODULE_VERSION_LOCATIONS)
