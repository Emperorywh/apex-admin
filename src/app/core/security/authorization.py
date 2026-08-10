"""授权执行核心逻辑 — SPEC 13.3 / 13.4 / 23.5.

SPEC 13.3: "提供统一的权限校验入口"。
SPEC 13.3: "禁止各模块复制权限判断逻辑"。
SPEC 13.4: "超级管理员绕过规则集中管理"。
SPEC 13.4: "禁止通过魔法用户 ID 判断超级管理员"。

此模块是全系统唯一的授权判断入口——超级管理员判定、权限点校验
和管理范围检查均集中于此，各模块 Use Case 和路由依赖不得复制
判断逻辑（SPEC 13.3）。

超管判定基于角色编码 ``super_admin``（SPEC 13.4: "超级管理员能力
具有显式定义"），不通过魔法用户 ID 或硬编码 UUID 判断
（SPEC 13.4: "禁止通过魔法用户 ID 判断超级管理员"）。
"""

from __future__ import annotations

from app.core.errors.exceptions import AuthorizationError

#: 超级管理员角色编码 — 全系统唯一判定依据（SPEC 13.4）。
#:
#: 超级管理员通过拥有此角色编码的角色来判定，不通过用户 ID 或 UUID。
#: 此常量集中定义于唯一位置（SPEC 13.4: "超级管理员绕过规则集中管理"）。
SUPER_ADMIN_ROLE_CODE: str = "super_admin"


def is_super_admin(role_codes: frozenset[str] | set[str]) -> bool:
    """判定用户是否为超级管理员 — SPEC 13.4.

    基于角色编码判定，不使用魔法用户 ID 或 UUID
    （SPEC 13.4: "禁止通过魔法用户 ID 判断超级管理员"）。

    参数:
        role_codes: 用户拥有的全部角色编码集合。

    返回:
        用户拥有 ``super_admin`` 角色时返回 True。
    """

    return SUPER_ADMIN_ROLE_CODE in role_codes


def check_permission(
    *,
    user_permissions: frozenset[str] | set[str],
    required_permission: str,
    user_is_super_admin: bool,
) -> None:
    """权限点校验入口 — SPEC 13.3 / 23.5.

    SPEC 23.5: "所有管理接口具有权限点"。
    SPEC 13.3: "权限拒绝行为统一返回稳定错误码"。

    超级管理员绕过权限校验（SPEC 13.4: "超级管理员不受此限制"）。

    参数:
        user_permissions:    用户的有效权限编码集合。
        required_permission: 路由或操作要求的权限编码。
        user_is_super_admin: 用户是否为超级管理员。

    抛出:
        AuthorizationError: 用户无所需权限且非超级管理员（HTTP 403，
                            稳定错误码 ``AUTH.FORBIDDEN``）。
    """

    if user_is_super_admin:
        return
    if required_permission not in user_permissions:
        raise AuthorizationError(f"缺少权限: {required_permission}")


def check_management_scope(
    *,
    actor_permissions: frozenset[str] | set[str],
    target_permissions: frozenset[str] | set[str],
    actor_is_super_admin: bool,
) -> None:
    """管理范围校验 — SPEC 13.2.

    SPEC 13.2: "管理范围固定按权限点集合定义：用户的管理范围为其全部
    启用角色的权限点并集；普通管理员只能授予自身范围内的权限点和角色，
    只能对管理范围是自身范围子集的用户执行管理操作，超级管理员不受
    此限制"。

    参数:
        actor_permissions:    操作者的有效权限编码集合。
        target_permissions:   目标（被操作）的权限编码集合。
        actor_is_super_admin: 操作者是否为超级管理员。

    抛出:
        AuthorizationError: 目标权限集不是操作者权限集的子集，
                            且操作者非超级管理员（HTTP 403）。
    """

    if actor_is_super_admin:
        return
    if not target_permissions.issubset(actor_permissions):
        raise AuthorizationError("操作超出管理范围")
