"""RBAC Use Case — Application 层应用服务（SPEC 5.2 / 5.6 / 5.7 / 13.2 / 18.2）.

SPEC 5.6 事务管理:
  - 一个最外层写 Use Case 对应一个 Unit of Work 和一个 AsyncSession。
  - 最外层写 Use Case 负责开始、提交或回滚。

SPEC 5.7 审计:
  - 角色、权限分配、用户角色变更通过 AuditPort 写审计，
    并与业务事务共同提交（SPEC 5.7 / 18.2）。

SPEC 13.2 角色管理:
  - 创建/详情/分页/更新/启用/禁用/分配权限点/查询成员/分配与移除用户角色。
  - 系统内置角色具有明确保护规则（不可删除、不可禁用）。

SPEC 13.1: 被禁用角色的权限不计入用户有效权限集。

Use Case 在每个写方法中:
  1. 创建新 UoW。
  2. 从 UoW 的 session 构造 Repository Adapter 和审计 Adapter。
  3. 执行业务逻辑。
  4. 显式调用 AuditPort 写审计（同事务提交）。
  5. 提交事务（异常时 ``__aexit__`` 自动回滚）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.api.pagination import SortField, total_pages
from app.modules.audit.diff import FieldWhitelist, generate_diff
from app.modules.audit.models import AuditEntry
from app.modules.rbac.adapter import SqlAlchemyRbacRepository
from app.modules.rbac.errors import (
    BuiltinRoleProtectedError,
    PermissionNotFoundError,
    RoleAlreadyActiveError,
    RoleAlreadyDisabledError,
    RoleNotFoundError,
    UserRoleNotAssignedError,
)
from app.modules.rbac.models import Role, RoleStatus
from app.modules.rbac.schemas import (
    AssignPermissionsRequest,
    AssignUserRolesRequest,
    RoleCreateRequest,
    RoleDetailResponse,
    RoleMemberResponse,
    RoleResponse,
    RoleUpdateRequest,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.application.context import UseCaseContext
    from app.application.ports import Clock, IdGenerator
    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.audit.models import ChangeDiff
    from app.modules.audit.port import AuditPort
    from app.modules.identity.port import UserAuthPort
    from app.modules.rbac.port import RbacRepository


# ── 角色审计字段白名单 — SPEC 18.2 ──────────────────────────────────────────

ROLE_FIELD_WHITELIST = FieldWhitelist(
    module="rbac",
    resource_type="role",
    fields=frozenset({"display_name", "description", "status", "sort_order"}),
)


class RbacUseCase:
    """RBAC Use Case — Application 层应用服务.

    SPEC 5.2 / 5.6: Use Case 是最外层写操作的入口，控制事务边界。
    SPEC 5.7: 审计通过 ``AuditPort`` 显式调用，与业务事务共同提交。

    构造参数:
        uow_factory:           UoW 工厂。
        clock:                 时钟 Port。
        id_generator:          标识生成器 Port。
        audit_factory:         审计 Port 工厂。
        user_auth_port_factory: 用户认证信息 Port 工厂（跨模块，
                               从 AsyncSession 构造 UserAuthPort）。
    """

    def __init__(
        self,
        *,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
        clock: Clock,
        id_generator: IdGenerator,
        audit_factory: Callable[[AsyncSession], AuditPort],
        user_auth_port_factory: Callable[[AsyncSession], UserAuthPort],
    ) -> None:
        """初始化 Use Case，注入所有依赖."""

        self._uow_factory = uow_factory
        self._clock = clock
        self._id_generator = id_generator
        self._audit_factory = audit_factory
        self._user_auth_port_factory = user_auth_port_factory

    def _create_repo(self, session: AsyncSession) -> RbacRepository:
        """从 session 构造 RBAC Repository Adapter — SPEC 5.6."""

        return SqlAlchemyRbacRepository(session)

    def _create_audit(self, session: AsyncSession) -> AuditPort:
        """从 session 构造审计 Port — SPEC 5.7 / 5.2."""

        return self._audit_factory(session)

    def _create_user_auth_port(self, session: AsyncSession) -> UserAuthPort:
        """从 session 构造用户认证信息 Port — SPEC 5.2 跨模块."""

        return self._user_auth_port_factory(session)

    def _make_audit_entry(
        self,
        ctx: UseCaseContext,
        *,
        action: str,
        resource_id: str | None,
        resource_display_name: str | None,
        diff: ChangeDiff | None = None,
    ) -> AuditEntry:
        """构造操作审计条目 — SPEC 18.2 / 5.7."""

        return AuditEntry(
            id=self._id_generator.generate_id(),
            actor_id=ctx.actor_id,
            actor_display_name=ctx.actor_id or "system",
            module="rbac",
            action=action,
            resource_type="role",
            resource_id=resource_id,
            resource_display_name=resource_display_name,
            result="success",
            request_id=ctx.request_id or None,
            diff=diff,
            occurred_at=self._clock.now(),
        )

    @staticmethod
    def _role_state(role: Role) -> dict[str, str | int | None]:
        """提取审计白名单字段状态 — SPEC 18.2."""

        return {
            "display_name": role.display_name,
            "description": role.description,
            "status": role.status.value,
            "sort_order": role.sort_order,
        }

    # ── 角色管理 ──────────────────────────────────────────────────────────

    async def create_role(
        self,
        ctx: UseCaseContext,
        request: RoleCreateRequest,
    ) -> RoleResponse:
        """创建角色 — SPEC 13.2.

        内置角色只能通过初始化器创建，普通管理员创建的角色 ``is_builtin=False``。
        """

        now = self._clock.now()
        role_id = self._id_generator.generate_id()

        role = Role(
            id=role_id,
            code=request.code,
            display_name=request.display_name,
            description=request.description,
            status=RoleStatus.ACTIVE,
            is_builtin=False,
            sort_order=request.sort_order,
            created_at=now,
            updated_at=now,
            created_by=ctx.actor_id,
            updated_by=ctx.actor_id,
        )

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            await repo.add_role(role)

            diff = generate_diff(
                ROLE_FIELD_WHITELIST,
                before=None,
                after=self._role_state(role),
            )
            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="rbac.role.create",
                    resource_id=str(role_id),
                    resource_display_name=role.display_name,
                    diff=diff,
                ),
            )

            await uow.commit()
            return _to_role_response(role)

    async def get_role_detail(
        self,
        ctx: UseCaseContext,
        role_id: UUID,
    ) -> RoleDetailResponse:
        """查询角色详情（含权限编码和成员数量）— SPEC 13.2."""

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            role = await repo.get_role_by_id(role_id)
            if role is None:
                raise RoleNotFoundError(str(role_id))

            permission_codes = await repo.get_role_permission_codes(role_id)
            member_count = await repo.count_role_members(role_id)
            return _to_detail_response(role, permission_codes, member_count)

    async def list_roles(
        self,
        ctx: UseCaseContext,
        *,
        page: int,
        page_size: int,
        sort_fields: list[SortField],
        status_filter: RoleStatus | None = None,
    ) -> dict[str, object]:
        """分页查询角色列表 — SPEC 9.4 / 13.2."""

        offset = (page - 1) * page_size
        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            roles, total = await repo.list_roles(
                offset=offset,
                limit=page_size,
                sort_fields=sort_fields,
                status_filter=status_filter,
            )
            return {
                "items": [_to_role_response(r) for r in roles],
                "total": total,
                "page": page,
                "page_size": page_size,
                "pages": total_pages(total, page_size),
            }

    async def update_role(
        self,
        ctx: UseCaseContext,
        role_id: UUID,
        request: RoleUpdateRequest,
    ) -> RoleResponse:
        """更新角色 — SPEC 13.2."""

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            existing = await repo.get_role_by_id(role_id)
            if existing is None:
                raise RoleNotFoundError(str(role_id))

            before_state = self._role_state(existing)

            updated = Role(
                id=existing.id,
                code=existing.code,
                display_name=request.display_name,
                description=request.description,
                status=existing.status,
                is_builtin=existing.is_builtin,
                sort_order=request.sort_order,
                created_at=existing.created_at,
                updated_at=self._clock.now(),
                created_by=existing.created_by,
                updated_by=ctx.actor_id,
            )
            await repo.save_role(updated)

            after_state = self._role_state(updated)
            diff = generate_diff(
                ROLE_FIELD_WHITELIST,
                before=before_state,
                after=after_state,
            )
            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="rbac.role.update",
                    resource_id=str(role_id),
                    resource_display_name=updated.display_name,
                    diff=diff,
                ),
            )

            await uow.commit()
            return _to_role_response(updated)

    async def enable_role(
        self,
        ctx: UseCaseContext,
        role_id: UUID,
    ) -> RoleResponse:
        """启用角色 — SPEC 13.2."""

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            existing = await repo.get_role_by_id(role_id)
            if existing is None:
                raise RoleNotFoundError(str(role_id))
            if existing.is_builtin:
                raise BuiltinRoleProtectedError(
                    f"内置角色 {existing.code} 不可禁用或启用",
                )
            if existing.status == RoleStatus.ACTIVE:
                raise RoleAlreadyActiveError(str(role_id))

            before_state = self._role_state(existing)

            updated = Role(
                id=existing.id,
                code=existing.code,
                display_name=existing.display_name,
                description=existing.description,
                status=RoleStatus.ACTIVE,
                is_builtin=existing.is_builtin,
                sort_order=existing.sort_order,
                created_at=existing.created_at,
                updated_at=self._clock.now(),
                created_by=existing.created_by,
                updated_by=ctx.actor_id,
            )
            await repo.save_role(updated)

            after_state = self._role_state(updated)
            diff = generate_diff(
                ROLE_FIELD_WHITELIST,
                before=before_state,
                after=after_state,
            )
            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="rbac.role.enable",
                    resource_id=str(role_id),
                    resource_display_name=updated.display_name,
                    diff=diff,
                ),
            )

            await uow.commit()
            return _to_role_response(updated)

    async def disable_role(
        self,
        ctx: UseCaseContext,
        role_id: UUID,
    ) -> RoleResponse:
        """禁用角色 — SPEC 13.2.

        SPEC 13.2: "系统内置角色具有明确保护规则"。
        内置角色不可禁用。
        SPEC 13.1: 被禁用角色的权限不再计入用户有效权限集。
        """

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            existing = await repo.get_role_by_id(role_id)
            if existing is None:
                raise RoleNotFoundError(str(role_id))
            if existing.is_builtin:
                raise BuiltinRoleProtectedError(
                    f"内置角色 {existing.code} 不可禁用",
                )
            if existing.status == RoleStatus.DISABLED:
                raise RoleAlreadyDisabledError(str(role_id))

            before_state = self._role_state(existing)

            updated = Role(
                id=existing.id,
                code=existing.code,
                display_name=existing.display_name,
                description=existing.description,
                status=RoleStatus.DISABLED,
                is_builtin=existing.is_builtin,
                sort_order=existing.sort_order,
                created_at=existing.created_at,
                updated_at=self._clock.now(),
                created_by=existing.created_by,
                updated_by=ctx.actor_id,
            )
            await repo.save_role(updated)

            after_state = self._role_state(updated)
            diff = generate_diff(
                ROLE_FIELD_WHITELIST,
                before=before_state,
                after=after_state,
            )
            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="rbac.role.disable",
                    resource_id=str(role_id),
                    resource_display_name=updated.display_name,
                    diff=diff,
                ),
            )

            await uow.commit()
            return _to_role_response(updated)

    async def delete_role(
        self,
        ctx: UseCaseContext,
        role_id: UUID,
    ) -> None:
        """删除角色 — SPEC 13.2.

        SPEC 13.2: "系统内置角色具有明确保护规则"。
        内置角色不可删除。有用户关联的角色不可删除。
        """

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            existing = await repo.get_role_by_id(role_id)
            if existing is None:
                raise RoleNotFoundError(str(role_id))
            if existing.is_builtin:
                raise BuiltinRoleProtectedError(
                    f"内置角色 {existing.code} 不可删除",
                )

            member_count = await repo.count_role_members(role_id)
            if member_count > 0:
                from app.modules.rbac.errors import RoleHasUsersError

                raise RoleHasUsersError(str(role_id))

            await repo.delete_role_by_id(role_id)

            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="rbac.role.delete",
                    resource_id=str(role_id),
                    resource_display_name=existing.display_name,
                ),
            )

            await uow.commit()

    # ── 权限点分配 ────────────────────────────────────────────────────────

    async def assign_permissions(
        self,
        ctx: UseCaseContext,
        role_id: UUID,
        request: AssignPermissionsRequest,
    ) -> RoleDetailResponse:
        """为角色分配权限点 — SPEC 13.2.

        全量替换角色的权限点集合。分配不存在的权限编码返回参数错误。
        """

        now = self._clock.now()

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            existing = await repo.get_role_by_id(role_id)
            if existing is None:
                raise RoleNotFoundError(str(role_id))

            # 验证所有权限编码存在 — SPEC 13.2
            requested_codes = set(request.permission_codes)
            if requested_codes:
                found = await repo.get_permission_codes(requested_codes)
                found_codes = {p.code for p in found}
                missing = requested_codes - found_codes
                if missing:
                    raise PermissionNotFoundError(
                        f"权限点不存在: {', '.join(sorted(missing))}",
                    )
                permission_ids = {p.id for p in found}
            else:
                permission_ids = set()

            # 获取旧权限列表用于审计
            old_codes = await repo.get_role_permission_codes(role_id)

            await repo.replace_role_permissions(
                role_id,
                permission_ids,
                now=now,
            )

            # 审计 — SPEC 18.2: 权限变更记录新旧权限集
            new_codes = sorted(requested_codes)
            from app.modules.audit.models import ChangeDiff, DiffField

            diff = ChangeDiff(
                fields=(
                    DiffField(
                        field_name="permission_codes",
                        old_value=sorted(old_codes) if old_codes else None,
                        new_value=new_codes if new_codes else None,
                    ),
                ),
            )
            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="rbac.role.assign_permissions",
                    resource_id=str(role_id),
                    resource_display_name=existing.display_name,
                    diff=diff,
                ),
            )

            await uow.commit()

            return _to_detail_response(
                existing,
                sorted(requested_codes),
                await repo.count_role_members(role_id),
            )

    # ── 角色成员 ──────────────────────────────────────────────────────────

    async def get_role_members(
        self,
        ctx: UseCaseContext,
        role_id: UUID,
        *,
        page: int,
        page_size: int,
    ) -> dict[str, object]:
        """查询角色成员 — SPEC 13.2."""

        offset = (page - 1) * page_size
        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)

            role = await repo.get_role_by_id(role_id)
            if role is None:
                raise RoleNotFoundError(str(role_id))

            members, total = await repo.list_role_members(
                role_id,
                offset=offset,
                limit=page_size,
            )
            return {
                "items": [_to_member_response(m) for m in members],
                "total": total,
                "page": page,
                "page_size": page_size,
                "pages": total_pages(total, page_size),
            }

    # ── 用户角色 ──────────────────────────────────────────────────────────

    async def assign_user_roles(
        self,
        ctx: UseCaseContext,
        user_id: UUID,
        request: AssignUserRolesRequest,
    ) -> dict[str, object]:
        """为用户分配角色（全量替换）— SPEC 13.2.

        通过 identity 模块 Port 校验用户存在性。
        """

        now = self._clock.now()

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)
            user_auth = self._create_user_auth_port(uow.session)

            # 校验用户存在性 — SPEC 5.2 跨模块
            status = await user_auth.get_status_by_id(user_id)
            if status is None:
                from app.modules.identity.errors import UserNotFoundError

                raise UserNotFoundError(str(user_id))

            # 解析目标角色编码为角色实体 — 通过 Repository Port
            requested_codes = set(request.role_codes)
            target_roles = await repo.get_roles_by_codes(requested_codes)
            found_codes = {r.code for r in target_roles}
            missing_codes = requested_codes - found_codes
            if missing_codes:
                raise RoleNotFoundError(
                    f"角色不存在: {', '.join(sorted(missing_codes))}",
                )

            target_role_ids = {r.id for r in target_roles}

            # 获取现有分配
            existing_assignments = await repo.list_user_roles(user_id)
            existing_role_ids = {a.role_id for a in existing_assignments}

            # 查询现有角色的编码（用于审计 diff）
            existing_roles = await repo.get_roles_by_ids(existing_role_ids)
            existing_code_map = {r.id: r.code for r in existing_roles}

            # 计算差异
            to_add = target_role_ids - existing_role_ids
            to_remove = existing_role_ids - target_role_ids

            # 添加新角色分配
            for rid in to_add:
                await repo.add_user_role(
                    user_id,
                    rid,
                    now=now,
                    created_by=ctx.actor_id,
                )

            # 移除多余角色分配
            for rid in to_remove:
                await repo.remove_user_role(user_id, rid)

            # 审计 — SPEC 18.2: 用户角色变更写审计
            if to_add or to_remove:
                from app.modules.audit.models import ChangeDiff, DiffField

                old_codes = sorted(existing_code_map[rid] for rid in existing_role_ids)
                new_codes = sorted(requested_codes)

                diff = ChangeDiff(
                    fields=(
                        DiffField(
                            field_name="assigned_roles",
                            old_value=old_codes if old_codes else None,
                            new_value=new_codes if new_codes else None,
                        ),
                    ),
                )
                await audit.record_audit(
                    self._make_audit_entry(
                        ctx,
                        action="rbac.user_role.assign",
                        resource_id=str(user_id),
                        resource_display_name=str(user_id),
                        diff=diff,
                    ),
                )

            await uow.commit()

            # 返回更新后的角色分配
            updated = await repo.list_user_roles(user_id)
            return {
                "user_id": str(user_id),
                "role_ids": [str(a.role_id) for a in updated],
                "added_count": len(to_add),
                "removed_count": len(to_remove),
            }

    async def remove_user_role(
        self,
        ctx: UseCaseContext,
        user_id: UUID,
        role_id: UUID,
    ) -> None:
        """移除用户角色 — SPEC 13.2."""

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            removed = await repo.remove_user_role(user_id, role_id)
            if not removed:
                raise UserRoleNotAssignedError(str(user_id))

            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="rbac.user_role.remove",
                    resource_id=str(user_id),
                    resource_display_name=str(user_id),
                ),
            )

            await uow.commit()

    async def get_user_roles(
        self,
        ctx: UseCaseContext,
        user_id: UUID,
    ) -> dict[str, object]:
        """查询用户角色列表 — SPEC 13.2.

        通过 identity 模块 Port 校验用户存在性。
        """

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            user_auth = self._create_user_auth_port(uow.session)

            # 校验用户存在性 — SPEC 5.2 跨模块
            status = await user_auth.get_status_by_id(user_id)
            if status is None:
                from app.modules.identity.errors import UserNotFoundError

                raise UserNotFoundError(str(user_id))

            assignments = await repo.list_user_roles(user_id)
            return {
                "user_id": str(user_id),
                "role_ids": [str(a.role_id) for a in assignments],
            }


# ── 领域实体 → 响应 Schema 转换 ──────────────────────────────────────────────


def _to_role_response(role: Role) -> RoleResponse:
    """角色领域实体 → 响应 Schema."""

    return RoleResponse(
        id=role.id,
        code=role.code,
        display_name=role.display_name,
        description=role.description,
        status=role.status.value,
        is_builtin=role.is_builtin,
        sort_order=role.sort_order,
        created_at=role.created_at,
        updated_at=role.updated_at,
    )


def _to_detail_response(
    role: Role,
    permission_codes: list[str],
    member_count: int,
) -> RoleDetailResponse:
    """角色领域实体 → 详情响应 Schema（含权限和成员数量）."""

    return RoleDetailResponse(
        id=role.id,
        code=role.code,
        display_name=role.display_name,
        description=role.description,
        status=role.status.value,
        is_builtin=role.is_builtin,
        sort_order=role.sort_order,
        permission_codes=permission_codes,
        member_count=member_count,
        created_at=role.created_at,
        updated_at=role.updated_at,
    )


def _to_member_response(
    assignment: object,
) -> RoleMemberResponse:
    """角色分配记录 → 成员响应 Schema."""

    from app.modules.rbac.models import RoleAssignment

    assert isinstance(assignment, RoleAssignment)
    return RoleMemberResponse(
        user_id=assignment.user_id,
        role_id=assignment.role_id,
        created_at=assignment.created_at,
        created_by=assignment.created_by,
    )
