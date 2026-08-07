"""用户模块领域层与应用服务单元测试（SPEC §11、§12.1、§23.2）。

使用内存 Fake UoW 和 Repository 验证 Use Case 的编排逻辑、
领域策略、密码哈希、超级管理员保护和事件收集行为。
不依赖数据库或 Docker。

覆盖范围：
- 领域实体创建、不变性和状态转换
- 用户名策略和密码策略校验（含 Unicode 字符计数）
- Argon2id 密码哈希与验证
- 全部 Use Case 的成功和失败路径
- 最后一个超级管理员保护
- 密码哈希不出现在响应 Schema 中
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import TracebackType
from typing import Self
from uuid import UUID

import pytest

from app.errors import ConflictError, NotFoundError, ParameterError
from app.events.dispatcher import TransactionalEventDispatcher
from app.events.registry import EventHandlerRegistry
from app.modules.registry import ModuleRegistry
from app.modules.user.application.port import (
    LastSuperAdminCheck,
    UserRepository,
    UserUnitOfWork,
)
from app.modules.user.application.schemas import (
    CreateUserRequest,
    UserResponse,
)
from app.modules.user.application.service import UserService
from app.modules.user.domain.events import UserCreated, UserDisabled
from app.modules.user.domain.model import User, UserStatus
from app.modules.user.domain.password import PasswordHasher
from app.modules.user.domain.policy import PasswordPolicy, UsernamePolicy

pytestmark = [pytest.mark.unit, pytest.mark.g2]


# ===========================================================================
# Fake 实现（内存，不依赖数据库）
# ===========================================================================


class FakeUserRepository(UserRepository):
    """内存用户 Repository。"""

    def __init__(self) -> None:
        self._users: dict[UUID, User] = {}

    async def add(self, entity: User) -> None:
        self._users[entity.id] = entity

    async def get_by_id(self, user_id: UUID) -> User | None:
        return self._users.get(user_id)

    async def get_by_username(self, username: str) -> User | None:
        for user in self._users.values():
            if user.username == username:
                return user
        return None

    async def count(self) -> int:
        return len(self._users)

    async def list_paginated(self, offset: int, limit: int) -> list[User]:
        all_users = sorted(
            self._users.values(),
            key=lambda u: u.created_at,
            reverse=True,
        )
        return all_users[offset : offset + limit]

    async def update(self, entity: User) -> None:
        self._users[entity.id] = entity


class FakeUserUnitOfWork(UserUnitOfWork):
    """内存用户 UoW，记录提交/回滚状态。"""

    def __init__(self) -> None:
        self._repo = FakeUserRepository()
        self.committed = False
        self.rolled_back = False
        self._active = False

    async def __aenter__(self) -> Self:
        self._active = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self._active = False
        if exc_type is None:
            self.committed = True
        else:
            self.rolled_back = True

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True

    @property
    def users(self) -> FakeUserRepository:
        return self._repo


class FakeLastSuperAdminCheck(LastSuperAdminCheck):
    """可配置的 Fake 超级管理员检查。"""

    def __init__(self, result: bool = False) -> None:
        self._result = result

    async def is_last_available_super_admin(self, user_id: UUID) -> bool:
        return self._result


def _make_dispatcher() -> TransactionalEventDispatcher:
    """构造带空处理器注册表的事件调度器。"""
    empty_registry = EventHandlerRegistry(ModuleRegistry([]), {})
    return TransactionalEventDispatcher(empty_registry)


def _make_service(
    *,
    uow: FakeUserUnitOfWork | None = None,
    super_admin_check: LastSuperAdminCheck | None = None,
    dispatcher: TransactionalEventDispatcher | None = None,
) -> UserService:
    """快速构造 UserService 测试实例。"""
    return UserService(
        uow_factory=lambda: uow or FakeUserUnitOfWork(),
        password_hasher=PasswordHasher(),
        last_super_admin_check=super_admin_check or FakeLastSuperAdminCheck(False),
        event_dispatcher=dispatcher or _make_dispatcher(),
    )


_VALID_PASSWORD = "SecurePass123!"


# ===========================================================================
# 领域实体测试
# ===========================================================================


class TestUserEntity:
    """用户领域实体测试。"""

    def test_new_creates_active_user(self) -> None:
        """新用户初始状态为 ACTIVE。"""
        now = datetime.now(UTC)
        user = User.new(
            username="alice",
            display_name="Alice",
            password_hash="hashed",
            current_time=now,
        )
        assert user.status is UserStatus.ACTIVE
        assert user.is_active is True
        assert user.is_disabled is False
        assert user.last_login_at is None
        assert user.password_updated_at == now
        assert user.created_at == now
        assert user.created_by is None
        assert user.updated_at == now

    def test_entity_is_frozen(self) -> None:
        """实体不可变。"""
        from dataclasses import FrozenInstanceError

        user = User.new(
            username="bob",
            display_name="Bob",
            password_hash="hashed",
            current_time=datetime.now(UTC),
        )
        with pytest.raises(FrozenInstanceError):
            user.username = "changed"  # type: ignore[misc]

    def test_enable_returns_new_instance(self) -> None:
        """enable 返回新实例且原实例不变。"""
        now = datetime.now(UTC)
        user = User.new(
            username="charlie",
            display_name="Charlie",
            password_hash="hashed",
            current_time=now,
        )
        disabled = user.disable(current_time=now)
        assert disabled.is_disabled
        assert user.is_active  # 原实例不变

        re_enabled = disabled.enable(current_time=now)
        assert re_enabled.is_active
        assert disabled.is_disabled  # disabled 实例不变

    def test_disable_returns_new_instance(self) -> None:
        """disable 返回新实例。"""
        now = datetime.now(UTC)
        user = User.new(
            username="dave",
            display_name="Dave",
            password_hash="hashed",
            current_time=now,
        )
        disabled = user.disable(current_time=now)
        assert disabled.status is UserStatus.DISABLED
        assert user.status is UserStatus.ACTIVE

    def test_change_password_updates_hash_and_time(self) -> None:
        """change_password 更新密码哈希和密码更新时间。"""
        now = datetime.now(UTC)
        user = User.new(
            username="eve",
            display_name="Eve",
            password_hash="old_hash",
            current_time=now,
        )
        later = datetime.now(UTC)
        updated = user.change_password(
            password_hash="new_hash",
            current_time=later,
        )
        assert updated.password_hash == "new_hash"
        assert updated.password_updated_at == later
        assert user.password_hash == "old_hash"

    def test_with_profile_updates_partial(self) -> None:
        """with_profile_updates 只更新提供的字段。"""
        now = datetime.now(UTC)
        user = User.new(
            username="frank",
            display_name="Frank",
            password_hash="hashed",
            phone="1234567890",
            email="frank@test.com",
            current_time=now,
        )
        updated = user.with_profile_updates(
            field_updates={"display_name": "Frank Jr."},
            current_time=now,
        )
        assert updated.display_name == "Frank Jr."
        assert updated.phone == "1234567890"  # 未提供，保持不变
        assert updated.email == "frank@test.com"  # 未提供，保持不变

    def test_with_profile_updates_clears_nullable_field(self) -> None:
        """with_profile_updates 允许清空可空字段。"""
        now = datetime.now(UTC)
        user = User.new(
            username="grace",
            display_name="Grace",
            password_hash="hashed",
            phone="1234567890",
            email="grace@test.com",
            current_time=now,
        )
        updated = user.with_profile_updates(
            field_updates={"phone": None},
            current_time=now,
        )
        assert updated.phone is None  # 显式清空
        assert updated.email == "grace@test.com"

    def test_record_login_updates_last_login(self) -> None:
        """record_login 更新最近登录时间。"""
        now = datetime.now(UTC)
        user = User.new(
            username="heidi",
            display_name="Heidi",
            password_hash="hashed",
            current_time=now,
        )
        login_time = datetime.now(UTC)
        updated = user.record_login(login_time=login_time)
        assert updated.last_login_at == login_time
        assert user.last_login_at is None

    def test_new_generates_unique_uuids(self) -> None:
        """每次创建生成不同 UUID。"""
        now = datetime.now(UTC)
        user_a = User.new(
            username="user_a",
            display_name="A",
            password_hash="hashed",
            current_time=now,
        )
        user_b = User.new(
            username="user_b",
            display_name="B",
            password_hash="hashed",
            current_time=now,
        )
        assert user_a.id != user_b.id


# ===========================================================================
# 用户名策略测试
# ===========================================================================


class TestUsernamePolicy:
    """用户名校验策略测试（SPEC §11.2）。"""

    def test_valid_username_passes(self) -> None:
        """合法用户名通过校验。"""
        UsernamePolicy.validate("alice")
        UsernamePolicy.validate("alice_01")
        UsernamePolicy.validate("alice-01")
        UsernamePolicy.validate("ABC123")

    def test_empty_username_raises(self) -> None:
        """空用户名抛出 ValueError。"""
        with pytest.raises(ValueError, match="不能为空"):
            UsernamePolicy.validate("")

    def test_too_short_raises(self) -> None:
        """太短的用户名抛出 ValueError。"""
        with pytest.raises(ValueError, match="不能少于"):
            UsernamePolicy.validate("ab")

    def test_too_long_raises(self) -> None:
        """太长的用户名抛出 ValueError。"""
        with pytest.raises(ValueError, match="不能超过"):
            UsernamePolicy.validate("a" * 51)

    def test_invalid_characters_raises(self) -> None:
        """包含非法字符抛出 ValueError。"""
        with pytest.raises(ValueError, match="只能包含"):
            UsernamePolicy.validate("alice@home")

    def test_boundary_lengths_pass(self) -> None:
        """边界长度通过：3 和 50。"""
        UsernamePolicy.validate("abc")
        UsernamePolicy.validate("a" * 50)


# ===========================================================================
# 密码策略测试
# ===========================================================================


class TestPasswordPolicy:
    """密码校验策略测试（SPEC §23.2）。

    SPEC §23.2：密码最小长度为 12 个 Unicode 字符，最大长度为 128 个
    Unicode 字符；不得静默截断。
    """

    def test_min_length_passes(self) -> None:
        """恰好 12 个字符通过。"""
        PasswordPolicy.validate("a" * 12)

    def test_max_length_passes(self) -> None:
        """恰好 128 个字符通过。"""
        PasswordPolicy.validate("a" * 128)

    def test_too_short_raises(self) -> None:
        """少于 12 个字符抛出 ValueError。"""
        with pytest.raises(ValueError, match="不能少于 12"):
            PasswordPolicy.validate("a" * 11)

    def test_too_long_raises_not_truncate(self) -> None:
        """超过 128 个字符抛出 ValueError（不截断）。"""
        with pytest.raises(ValueError, match="不能超过 128"):
            PasswordPolicy.validate("a" * 129)

    def test_unicode_chars_counted_correctly(self) -> None:
        """Unicode 字符按码点计数，不是字节。

        12 个中日韩字符应通过最小长度校验。
        """
        # 每个中文字符是 1 个 Unicode 码点
        chinese_12 = "密" * 12
        assert len(chinese_12) == 12
        PasswordPolicy.validate(chinese_12)

    def test_unicode_short_raises(self) -> None:
        """11 个中日韩字符不通过最小长度。"""
        chinese_11 = "密" * 11
        with pytest.raises(ValueError, match="不能少于 12"):
            PasswordPolicy.validate(chinese_11)


# ===========================================================================
# Argon2id 密码哈希测试
# ===========================================================================


class TestPasswordHasher:
    """Argon2id 密码哈希测试（SPEC §12.1、§23.2）。"""

    def test_hash_produces_valid_argon2id(self) -> None:
        """hash 返回 Argon2id 编码字符串。"""
        hasher = PasswordHasher()
        h = hasher.hash(_VALID_PASSWORD)
        assert h.startswith("$argon2id$")

    def test_hash_generates_unique_salt(self) -> None:
        """每次 hash 使用独立随机盐——结果不同（SPEC §23.2）。"""
        hasher = PasswordHasher()
        h1 = hasher.hash(_VALID_PASSWORD)
        h2 = hasher.hash(_VALID_PASSWORD)
        assert h1 != h2

    def test_verify_correct_password(self) -> None:
        """verify 正确密码返回 True。"""
        hasher = PasswordHasher()
        h = hasher.hash(_VALID_PASSWORD)
        assert hasher.verify(h, _VALID_PASSWORD) is True

    def test_verify_wrong_password(self) -> None:
        """verify 错误密码返回 False（不抛异常）。"""
        hasher = PasswordHasher()
        h = hasher.hash(_VALID_PASSWORD)
        assert hasher.verify(h, "WrongPassword!!") is False

    def test_hash_contains_fixed_parameters(self) -> None:
        """哈希包含 SPEC §12.1 固定参数。"""
        hasher = PasswordHasher()
        h = hasher.hash(_VALID_PASSWORD)
        # Argon2id 编码格式：$argon2id$v=19$m=65536,t=3,p=1$...
        assert "m=65536" in h
        assert "t=3" in h
        assert "p=1" in h

    def test_needs_rehash_returns_false_for_current_params(self) -> None:
        """当前参数生成的哈希不需要 rehash。"""
        hasher = PasswordHasher()
        h = hasher.hash(_VALID_PASSWORD)
        assert hasher.needs_rehash(h) is False


# ===========================================================================
# 用户状态枚举测试
# ===========================================================================


class TestUserStatus:
    """用户状态枚举测试（SPEC §8.3）。"""

    def test_active_value(self) -> None:
        """ACTIVE 值为 'active'。"""
        assert UserStatus.ACTIVE.value == "active"

    def test_disabled_value(self) -> None:
        """DISABLED 值为 'disabled'。"""
        assert UserStatus.DISABLED.value == "disabled"

    def test_status_is_string(self) -> None:
        """StrEnum 实例本身即为字符串值。"""
        assert UserStatus.ACTIVE == "active"
        assert UserStatus.DISABLED == "disabled"

    def test_from_string_value(self) -> None:
        """从字符串值构造枚举。"""
        assert UserStatus("active") is UserStatus.ACTIVE
        assert UserStatus("disabled") is UserStatus.DISABLED


# ===========================================================================
# 应用服务测试——创建用户
# ===========================================================================


class TestCreateUser:
    """创建用户 Use Case 测试。"""

    async def test_create_user_success(self) -> None:
        """成功创建用户，事件被收集，UoW 提交。"""
        uow = FakeUserUnitOfWork()
        dispatcher = _make_dispatcher()
        service = _make_service(uow=uow, dispatcher=dispatcher)

        now = datetime.now(UTC)
        user = await service.create_user(
            username="newuser",
            display_name="New User",
            password=_VALID_PASSWORD,
            current_time=now,
        )

        assert user.username == "newuser"
        assert user.display_name == "New User"
        assert user.status is UserStatus.ACTIVE
        assert user.password_hash.startswith("$argon2id$")
        assert uow.committed is True
        assert dispatcher.pending_count == 0
        # 数据已存入 Repository
        assert await uow.users.count() == 1

    async def test_create_user_with_optional_fields(self) -> None:
        """创建用户时可携带手机号和邮箱。"""
        uow = FakeUserUnitOfWork()
        service = _make_service(uow=uow)

        user = await service.create_user(
            username="newuser",
            display_name="New User",
            password=_VALID_PASSWORD,
            phone="13800138000",
            email="user@test.com",
            current_time=datetime.now(UTC),
        )
        assert user.phone == "13800138000"
        assert user.email == "user@test.com"

    async def test_create_user_invalid_username_raises(self) -> None:
        """非法用户名抛出 ParameterError。"""
        uow = FakeUserUnitOfWork()
        service = _make_service(uow=uow)

        with pytest.raises(ParameterError, match="USER.INVALID_INPUT"):
            await service.create_user(
                username="ab",  # 太短
                display_name="Short",
                password=_VALID_PASSWORD,
                current_time=datetime.now(UTC),
            )
        assert uow.rolled_back is True

    async def test_create_user_short_password_raises(self) -> None:
        """太短的密码抛出 ParameterError。"""
        uow = FakeUserUnitOfWork()
        service = _make_service(uow=uow)

        with pytest.raises(ParameterError, match="USER.INVALID_INPUT"):
            await service.create_user(
                username="newuser",
                display_name="New",
                password="short",  # 少于 12 字符
                current_time=datetime.now(UTC),
            )

    async def test_create_user_duplicate_username_raises(self) -> None:
        """重复用户名抛出 ConflictError。"""
        uow = FakeUserUnitOfWork()
        service = _make_service(uow=uow)

        await service.create_user(
            username="existing",
            display_name="Existing",
            password=_VALID_PASSWORD,
            current_time=datetime.now(UTC),
        )
        # 第二次用同用户名应失败
        uow2 = FakeUserUnitOfWork()
        uow2._repo._users = uow._repo._users  # 共享数据
        service2 = _make_service(uow=uow2)

        with pytest.raises(ConflictError, match="USER.ALREADY_EXISTS"):
            await service2.create_user(
                username="existing",
                display_name="Another",
                password=_VALID_PASSWORD,
                current_time=datetime.now(UTC),
            )

    async def test_create_user_collects_event(self) -> None:
        """创建用户时收集 UserCreated 事件。"""
        from app.modules.user.definition import MODULE

        received: list[UserCreated] = []

        async def capture_handler(
            event: UserCreated,  # type: ignore[override]
            uow_obj: object,
        ) -> None:
            if isinstance(event, UserCreated):
                received.append(event)

        async def noop_handler(event: object, uow_obj: object) -> None:  # noqa: ARG001
            pass

        registry = EventHandlerRegistry(
            ModuleRegistry([MODULE]),
            {
                "user.handler.created": capture_handler,
                "user.handler.disabled": noop_handler,
            },
        )
        dispatcher = TransactionalEventDispatcher(registry)
        uow = FakeUserUnitOfWork()
        service = _make_service(uow=uow, dispatcher=dispatcher)

        await service.create_user(
            username="eventuser",
            display_name="Event",
            password=_VALID_PASSWORD,
            current_time=datetime.now(UTC),
        )

        assert len(received) == 1
        assert received[0].username == "eventuser"


# ===========================================================================
# 应用服务测试——查询用户
# ===========================================================================


class TestGetUser:
    """查询用户详情 Use Case 测试。"""

    async def test_get_existing_user(self) -> None:
        """查询存在的用户返回实体。"""
        uow = FakeUserUnitOfWork()
        now = datetime.now(UTC)
        created = User.new(
            username="findme",
            display_name="Find Me",
            password_hash="hashed",
            current_time=now,
        )
        await uow.users.add(created)
        service = _make_service(uow=uow)

        found = await service.get_user(created.id)
        assert found.username == "findme"

    async def test_get_nonexistent_user_raises(self) -> None:
        """查询不存在的用户抛出 NotFoundError。"""
        service = _make_service()

        with pytest.raises(NotFoundError, match="USER.NOT_FOUND"):
            await service.get_user(UUID("00000000-0000-0000-0000-000000000001"))


class TestListUsers:
    """分页查询用户列表 Use Case 测试。"""

    async def test_list_empty(self) -> None:
        """空库返回空列表和零总数。"""
        service = _make_service()
        users, total = await service.list_users(page=1, page_size=20)
        assert users == []
        assert total == 0

    async def test_list_with_data(self) -> None:
        """有数据时返回正确分页。"""
        uow = FakeUserUnitOfWork()
        now = datetime.now(UTC)
        for i in range(3):
            await uow.users.add(
                User.new(
                    username=f"user{i}",
                    display_name=f"User {i}",
                    password_hash="hashed",
                    current_time=now,
                )
            )
        service = _make_service(uow=uow)

        users, total = await service.list_users(page=1, page_size=2)
        assert total == 3
        assert len(users) == 2

    async def test_list_second_page(self) -> None:
        """第二页返回剩余数据。"""
        uow = FakeUserUnitOfWork()
        now = datetime.now(UTC)
        for i in range(3):
            await uow.users.add(
                User.new(
                    username=f"user{i}",
                    display_name=f"User {i}",
                    password_hash="hashed",
                    current_time=now,
                )
            )
        service = _make_service(uow=uow)

        users, total = await service.list_users(page=2, page_size=2)
        assert total == 3
        assert len(users) == 1


# ===========================================================================
# 应用服务测试——更新资料
# ===========================================================================


class TestUpdateUserProfile:
    """更新用户资料 Use Case 测试。"""

    async def test_update_profile_success(self) -> None:
        """成功更新资料。"""
        uow = FakeUserUnitOfWork()
        now = datetime.now(UTC)
        user = User.new(
            username="updateme",
            display_name="Original",
            password_hash="hashed",
            current_time=now,
        )
        await uow.users.add(user)
        service = _make_service(uow=uow)

        updated = await service.update_user_profile(
            user_id=user.id,
            field_updates={"display_name": "Updated"},
            current_time=datetime.now(UTC),
        )
        assert updated.display_name == "Updated"

    async def test_update_profile_not_found(self) -> None:
        """更新不存在的用户抛出 NotFoundError。"""
        service = _make_service()

        with pytest.raises(NotFoundError, match="USER.NOT_FOUND"):
            await service.update_user_profile(
                user_id=UUID("00000000-0000-0000-0000-000000000001"),
                field_updates={"display_name": "X"},
                current_time=datetime.now(UTC),
            )


# ===========================================================================
# 应用服务测试——启用/禁用
# ===========================================================================


class TestEnableDisableUser:
    """启用/禁用用户 Use Case 测试。"""

    async def test_enable_user_success(self) -> None:
        """成功启用已禁用的用户。"""
        uow = FakeUserUnitOfWork()
        now = datetime.now(UTC)
        user = User.new(
            username="enableme",
            display_name="Enable",
            password_hash="hashed",
            current_time=now,
        )
        user = user.disable(current_time=now)
        await uow.users.add(user)
        service = _make_service(uow=uow)

        enabled = await service.enable_user(user_id=user.id, current_time=now)
        assert enabled.is_active

    async def test_enable_not_found(self) -> None:
        """启用不存在的用户抛出 NotFoundError。"""
        service = _make_service()
        with pytest.raises(NotFoundError, match="USER.NOT_FOUND"):
            await service.enable_user(
                user_id=UUID("00000000-0000-0000-0000-000000000001"),
                current_time=datetime.now(UTC),
            )

    async def test_disable_user_success(self) -> None:
        """成功禁用用户。"""
        uow = FakeUserUnitOfWork()
        now = datetime.now(UTC)
        user = User.new(
            username="disableme",
            display_name="Disable",
            password_hash="hashed",
            current_time=now,
        )
        await uow.users.add(user)
        service = _make_service(uow=uow)

        disabled = await service.disable_user(user_id=user.id, current_time=now)
        assert disabled.is_disabled

    async def test_disable_not_found(self) -> None:
        """禁用不存在的用户抛出 NotFoundError。"""
        service = _make_service()
        with pytest.raises(NotFoundError, match="USER.NOT_FOUND"):
            await service.disable_user(
                user_id=UUID("00000000-0000-0000-0000-000000000001"),
                current_time=datetime.now(UTC),
            )

    async def test_disable_last_super_admin_raises(self) -> None:
        """禁用最后一个超级管理员抛出 ConflictError（SPEC §11.1、§13.4）。"""
        uow = FakeUserUnitOfWork()
        now = datetime.now(UTC)
        user = User.new(
            username="superadmin",
            display_name="Super Admin",
            password_hash="hashed",
            current_time=now,
        )
        await uow.users.add(user)
        service = _make_service(
            uow=uow,
            super_admin_check=FakeLastSuperAdminCheck(True),
        )

        with pytest.raises(ConflictError, match="USER.LAST_SUPER_ADMIN"):
            await service.disable_user(user_id=user.id, current_time=now)

    async def test_disable_emits_disabled_event(self) -> None:
        """禁用用户时收集 UserDisabled 事件。"""
        from app.modules.user.definition import MODULE

        received: list[UserDisabled] = []

        async def capture_handler(
            event: UserDisabled,  # type: ignore[override]
            uow_obj: object,
        ) -> None:
            if isinstance(event, UserDisabled):
                received.append(event)

        async def noop_handler(event: object, uow_obj: object) -> None:  # noqa: ARG001
            pass

        registry = EventHandlerRegistry(
            ModuleRegistry([MODULE]),
            {
                "user.handler.created": noop_handler,
                "user.handler.disabled": capture_handler,
            },
        )
        dispatcher = TransactionalEventDispatcher(registry)
        uow = FakeUserUnitOfWork()
        now = datetime.now(UTC)
        user = User.new(
            username="eventdisable",
            display_name="Event Disable",
            password_hash="hashed",
            current_time=now,
        )
        await uow.users.add(user)
        service = _make_service(uow=uow, dispatcher=dispatcher)

        await service.disable_user(user_id=user.id, current_time=now)

        assert len(received) == 1
        assert received[0].user_id == user.id


# ===========================================================================
# 应用服务测试——重置密码
# ===========================================================================


class TestResetPassword:
    """管理员重置密码 Use Case 测试。"""

    async def test_reset_password_success(self) -> None:
        """成功重置密码。"""
        uow = FakeUserUnitOfWork()
        now = datetime.now(UTC)
        user = User.new(
            username="resetme",
            display_name="Reset",
            password_hash="old_hash",
            current_time=now,
        )
        await uow.users.add(user)
        service = _make_service(uow=uow)

        updated = await service.reset_password(
            user_id=user.id,
            new_password="NewSecurePass123!",
            current_time=datetime.now(UTC),
        )
        assert updated.password_hash != "old_hash"
        assert updated.password_hash.startswith("$argon2id$")

    async def test_reset_password_not_found(self) -> None:
        """重置不存在的用户密码抛出 NotFoundError。"""
        service = _make_service()
        with pytest.raises(NotFoundError, match="USER.NOT_FOUND"):
            await service.reset_password(
                user_id=UUID("00000000-0000-0000-0000-000000000001"),
                new_password="NewSecurePass123!",
                current_time=datetime.now(UTC),
            )

    async def test_reset_password_too_short_raises(self) -> None:
        """重置密码太短抛出 ParameterError。"""
        uow = FakeUserUnitOfWork()
        now = datetime.now(UTC)
        user = User.new(
            username="resetme",
            display_name="Reset",
            password_hash="old_hash",
            current_time=now,
        )
        await uow.users.add(user)
        service = _make_service(uow=uow)

        with pytest.raises(ParameterError, match="USER.INVALID_PASSWORD"):
            await service.reset_password(
                user_id=user.id,
                new_password="short",
                current_time=datetime.now(UTC),
            )


# ===========================================================================
# 应用服务测试——自助修改密码
# ===========================================================================


class TestChangePassword:
    """用户自助修改密码 Use Case 测试。"""

    async def test_change_password_success(self) -> None:
        """成功修改密码——当前密码正确。"""
        hasher = PasswordHasher()
        uow = FakeUserUnitOfWork()
        now = datetime.now(UTC)
        user = User.new(
            username="changeme",
            display_name="Change",
            password_hash=hasher.hash(_VALID_PASSWORD),
            current_time=now,
        )
        await uow.users.add(user)
        service = _make_service(uow=uow)

        updated = await service.change_password(
            user_id=user.id,
            current_password=_VALID_PASSWORD,
            new_password="BrandNewPass456!",
            current_time=datetime.now(UTC),
        )
        assert hasher.verify(updated.password_hash, "BrandNewPass456!") is True

    async def test_change_password_wrong_current_raises(self) -> None:
        """当前密码错误抛出 ParameterError。"""
        hasher = PasswordHasher()
        uow = FakeUserUnitOfWork()
        now = datetime.now(UTC)
        user = User.new(
            username="changeme",
            display_name="Change",
            password_hash=hasher.hash(_VALID_PASSWORD),
            current_time=now,
        )
        await uow.users.add(user)
        service = _make_service(uow=uow)

        with pytest.raises(ParameterError, match="USER.INVALID_CREDENTIALS"):
            await service.change_password(
                user_id=user.id,
                current_password="WrongCurrent!!",
                new_password="BrandNewPass456!",
                current_time=datetime.now(UTC),
            )

    async def test_change_password_new_too_short_raises(self) -> None:
        """新密码太短抛出 ParameterError。"""
        hasher = PasswordHasher()
        uow = FakeUserUnitOfWork()
        now = datetime.now(UTC)
        user = User.new(
            username="changeme",
            display_name="Change",
            password_hash=hasher.hash(_VALID_PASSWORD),
            current_time=now,
        )
        await uow.users.add(user)
        service = _make_service(uow=uow)

        with pytest.raises(ParameterError, match="USER.INVALID_PASSWORD"):
            await service.change_password(
                user_id=user.id,
                current_password=_VALID_PASSWORD,
                new_password="short",
                current_time=datetime.now(UTC),
            )

    async def test_change_password_user_not_found(self) -> None:
        """用户不存在抛出 NotFoundError。"""
        service = _make_service()
        with pytest.raises(NotFoundError, match="USER.NOT_FOUND"):
            await service.change_password(
                user_id=UUID("00000000-0000-0000-0000-000000000001"),
                current_password=_VALID_PASSWORD,
                new_password="BrandNewPass456!",
                current_time=datetime.now(UTC),
            )


# ===========================================================================
# 应用服务测试——自助查询和更新资料
# ===========================================================================


class TestSelfService:
    """用户自助操作测试。"""

    async def test_get_self_profile_success(self) -> None:
        """自助查询资料成功。"""
        uow = FakeUserUnitOfWork()
        now = datetime.now(UTC)
        user = User.new(
            username="myself",
            display_name="My Self",
            password_hash="hashed",
            current_time=now,
        )
        await uow.users.add(user)
        service = _make_service(uow=uow)

        found = await service.get_self_profile(user.id)
        assert found.username == "myself"

    async def test_get_self_profile_not_found(self) -> None:
        """用户不存在抛出 NotFoundError。"""
        service = _make_service()
        with pytest.raises(NotFoundError, match="USER.NOT_FOUND"):
            await service.get_self_profile(UUID("00000000-0000-0000-0000-000000000001"))

    async def test_update_self_profile_success(self) -> None:
        """自助更新资料成功。"""
        uow = FakeUserUnitOfWork()
        now = datetime.now(UTC)
        user = User.new(
            username="myself",
            display_name="My Self",
            password_hash="hashed",
            current_time=now,
        )
        await uow.users.add(user)
        service = _make_service(uow=uow)

        updated = await service.update_self_profile(
            user_id=user.id,
            field_updates={"display_name": "Updated Self"},
            current_time=datetime.now(UTC),
        )
        assert updated.display_name == "Updated Self"
        assert updated.updated_by == user.id  # 自助更新，actor 是自身


# ===========================================================================
# 响应 Schema 安全测试
# ===========================================================================


class TestResponseSecurity:
    """响应 Schema 安全测试（SPEC §9.3、§23.2）。"""

    def test_user_response_has_no_password_hash(self) -> None:
        """UserResponse 不包含 password_hash 字段（SPEC §9.3、§23.2）。"""
        field_names = set(UserResponse.model_fields.keys())
        assert "password_hash" not in field_names

    def test_user_response_fields(self) -> None:
        """UserResponse 包含全部非敏感字段。"""
        field_names = set(UserResponse.model_fields.keys())
        expected = {
            "id",
            "username",
            "display_name",
            "status",
            "phone",
            "email",
            "last_login_at",
            "password_updated_at",
            "created_at",
            "created_by",
            "updated_at",
            "updated_by",
        }
        assert field_names == expected

    def test_user_response_has_extra_forbid(self) -> None:
        """UserResponse 拒绝额外字段。"""
        assert UserResponse.model_config.get("extra") == "forbid"

    def test_create_request_has_extra_forbid(self) -> None:
        """CreateUserRequest 拒绝未知字段（SPEC §9.2）。"""
        assert CreateUserRequest.model_config.get("extra") == "forbid"

    def test_all_request_schemas_have_extra_forbid(self) -> None:
        """全部请求 Schema 拒绝未知字段（SPEC §9.2）。"""
        from app.modules.user.application.schemas import (
            ChangePasswordRequest,
            ResetPasswordRequest,
            UpdateSelfProfileRequest,
            UpdateUserRequest,
        )

        for schema_cls in [
            CreateUserRequest,
            UpdateUserRequest,
            UpdateSelfProfileRequest,
            ResetPasswordRequest,
            ChangePasswordRequest,
        ]:
            assert schema_cls.model_config.get("extra") == "forbid", (
                f"{schema_cls.__name__} 应设置 extra='forbid'"
            )


# ===========================================================================
# 模块定义测试
# ===========================================================================


class TestUserModuleDefinition:
    """用户模块定义测试（SPEC §5.5）。"""

    def test_module_code_is_user(self) -> None:
        """模块编码为 'user'。"""
        from app.modules.user.definition import MODULE

        assert MODULE.code == "user"

    def test_module_has_admin_and_self_routers(self) -> None:
        """模块声明了管理员和自助两个 Router。"""
        from app.modules.user.definition import MODULE

        assert len(MODULE.routers) == 2

    def test_permission_points_format(self) -> None:
        """权限点编码使用 system:user:* 格式（SPEC §5.5）。"""
        from app.modules.user.definition import MODULE

        for perm in MODULE.permission_points:
            parts = perm.code.split(":")
            assert len(parts) >= 3
            assert parts[0] == "system"
            assert parts[1] == "user"

    def test_error_codes_format(self) -> None:
        """错误码使用 USER.* 格式（SPEC §5.5）。"""
        from app.modules.user.definition import MODULE

        for err in MODULE.error_codes:
            assert err.code.startswith("USER.")

    def test_event_handlers_match_events(self) -> None:
        """每个事件处理器声明的事件编码在事件集合中存在。"""
        from app.modules.user.definition import MODULE

        event_codes = {e.code for e in MODULE.events}
        for handler in MODULE.event_handlers:
            assert handler.event_code in event_codes

    def test_registry_validates_without_conflicts(self) -> None:
        """用户模块在注册表中校验通过。"""
        from app.modules.user.definition import MODULE

        registry = ModuleRegistry([MODULE])
        assert registry.get_module("user") is MODULE

    def test_registry_validates_with_example_module(self) -> None:
        """用户模块与示例模块在注册表中同时校验通过。"""
        from app.modules.example.definition import MODULE as EXAMPLE_MODULE
        from app.modules.user.definition import MODULE

        registry = ModuleRegistry([EXAMPLE_MODULE, MODULE])
        assert registry.get_module("user") is MODULE
        assert registry.get_module("example") is EXAMPLE_MODULE

    def test_migration_version_dir(self) -> None:
        """迁移版本目录指向全局 Alembic versions 目录。"""
        from app.modules.user.definition import MODULE

        assert MODULE.migration_version_dir is not None
        assert MODULE.migration_version_dir.name == "versions"
