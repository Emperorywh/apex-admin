"""auth create-admin / auth sync-permissions 命令（SPEC §25.2）。

- ``auth create-admin``：安全创建首个管理员（SPEC §25.2）
- ``auth sync-permissions``：幂等同步 G2 模块声明的权限点（SPEC §25.2）
"""

from __future__ import annotations

import asyncio
import getpass
import logging
import sys
from datetime import UTC, datetime

from app.composition_root import get_enabled_modules
from app.config.settings import Settings
from app.infrastructure.database.db_pool_provider import SqlAlchemyDbPoolProvider
from app.modules.rbac.application.permission_sync import PermissionSyncService
from app.modules.rbac.infrastructure.unit_of_work import SqlAlchemyRbacUnitOfWork
from app.modules.user.domain.password import PasswordHasher
from app.modules.user.domain.policy import PasswordPolicy, UsernamePolicy
from app.modules.user.infrastructure.repository import SqlAlchemyUserRepository
from app.modules.user.infrastructure.unit_of_work import SqlAlchemyUserUnitOfWork

_logger = logging.getLogger("app.cli.auth")

#: 内置超级管理员角色编码（SPEC §13.4）
SUPER_ADMIN_ROLE_CODE = "super_admin"
#: 内置超级管理员角色名称
SUPER_ADMIN_ROLE_NAME = "超级管理员"


def auth_create_admin() -> int:
    """安全创建首个管理员（SPEC §25.2）。

    管理员初始密码只能通过交互式隐藏输入或受控标准输入传入，
    不允许作为命令行参数（SPEC §25.2）。

    流程：
    1. 交互式读取用户名和密码（``getpass`` 隐藏输入）
    2. 创建内置超级管理员角色（如不存在）
    3. 创建管理员用户
    4. 分配超级管理员角色
    5. 密码不在参数/输出/日志中

    幂等：用户名已存在时不创建重复用户（SPEC §25.2）。

    Returns:
        0 表示成功，1 表示失败
    """
    settings = Settings()  # type: ignore[call-arg]
    return asyncio.run(_async_create_admin(settings))


async def _async_create_admin(settings: Settings) -> int:
    """create-admin 的异步实现。"""
    # 读取用户名
    username = _read_input("管理员用户名: ")
    if not username:
        print("错误：用户名不能为空", file=sys.stderr)
        return 1

    # 读取显示名称
    display_name = _read_input("显示名称: ")
    if not display_name:
        display_name = username

    # 读取密码——隐藏输入（SPEC §25.2：交互式隐藏输入）
    password = _read_password()
    if not password:
        print("错误：密码不能为空", file=sys.stderr)
        return 1

    # 校验用户名和密码策略
    try:
        UsernamePolicy.validate(username)
        PasswordPolicy.validate(password)
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    provider = SqlAlchemyDbPoolProvider(settings)
    try:
        await provider.initialize()
        engine = provider.engine
        assert engine is not None  # noqa: S101

        # 幂等检查：用户名已存在则跳过（SPEC §25.2）
        async with SqlAlchemyUserUnitOfWork(engine) as uow:
            user_repo = SqlAlchemyUserRepository(uow.session)
            existing = await user_repo.get_by_username(username)
            if existing is not None:
                # 不泄露用户是否存在的区分信息，但 create-admin 是管理命令
                print(f"管理员 '{username}' 已存在，跳过创建")
                return 0

        # 创建内置超级管理员角色（如不存在）
        from app.modules.rbac.infrastructure.repository import (
            SqlAlchemyRoleRepository,
            SqlAlchemyUserRoleRepository,
        )

        async with SqlAlchemyRbacUnitOfWork(engine) as uow:
            role_repo = SqlAlchemyRoleRepository(uow.session)
            role = await role_repo.get_by_code(SUPER_ADMIN_ROLE_CODE)
            if role is None:
                from app.modules.rbac.domain.model import Role

                role = Role.new(
                    code=SUPER_ADMIN_ROLE_CODE,
                    name=SUPER_ADMIN_ROLE_NAME,
                    description="系统内置超级管理员角色（SPEC §13.4）",
                    is_super_admin=True,
                    is_builtin=True,
                    current_time=datetime.now(UTC),
                )
                await role_repo.add(role)

        # 创建管理员用户
        hasher = PasswordHasher()
        password_hash = hasher.hash(password)
        async with SqlAlchemyUserUnitOfWork(engine) as uow:
            user_repo = SqlAlchemyUserRepository(uow.session)
            from app.modules.user.domain.model import User

            user = User.new(
                username=username,
                display_name=display_name,
                password_hash=password_hash,
                current_time=datetime.now(UTC),
            )
            await user_repo.add(user)
            user_id = user.id

        # 分配超级管理员角色
        async with SqlAlchemyRbacUnitOfWork(engine) as uow:
            role_repo = SqlAlchemyRoleRepository(uow.session)
            user_role_repo = SqlAlchemyUserRoleRepository(uow.session)
            role = await role_repo.get_by_code(SUPER_ADMIN_ROLE_CODE)
            assert role is not None  # noqa: S101

            await user_role_repo.assign(
                user_id=user_id,
                role_id=role.id,
                assigned_at=datetime.now(UTC),
            )

        # 日志不包含密码（SPEC §25.2：密码不在日志中）
        _logger.info(
            "管理员创建成功",
            extra={
                "event": "admin_created",
                "username": username,
            },
        )
        print(f"管理员 '{username}' 创建成功")
        return 0
    finally:
        await provider.dispose()


def auth_sync_permissions() -> int:
    """幂等同步 G2 模块声明的权限点（SPEC §25.2）。

    默认只新增和更新，不自动删除代码中已移除但仍被角色引用的权限点
    （SPEC §25.2）。孤立权限点只报告，需要显式确认命令清理。

    Returns:
        0 表示成功，1 表示失败
    """
    settings = Settings()  # type: ignore[call-arg]
    return asyncio.run(_async_sync_permissions(settings))


async def _async_sync_permissions(settings: Settings) -> int:
    """sync-permissions 的异步实现。"""
    modules = get_enabled_modules()

    provider = SqlAlchemyDbPoolProvider(settings)
    try:
        await provider.initialize()
        engine = provider.engine
        assert engine is not None  # noqa: S101

        def uow_factory() -> SqlAlchemyRbacUnitOfWork:
            return SqlAlchemyRbacUnitOfWork(engine)

        service = PermissionSyncService(uow_factory, modules)
        result = await service.sync(current_time=datetime.now(UTC))

        # 报告同步结果
        print(
            f"权限同步完成：{result.total_declared} 个声明权限点"
            f"（新增 {len(result.added)}，更新 {len(result.updated)}，"
            f"未变 {len(result.unchanged)}）"
        )

        if result.orphans:
            print(f"\n孤立权限点（{len(result.orphans)} 个）：")
            for code in sorted(result.orphans):
                referenced = code in result.orphan_referenced
                tag = " [仍被角色引用]" if referenced else ""
                print(f"  - {code}{tag}")
            print("\n孤立权限点未自动删除（SPEC §25.2），需要显式确认命令清理。")

        return 0
    finally:
        await provider.dispose()


# ---------------------------------------------------------------------------
# 输入读取辅助
# ---------------------------------------------------------------------------


def _read_input(prompt: str) -> str:
    """读取标准输入。

    交互模式下显示提示，非交互模式（管道输入）时也读取 stdin。
    """
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def _read_password() -> str:
    """读取密码——隐藏输入（SPEC §25.2）。

    使用 ``getpass.getpass`` 在交互终端隐藏输入。
    当 stdin 非交互式（管道）时，``getpass`` 自动回退到从 stdin 读取。
    密码不显示在终端、不写入命令历史。
    """
    return getpass.getpass("管理员密码（隐藏输入）: ")
