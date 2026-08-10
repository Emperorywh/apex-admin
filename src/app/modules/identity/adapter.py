"""用户 Repository Adapter — Infrastructure 层实现（SPEC 5.2 / 5.6）.

SPEC 5.2: "Infrastructure 只实现内层 Port，不得在内层暴露 SQLAlchemy 类型"。
SPEC 5.6: "Repository Adapter 由 Composition Root 使用当前 Unit of Work
拥有的 AsyncSession 构造"。

Adapter 接收 ``AsyncSession``，实现 ``UserRepository`` Port。
Adapter 在内部将 ORM 模型与领域实体互转，确保内层不感知 ORM 类型
（SPEC 5.2: "DTO、API Schema、领域对象和 ORM 模型职责分离"）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.core.api.pagination import SortField, SortOrder
from app.core.errors.exceptions import UniqueViolationError
from app.infrastructure.db.exceptions import translate_db_exception
from app.modules.identity.errors import UserAlreadyExistsError
from app.modules.identity.models import User, UserAuthInfo, UserStatus
from app.modules.identity.orm import UserORM
from app.modules.identity.port import UserAuthPort, UserRepository

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession


class SqlAlchemyUserRepository(UserRepository):
    """SQLAlchemy 异步用户 Repository Adapter — 实现 ``UserRepository`` Port.

    由 Composition Root（或 Use Case 内部）使用当前 UoW 的 ``AsyncSession`` 构造。
    Adapter 方法返回领域实体 ``User``，不是 ORM 模型，
    实现 ORM 类型不泄漏（SPEC 5.2 / 8.1）。
    """

    def __init__(self, session: AsyncSession) -> None:
        """初始化 Adapter，绑定当前事务的 AsyncSession.

        参数:
            session: 当前 UoW 拥有的 AsyncSession（SPEC 5.6）。
        """

        self._session = session

    async def add(self, user: User) -> None:
        """添加新用户到当前事务.

        SPEC 8.3: "唯一性规则优先由数据库唯一约束保证"。
        用户名冲突时由数据库唯一约束拦截，翻译为 ``UserAlreadyExistsError``
        （SPEC 8.4: "冲突错误具有明确的业务错误码"）。
        """

        orm = _to_orm(user)
        self._session.add(orm)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            translated = translate_db_exception(exc)
            if isinstance(translated, UniqueViolationError):
                raise UserAlreadyExistsError(
                    f"用户名 '{user.username}' 已存在",
                ) from exc
            raise

    async def get_by_id(self, user_id: UUID) -> User | None:
        """按 ID 查询用户，返回领域实体或 None。"""

        stmt = select(UserORM).where(UserORM.id == user_id)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        return _to_domain(orm) if orm else None

    async def get_by_username(self, username: str) -> User | None:
        """按用户名查询用户，返回领域实体或 None。"""

        stmt = select(UserORM).where(UserORM.username == username)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        return _to_domain(orm) if orm else None

    async def list_users(
        self,
        *,
        offset: int,
        limit: int,
        sort_fields: list[SortField],
        status_filter: UserStatus | None,
    ) -> tuple[list[User], int]:
        """分页查询用户列表.

        SPEC 9.4: 排序字段已通过白名单校验，直接用于构建 ORDER BY。
        status_filter 为 None 时不筛选状态。
        """

        # 总数查询
        count_stmt = select(func.count()).select_from(UserORM)
        if status_filter is not None:
            count_stmt = count_stmt.where(UserORM.status == status_filter.value)
        total = (await self._session.execute(count_stmt)).scalar() or 0

        # 数据查询
        stmt = select(UserORM)
        if status_filter is not None:
            stmt = stmt.where(UserORM.status == status_filter.value)
        stmt = stmt.offset(offset).limit(limit)
        for sf in sort_fields:
            col = getattr(UserORM, sf.name)
            stmt = stmt.order_by(
                col.desc() if sf.order == SortOrder.DESC else col.asc(),
            )
        result = await self._session.execute(stmt)
        users = [_to_domain(orm) for orm in result.scalars().all()]
        return users, int(total)

    async def save(self, user: User) -> None:
        """保存用户变更.

        通过查询现有 ORM 记录并逐字段更新，确保只修改显式赋值的字段。
        """

        stmt = select(UserORM).where(UserORM.id == user.id)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        if orm is None:
            from app.modules.identity.errors import UserNotFoundError

            raise UserNotFoundError(str(user.id))

        orm.username = user.username
        orm.display_name = user.display_name
        orm.password_hash = user.password_hash
        orm.status = user.status.value
        orm.phone = user.phone
        orm.email = user.email
        orm.last_login_at = user.last_login_at
        orm.password_updated_at = user.password_updated_at
        orm.updated_at = user.updated_at
        orm.updated_by = user.updated_by
        await self._session.flush()

    async def delete_by_id(self, user_id: UUID) -> bool:
        """按 ID 物理删除用户，返回是否删除成功。

        SPEC 11.3: 物理删除前的审计记录存在性检查由 Use Case 完成。
        """

        stmt = select(UserORM).where(UserORM.id == user_id)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        if orm is None:
            return False
        await self._session.delete(orm)
        await self._session.flush()
        return True


# ── ORM ↔ 领域实体转换 ──────────────────────────────────────────────────────


def _to_domain(orm: UserORM) -> User:
    """ORM 模型 → 领域实体转换 — SPEC 5.2 职责分离."""

    return User(
        id=orm.id,
        username=orm.username,
        display_name=orm.display_name,
        password_hash=orm.password_hash,
        status=UserStatus(orm.status),
        phone=orm.phone,
        email=orm.email,
        last_login_at=orm.last_login_at,
        password_updated_at=orm.password_updated_at,
        created_at=orm.created_at,
        updated_at=orm.updated_at,
        created_by=orm.created_by,
        updated_by=orm.updated_by,
    )


def _to_orm(user: User) -> UserORM:
    """领域实体 → ORM 模型转换 — SPEC 5.2 职责分离.

    用于 ``add`` 方法将新领域实体写入事务缓冲区。
    """

    return UserORM(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        password_hash=user.password_hash,
        status=user.status.value,
        phone=user.phone,
        email=user.email,
        last_login_at=user.last_login_at,
        password_updated_at=user.password_updated_at,
        created_at=user.created_at,
        updated_at=user.updated_at,
        created_by=user.created_by,
        updated_by=user.updated_by,
    )


class SqlAlchemyUserAuthAdapter(UserAuthPort):
    """SQLAlchemy 用户认证信息 Adapter — 实现 ``UserAuthPort`` Port.

    SPEC 5.5: auth 模块通过此 Port 跨模块查询用户认证数据。
    返回 ``UserAuthInfo`` 投影（最小字段集），不暴露完整 ``User`` 实体。

    由 Composition Root 使用当前 UoW 的 AsyncSession 构造（SPEC 5.6）。
    """

    def __init__(self, session: AsyncSession) -> None:
        """初始化 Adapter，绑定当前事务的 AsyncSession."""

        self._session = session

    async def get_auth_info_by_username(self, username: str) -> UserAuthInfo | None:
        """按用户名查询认证信息投影 — SPEC 12.1 登录用."""

        stmt = select(UserORM).where(UserORM.username == username)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        if orm is None:
            return None
        return UserAuthInfo(
            id=orm.id,
            username=orm.username,
            display_name=orm.display_name,
            password_hash=orm.password_hash,
            status=UserStatus(orm.status),
        )

    async def get_status_by_id(self, user_id: UUID) -> UserStatus | None:
        """按 ID 查询用户状态 — SPEC 12.3 认证依赖用."""

        stmt = select(UserORM.status).where(UserORM.id == user_id)
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        return UserStatus(row) if row else None

    async def update_login_state(
        self,
        user_id: UUID,
        *,
        last_login_at: datetime,
        new_password_hash: str | None = None,
    ) -> None:
        """更新用户登录状态 — 同事务（SPEC 12.1）.

        SPEC 12.1: "登录成功时使用 check_needs_rehash 判断并在同一事务中
        升级旧参数哈希"。当 ``new_password_hash`` 非空时同时更新密码哈希
        和密码更新时间。
        """

        values: dict[str, datetime | str] = {
            "last_login_at": last_login_at,
            "updated_at": last_login_at,
        }
        if new_password_hash is not None:
            values["password_hash"] = new_password_hash
            values["password_updated_at"] = last_login_at

        stmt = update(UserORM).where(UserORM.id == user_id).values(**values)
        await self._session.execute(stmt)
        await self._session.flush()

    async def count_active_users_by_ids(self, user_ids: set[UUID]) -> int:
        """查询给定用户 ID 集合中处于启用状态的用户数量 — SPEC 13.4."""

        if not user_ids:
            return 0
        stmt = (
            select(func.count())
            .select_from(UserORM)
            .where(
                UserORM.id.in_(user_ids),
                UserORM.status == UserStatus.ACTIVE.value,
            )
        )
        result = await self._session.execute(stmt)
        return int(result.scalar() or 0)
