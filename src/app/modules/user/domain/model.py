"""用户领域实体与状态枚举（SPEC §11.2）。

``User`` 是不可变领域实体，包含用户全部业务字段。实体通过工厂方法
``new`` 创建，修改操作通过 ``with_*`` 方法返回新实例（frozen dataclass）。

``UserStatus`` 使用 ``StrEnum``，状态值为稳定编码（SPEC §8.3），
在 API 响应、数据库存储和跨模块事件载荷中保持一致。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4


class UserStatus(enum.StrEnum):
    """用户状态枚举（SPEC §11.2、§8.3）。

    状态值为稳定编码，使用 ``StrEnum`` 确保 JSON 序列化和数据库
    存储时自动使用字符串值。

    Attributes:
        ACTIVE: 启用——用户可以登录和使用系统
        DISABLED: 禁用——用户不可登录，已有会话全部失效（SPEC §12.3）
    """

    ACTIVE = "active"
    DISABLED = "disabled"


@dataclass(frozen=True)
class User:
    """用户领域实体（SPEC §11.2）。

    包含用户的全部业务字段。实体不可变（frozen dataclass），
    所有修改操作通过 ``with_*`` 方法返回新实例。

    密码哈希（``password_hash``）属于领域实体的内部字段，
    绝不出现在 API 响应模型中（SPEC §9.3、§23.2）。

    Attributes:
        id: 用户唯一标识（UUID）
        username: 用户名 / 登录账号，全局唯一
        display_name: 显示名称
        password_hash: Argon2id 密码哈希（SPEC §12.1），不进入响应
        status: 用户状态
        phone: 手机号（可选，按项目需要启用）
        email: 邮箱（可选，按项目需要启用）
        last_login_at: 最近登录时间（UTC），初始为 None
        password_updated_at: 密码更新时间（UTC）
        created_at: 创建时间（UTC）
        created_by: 创建人 ID（审计字段，SPEC §11.2）
        updated_at: 更新时间（UTC）
        updated_by: 更新人 ID（审计字段，SPEC §11.2）
    """

    id: UUID
    username: str
    display_name: str
    password_hash: str
    status: UserStatus
    phone: str | None
    email: str | None
    last_login_at: datetime | None
    password_updated_at: datetime
    created_at: datetime
    created_by: UUID | None
    updated_at: datetime
    updated_by: UUID | None

    # ------------------------------------------------------------------
    # 工厂方法
    # ------------------------------------------------------------------

    @classmethod
    def new(
        cls,
        *,
        username: str,
        display_name: str,
        password_hash: str,
        phone: str | None = None,
        email: str | None = None,
        current_time: datetime,
        actor_id: UUID | None = None,
    ) -> User:
        """创建新用户实体。

        新用户初始状态为 ``ACTIVE``，最近登录时间为 None。
        创建时间和更新时间均设为 ``current_time``。

        Args:
            username: 用户名（须通过 :class:`~app.modules.user.domain.policy.UsernamePolicy` 校验）
            display_name: 显示名称
            password_hash: 已通过 Argon2id 哈希的密码值
            phone: 手机号（可选）
            email: 邮箱（可选）
            current_time: 当前 UTC 时间
            actor_id: 操作者 ID（审计字段）

        Returns:
            新创建的 :class:`User` 实例
        """
        return cls(
            id=uuid4(),
            username=username,
            display_name=display_name,
            password_hash=password_hash,
            status=UserStatus.ACTIVE,
            phone=phone,
            email=email,
            last_login_at=None,
            password_updated_at=current_time,
            created_at=current_time,
            created_by=actor_id,
            updated_at=current_time,
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
    ) -> User:
        """返回启用状态的新实例（SPEC §11.1）。"""
        return replace(
            self,
            status=UserStatus.ACTIVE,
            updated_at=current_time,
            updated_by=actor_id,
        )

    def disable(
        self,
        *,
        current_time: datetime,
        actor_id: UUID | None = None,
    ) -> User:
        """返回禁用状态的新实例（SPEC §11.1）。"""
        return replace(
            self,
            status=UserStatus.DISABLED,
            updated_at=current_time,
            updated_by=actor_id,
        )

    def change_password(
        self,
        *,
        password_hash: str,
        current_time: datetime,
        actor_id: UUID | None = None,
    ) -> User:
        """返回更新密码后的新实例。

        同时更新密码哈希和密码更新时间（SPEC §11.2）。
        """
        return replace(
            self,
            password_hash=password_hash,
            password_updated_at=current_time,
            updated_at=current_time,
            updated_by=actor_id,
        )

    def with_profile_updates(
        self,
        *,
        field_updates: dict[str, str | None],
        current_time: datetime,
        actor_id: UUID | None = None,
    ) -> User:
        """返回应用资料更新后的新实例（SPEC §11.1）。

        使用字段字典实现部分更新：仅修改字典中包含的字段。
        字典中字段值为 ``None`` 表示将该字段清空。

        Args:
            field_updates: 字段更新字典，键为字段名，值为新值
            current_time: 当前 UTC 时间
            actor_id: 操作者 ID

        Returns:
            更新后的 :class:`User` 新实例
        """
        changes: dict[str, Any] = {
            "updated_at": current_time,
            "updated_by": actor_id,
        }
        if "display_name" in field_updates:
            changes["display_name"] = field_updates["display_name"]
        if "phone" in field_updates:
            changes["phone"] = field_updates["phone"]
        if "email" in field_updates:
            changes["email"] = field_updates["email"]
        return replace(self, **changes)

    def record_login(self, *, login_time: datetime) -> User:
        """返回更新最近登录时间后的新实例（SPEC §11.2）。

        由认证模块（TASK-015）在登录成功后调用。
        """
        return replace(self, last_login_at=login_time)

    # ------------------------------------------------------------------
    # 查询方法
    # ------------------------------------------------------------------

    @property
    def is_active(self) -> bool:
        """用户是否处于启用状态。"""
        return self.status is UserStatus.ACTIVE

    @property
    def is_disabled(self) -> bool:
        """用户是否处于禁用状态。"""
        return self.status is UserStatus.DISABLED
