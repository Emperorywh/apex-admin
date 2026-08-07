"""RBAC 领域实体与状态枚举（SPEC §13.1、§13.2、§13.4）。

``Role`` 是不可变领域实体，包含角色的全部业务字段。实体通过工厂方法
``new`` 创建，修改操作通过 ``enable``/``disable``/``update`` 方法返回新实例
（frozen dataclass）。

``RoleStatus`` 使用 ``StrEnum``，状态值为稳定编码（SPEC §8.3）。

超级管理员通过角色上的 ``is_super_admin`` 标志显式定义（SPEC §13.4：
禁止通过魔法用户 ID 判断超级管理员）。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4


class RoleStatus(enum.StrEnum):
    """角色状态枚举（SPEC §13.2、§8.3）。

    Attributes:
        ACTIVE: 启用——角色有效，其权限计入用户管理范围
        DISABLED: 禁用——角色无效，其权限不计入用户管理范围
    """

    ACTIVE = "active"
    DISABLED = "disabled"


@dataclass(frozen=True)
class Role:
    """角色领域实体（SPEC §13.1、§13.2、§13.4）。

    包含角色的全部业务字段。实体不可变（frozen dataclass），
    所有修改操作通过方法返回新实例。

    超级管理员通过 ``is_super_admin`` 标志显式定义（SPEC §13.4：
    显式定义、禁止魔法用户 ID）。

    内置角色通过 ``is_builtin`` 标志标记，受保护规则约束
    （SPEC §13.2：系统内置角色具有明确保护规则）。

    Attributes:
        id: 角色唯一标识（UUID）
        code: 角色编码，全局唯一且稳定
        name: 角色名称
        status: 角色状态
        description: 描述（可选）
        is_builtin: 是否为系统内置角色
        is_super_admin: 是否为超级管理员角色（SPEC §13.4）
        created_at: 创建时间（UTC）
        updated_at: 更新时间（UTC）
        created_by: 创建人 ID（审计字段）
        updated_by: 更新人 ID（审计字段）
    """

    id: UUID
    code: str
    name: str
    status: RoleStatus
    description: str | None
    is_builtin: bool
    is_super_admin: bool
    created_at: datetime
    updated_at: datetime
    created_by: UUID | None
    updated_by: UUID | None

    # ------------------------------------------------------------------
    # 工厂方法
    # ------------------------------------------------------------------

    @classmethod
    def new(
        cls,
        *,
        code: str,
        name: str,
        description: str | None = None,
        is_super_admin: bool = False,
        is_builtin: bool = False,
        current_time: datetime,
        actor_id: UUID | None = None,
    ) -> Role:
        """创建新角色实体。

        新角色初始状态为 ``ACTIVE``。

        Args:
            code: 角色编码，全局唯一且稳定
            name: 角色名称
            description: 描述（可选）
            is_super_admin: 是否为超级管理员角色（SPEC §13.4）
            is_builtin: 是否为系统内置角色
            current_time: 当前 UTC 时间
            actor_id: 操作者 ID（审计字段）

        Returns:
            新创建的 :class:`Role` 实例
        """
        return cls(
            id=uuid4(),
            code=code,
            name=name,
            status=RoleStatus.ACTIVE,
            description=description,
            is_builtin=is_builtin,
            is_super_admin=is_super_admin,
            created_at=current_time,
            updated_at=current_time,
            created_by=actor_id,
            updated_by=actor_id,
        )

    # ------------------------------------------------------------------
    # 状态变更方法（返回新实例）
    # ------------------------------------------------------------------

    def enable(
        self,
        *,
        current_time: datetime,
        actor_id: UUID | None = None,
    ) -> Role:
        """返回启用状态的新实例（SPEC §13.2）。"""
        return replace(
            self,
            status=RoleStatus.ACTIVE,
            updated_at=current_time,
            updated_by=actor_id,
        )

    def disable(
        self,
        *,
        current_time: datetime,
        actor_id: UUID | None = None,
    ) -> Role:
        """返回禁用状态的新实例（SPEC §13.2）。"""
        return replace(
            self,
            status=RoleStatus.DISABLED,
            updated_at=current_time,
            updated_by=actor_id,
        )

    def update(
        self,
        *,
        field_updates: dict[str, str | None],
        current_time: datetime,
        actor_id: UUID | None = None,
    ) -> Role:
        """返回应用更新后的新实例（SPEC §13.2）。

        允许更新名称和描述。不允许更新编码、状态、is_builtin 和 is_super_admin。

        Args:
            field_updates: 字段更新字典
            current_time: 当前 UTC 时间
            actor_id: 操作者 ID

        Returns:
            更新后的 :class:`Role` 新实例
        """
        changes: dict[str, Any] = {
            "updated_at": current_time,
            "updated_by": actor_id,
        }
        if "name" in field_updates:
            changes["name"] = field_updates["name"]
        if "description" in field_updates:
            changes["description"] = field_updates["description"]
        return replace(self, **changes)

    # ------------------------------------------------------------------
    # 查询方法
    # ------------------------------------------------------------------

    @property
    def is_active(self) -> bool:
        """角色是否处于启用状态。"""
        return self.status is RoleStatus.ACTIVE

    @property
    def is_disabled(self) -> bool:
        """角色是否处于禁用状态。"""
        return self.status is RoleStatus.DISABLED
