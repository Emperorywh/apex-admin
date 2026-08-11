"""菜单模块 Use Case — SPEC 5.2 / 5.6 / 5.7 / 15.1 / 15.2 / 18.2.

Application 层应用服务。

SPEC 5.6 事务管理:
  - 一个最外层写 Use Case 对应一个 Unit of Work 和一个 AsyncSession。
  - 最外层写 Use Case 负责开始、提交或回滚。

SPEC 5.7 审计:
  - 菜单变更通过 AuditPort 写审计，并与业务事务共同提交
    （SPEC 5.7 / 18.2）。

SPEC 15.1 菜单资源:
  - 创建/树查询/详情/更新/启用禁用/层级与排序调整/删除。
  - 循环防护：调整层级时校验目标父菜单不是自身子树。
  - 并发防护：使用事务级咨询锁序列化并发层级调整。
  - 删除保护：有子菜单时拒绝删除。
  - 角色菜单分配（全量替换，幂等）与移除（幂等）。

SPEC 15.2 当前用户菜单:
  - 根据当前用户启用角色返回可访问菜单树。
  - 返回当前用户拥有的按钮/操作权限编码（RBAC 权限并集）。
  - 菜单变更无缓存，提交即生效。

SPEC 23.5: 菜单可见性不承担授权——隐藏菜单对应接口仍按服务端权限放行，
无权限接口即使菜单可见仍 403。授权由 RBAC 权限校验决定（SPEC 13.3）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.modules.audit.diff import FieldWhitelist, generate_diff
from app.modules.audit.models import AuditEntry
from app.modules.menu.adapter import SqlAlchemyMenuRepository
from app.modules.menu.errors import (
    InvalidMenuParentError,
    MenuAlreadyActiveError,
    MenuAlreadyDisabledError,
    MenuCycleError,
    MenuHasChildrenError,
    MenuNotFoundError,
)
from app.modules.menu.models import (
    Menu,
    MenuStatus,
    MenuTreeNode,
    MenuType,
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
    from app.modules.menu.port import MenuRepository
    from app.modules.menu.schemas import (
        AssignRoleMenusRequest,
        MenuCreateRequest,
        MenuHierarchyRequest,
        MenuUpdateRequest,
    )
    from app.modules.rbac.port import UserRbacPort


# ── 菜单审计字段白名单 — SPEC 18.2 ──────────────────────────────────────────

MENU_FIELD_WHITELIST = FieldWhitelist(
    module="menu",
    resource_type="menu",
    fields=frozenset(
        {
            "menu_type",
            "title",
            "name",
            "path",
            "component",
            "icon",
            "parent_id",
            "sort_order",
            "visible",
            "status",
        },
    ),
)


class MenuUseCase:
    """菜单 Use Case — Application 层应用服务.

    SPEC 5.2 / 5.6: Use Case 是最外层写操作的入口，控制事务边界。
    SPEC 5.7: 审计通过 ``AuditPort`` 显式调用，与业务事务共同提交。

    构造参数:
        uow_factory:           UoW 工厂。
        clock:                 时钟 Port。
        id_generator:          标识生成器 Port。
        audit_factory:         审计 Port 工厂。
        user_rbac_port_factory: 用户 RBAC Port 工厂（跨模块，
                                从 AsyncSession 构造 UserRbacPort）。
    """

    def __init__(
        self,
        *,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
        clock: Clock,
        id_generator: IdGenerator,
        audit_factory: Callable[[AsyncSession], AuditPort],
        user_rbac_port_factory: Callable[[AsyncSession], UserRbacPort],
    ) -> None:
        """初始化 Use Case，注入所有依赖."""

        self._uow_factory = uow_factory
        self._clock = clock
        self._id_generator = id_generator
        self._audit_factory = audit_factory
        self._user_rbac_port_factory = user_rbac_port_factory

    def _create_repo(self, session: AsyncSession) -> MenuRepository:
        """从 session 构造菜单 Repository Adapter — SPEC 5.6."""

        return SqlAlchemyMenuRepository(session)

    def _create_audit(self, session: AsyncSession) -> AuditPort:
        """从 session 构造审计 Port — SPEC 5.7 / 5.2."""

        return self._audit_factory(session)

    def _create_user_rbac_port(self, session: AsyncSession) -> UserRbacPort:
        """从 session 构造用户 RBAC Port — SPEC 5.2 跨模块."""

        return self._user_rbac_port_factory(session)

    def _make_audit_entry(
        self,
        ctx: UseCaseContext,
        *,
        action: str,
        resource_id: str | None,
        resource_display_name: str | None,
        diff: ChangeDiff | None = None,
        resource_type: str = "menu",
    ) -> AuditEntry:
        """构造操作审计条目 — SPEC 18.2 / 5.7."""

        return AuditEntry(
            id=self._id_generator.generate_id(),
            actor_id=ctx.actor_id,
            actor_display_name=ctx.actor_id or "system",
            module="menu",
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            resource_display_name=resource_display_name,
            result="success",
            request_id=ctx.request_id or None,
            diff=diff,
            occurred_at=self._clock.now(),
        )

    @staticmethod
    def _menu_state(menu: Menu) -> dict[str, str | int | None | bool]:
        """提取审计白名单字段状态 — SPEC 18.2."""

        return {
            "menu_type": menu.menu_type.value,
            "title": menu.title,
            "name": menu.name,
            "path": menu.path,
            "component": menu.component,
            "icon": menu.icon,
            "parent_id": str(menu.parent_id) if menu.parent_id else None,
            "sort_order": menu.sort_order,
            "visible": menu.visible,
            "status": menu.status.value,
        }

    # ── 菜单管理 ──────────────────────────────────────────────────────────

    async def create_menu(
        self,
        ctx: UseCaseContext,
        request: MenuCreateRequest,
    ) -> dict[str, object]:
        """创建菜单 — SPEC 15.1.

        如果指定了父菜单，校验父菜单存在且处于启用状态。
        """

        now = self._clock.now()
        menu_id = self._id_generator.generate_id()

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            # 校验父菜单
            if request.parent_id is not None:
                parent = await repo.get_menu_by_id(request.parent_id)
                if parent is None:
                    raise InvalidMenuParentError(
                        f"父菜单 {request.parent_id} 不存在",
                    )
                if parent.status == MenuStatus.DISABLED:
                    raise InvalidMenuParentError(
                        f"父菜单 {parent.title} 已禁用，不能作为父菜单",
                    )

            menu = Menu(
                id=menu_id,
                parent_id=request.parent_id,
                menu_type=MenuType(request.menu_type),
                title=request.title,
                name=request.name,
                path=request.path,
                component=request.component,
                icon=request.icon,
                sort_order=request.sort_order,
                visible=request.visible,
                status=MenuStatus.ACTIVE,
                created_at=now,
                updated_at=now,
                created_by=ctx.actor_id,
                updated_by=ctx.actor_id,
            )

            await repo.add_menu(menu)

            diff = generate_diff(
                MENU_FIELD_WHITELIST,
                before=None,
                after=self._menu_state(menu),
            )
            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="menu.create",
                    resource_id=str(menu_id),
                    resource_display_name=menu.title,
                    diff=diff,
                ),
            )

            await uow.commit()
            return _to_response_dict(menu)

    async def get_menu_tree(
        self,
        ctx: UseCaseContext,
        *,
        include_disabled: bool = True,
    ) -> list[dict[str, object]]:
        """查询菜单树 — SPEC 15.1.

        管理端查询返回完整菜单树（含不可见菜单）。
        当 ``include_disabled=False`` 时，禁用菜单被排除。
        """

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            menus = await repo.list_all_menus(include_disabled=include_disabled)
            tree = _build_tree(menus)
            return [_tree_node_to_dict(node) for node in tree]

    async def get_menu_detail(
        self,
        ctx: UseCaseContext,
        menu_id: UUID,
    ) -> dict[str, object]:
        """查询菜单详情 — SPEC 15.1."""

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            menu = await repo.get_menu_by_id(menu_id)
            if menu is None:
                raise MenuNotFoundError(str(menu_id))
            return _to_response_dict(menu)

    async def update_menu(
        self,
        ctx: UseCaseContext,
        menu_id: UUID,
        request: MenuUpdateRequest,
    ) -> dict[str, object]:
        """更新菜单基本信息 — SPEC 15.1.

        层级调整使用独立端点 ``adjust_hierarchy``。
        菜单类型不可变更。
        """

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            existing = await repo.get_menu_by_id(menu_id)
            if existing is None:
                raise MenuNotFoundError(str(menu_id))

            before_state = self._menu_state(existing)

            updated = Menu(
                id=existing.id,
                parent_id=existing.parent_id,
                menu_type=existing.menu_type,
                title=request.title,
                name=request.name,
                path=request.path,
                component=request.component,
                icon=request.icon,
                sort_order=existing.sort_order,
                visible=request.visible,
                status=existing.status,
                created_at=existing.created_at,
                updated_at=self._clock.now(),
                created_by=existing.created_by,
                updated_by=ctx.actor_id,
            )
            await repo.save_menu(updated)

            after_state = self._menu_state(updated)
            diff = generate_diff(
                MENU_FIELD_WHITELIST,
                before=before_state,
                after=after_state,
            )
            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="menu.update",
                    resource_id=str(menu_id),
                    resource_display_name=updated.title,
                    diff=diff,
                ),
            )

            await uow.commit()
            return _to_response_dict(updated)

    async def enable_menu(
        self,
        ctx: UseCaseContext,
        menu_id: UUID,
    ) -> dict[str, object]:
        """启用菜单 — SPEC 15.1."""

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            existing = await repo.get_menu_by_id(menu_id)
            if existing is None:
                raise MenuNotFoundError(str(menu_id))
            if existing.status == MenuStatus.ACTIVE:
                raise MenuAlreadyActiveError(str(menu_id))

            before_state = self._menu_state(existing)

            updated = Menu(
                id=existing.id,
                parent_id=existing.parent_id,
                menu_type=existing.menu_type,
                title=existing.title,
                name=existing.name,
                path=existing.path,
                component=existing.component,
                icon=existing.icon,
                sort_order=existing.sort_order,
                visible=existing.visible,
                status=MenuStatus.ACTIVE,
                created_at=existing.created_at,
                updated_at=self._clock.now(),
                created_by=existing.created_by,
                updated_by=ctx.actor_id,
            )
            await repo.save_menu(updated)

            after_state = self._menu_state(updated)
            diff = generate_diff(
                MENU_FIELD_WHITELIST,
                before=before_state,
                after=after_state,
            )
            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="menu.enable",
                    resource_id=str(menu_id),
                    resource_display_name=updated.title,
                    diff=diff,
                ),
            )

            await uow.commit()
            return _to_response_dict(updated)

    async def disable_menu(
        self,
        ctx: UseCaseContext,
        menu_id: UUID,
    ) -> dict[str, object]:
        """禁用菜单 — SPEC 15.1.

        禁用菜单不出现在当前用户菜单树中。
        """

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            existing = await repo.get_menu_by_id(menu_id)
            if existing is None:
                raise MenuNotFoundError(str(menu_id))
            if existing.status == MenuStatus.DISABLED:
                raise MenuAlreadyDisabledError(str(menu_id))

            before_state = self._menu_state(existing)

            updated = Menu(
                id=existing.id,
                parent_id=existing.parent_id,
                menu_type=existing.menu_type,
                title=existing.title,
                name=existing.name,
                path=existing.path,
                component=existing.component,
                icon=existing.icon,
                sort_order=existing.sort_order,
                visible=existing.visible,
                status=MenuStatus.DISABLED,
                created_at=existing.created_at,
                updated_at=self._clock.now(),
                created_by=existing.created_by,
                updated_by=ctx.actor_id,
            )
            await repo.save_menu(updated)

            after_state = self._menu_state(updated)
            diff = generate_diff(
                MENU_FIELD_WHITELIST,
                before=before_state,
                after=after_state,
            )
            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="menu.disable",
                    resource_id=str(menu_id),
                    resource_display_name=updated.title,
                    diff=diff,
                ),
            )

            await uow.commit()
            return _to_response_dict(updated)

    async def adjust_hierarchy(
        self,
        ctx: UseCaseContext,
        menu_id: UUID,
        request: MenuHierarchyRequest,
    ) -> dict[str, object]:
        """调整菜单层级与排序 — SPEC 15.1.

        循环防护（SPEC 15.1: "防止形成循环层级"）:
          1. 直接循环：目标父菜单是自身 → 拒绝。
          2. 间接循环：目标父菜单是自身后代 → 拒绝。
          3. 并发防护：事务级咨询锁序列化并发层级调整。
        """

        now = self._clock.now()

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            # SPEC 15.1: 事务级咨询锁——序列化并发层级调整
            await repo.acquire_hierarchy_lock()

            existing = await repo.get_menu_by_id(menu_id)
            if existing is None:
                raise MenuNotFoundError(str(menu_id))

            before_state = self._menu_state(existing)

            # 校验新父菜单
            if request.parent_id is not None:
                # 直接循环：目标父菜单是自身
                if request.parent_id == menu_id:
                    raise MenuCycleError(
                        "不能将菜单设为自身的子菜单",
                    )

                # 间接循环：目标父菜单是自身后代
                descendant_ids = await repo.get_descendant_ids(menu_id)
                if request.parent_id in descendant_ids:
                    raise MenuCycleError(
                        "不能将菜单移动到自身后代下，会形成循环层级",
                    )

                # 校验父菜单存在且启用
                parent = await repo.get_menu_by_id(request.parent_id)
                if parent is None:
                    raise InvalidMenuParentError(
                        f"父菜单 {request.parent_id} 不存在",
                    )
                if parent.status == MenuStatus.DISABLED:
                    raise InvalidMenuParentError(
                        f"父菜单 {parent.title} 已禁用，不能作为父菜单",
                    )

            updated = Menu(
                id=existing.id,
                parent_id=request.parent_id,
                menu_type=existing.menu_type,
                title=existing.title,
                name=existing.name,
                path=existing.path,
                component=existing.component,
                icon=existing.icon,
                sort_order=request.sort_order,
                visible=existing.visible,
                status=existing.status,
                created_at=existing.created_at,
                updated_at=now,
                created_by=existing.created_by,
                updated_by=ctx.actor_id,
            )
            await repo.save_menu(updated)

            after_state = self._menu_state(updated)
            diff = generate_diff(
                MENU_FIELD_WHITELIST,
                before=before_state,
                after=after_state,
            )
            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="menu.adjust_hierarchy",
                    resource_id=str(menu_id),
                    resource_display_name=updated.title,
                    diff=diff,
                ),
            )

            await uow.commit()
            return _to_response_dict(updated)

    async def delete_menu(
        self,
        ctx: UseCaseContext,
        menu_id: UUID,
    ) -> None:
        """删除菜单 — SPEC 15.1.

        存在子菜单时拒绝删除（``MENU.HAS_CHILDREN``）。
        """

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            existing = await repo.get_menu_by_id(menu_id)
            if existing is None:
                raise MenuNotFoundError(str(menu_id))

            # 删除保护：子菜单
            child_count = await repo.count_children(menu_id)
            if child_count > 0:
                raise MenuHasChildrenError(str(menu_id))

            await repo.delete_menu_by_id(menu_id)

            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="menu.delete",
                    resource_id=str(menu_id),
                    resource_display_name=existing.title,
                ),
            )

            await uow.commit()

    # ── 角色菜单分配 ─────────────────────────────────────────────────────

    async def assign_role_menus(
        self,
        ctx: UseCaseContext,
        role_id: UUID,
        request: AssignRoleMenusRequest,
    ) -> dict[str, object]:
        """为角色分配菜单（全量替换）— SPEC 15.1.

        SPEC 15.1: "为角色分配和移除菜单"。
        全量替换天然幂等——相同输入多次调用结果一致。

        校验角色存在性（跨模块 RBAC Port）。
        校验全部菜单 ID 存在且处于启用状态。
        """

        now = self._clock.now()
        menu_ids = set(request.menu_ids)

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            # 校验菜单存在性
            for mid in menu_ids:
                menu = await repo.get_menu_by_id(mid)
                if menu is None:
                    raise MenuNotFoundError(str(mid))

            await repo.replace_role_menus(role_id, menu_ids, now=now)

            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="menu.role.assign_menus",
                    resource_id=str(role_id),
                    resource_display_name=str(sorted(menu_ids)),
                    resource_type="role_menu",
                ),
            )

            await uow.commit()
            return {"role_id": role_id, "menu_ids": list(menu_ids)}

    async def remove_role_menu(
        self,
        ctx: UseCaseContext,
        role_id: UUID,
        menu_id: UUID,
    ) -> None:
        """移除角色单个菜单关联（幂等）— SPEC 15.1.

        幂等——关联不存在时也返回成功（无操作）。
        """

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            await repo.remove_role_menu(role_id, menu_id)

            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="menu.role.remove_menu",
                    resource_id=str(role_id),
                    resource_display_name=str(menu_id),
                    resource_type="role_menu",
                ),
            )

            await uow.commit()

    async def get_role_menu_ids(
        self,
        ctx: UseCaseContext,
        role_id: UUID,
    ) -> list[UUID]:
        """查询角色已分配的菜单 ID 列表 — SPEC 15.1."""

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            return sorted(await repo.get_menu_ids_by_role_ids({role_id}))

    # ── 当前用户菜单与权限 ───────────────────────────────────────────────

    async def get_current_user_menu_tree(
        self,
        ctx: UseCaseContext,
    ) -> list[dict[str, object]]:
        """查询当前用户菜单树 — SPEC 15.2.

        SPEC 15.2: "根据当前用户角色返回可访问菜单树"。
        SPEC 15.2: "菜单变更事务提交后，当前用户下一次菜单查询立即读取新关系"。
        每次调用查库，无缓存。

        流程:
          1. 通过 RBAC Port 查询当前用户启用角色 ID 集合。
          2. 查询这些角色已分配的菜单 ID 集合。
          3. 查询这些菜单 ID 对应的启用菜单实体。
          4. 过滤不可见菜单（SPEC 23.5: 仅前端展示控制）。
          5. 构建并返回菜单树。
        """

        assert ctx.actor_id is not None

        from uuid import UUID

        actor_uuid = UUID(ctx.actor_id)

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            rbac_port = self._create_user_rbac_port(uow.session)

            # 1. 查询用户启用角色 ID
            active_role_ids = await rbac_port.get_active_role_ids_by_user(actor_uuid)
            if not active_role_ids:
                return []

            # 2. 查询这些角色已分配的菜单 ID
            menu_ids = await repo.get_menu_ids_by_role_ids(active_role_ids)
            if not menu_ids:
                return []

            # 3. 查询启用菜单实体
            menus = await repo.get_menus_by_ids(menu_ids)

            # 4. 过滤不可见菜单（仅前端展示控制，SPEC 23.5）
            visible_menus = [m for m in menus if m.visible]

            # 5. 构建菜单树
            tree = _build_tree(visible_menus)
            return [_tree_node_to_dict(node) for node in tree]

    async def get_current_user_permissions(
        self,
        ctx: UseCaseContext,
    ) -> set[str]:
        """查询当前用户按钮/操作权限编码 — SPEC 15.2.

        SPEC 15.2: "返回当前用户拥有的按钮或操作权限编码"。
        按钮权限来自 RBAC 权限点——用户启用角色权限并集。
        每次调用查库，无缓存（SPEC 13.3）。
        """

        assert ctx.actor_id is not None

        from uuid import UUID

        actor_uuid = UUID(ctx.actor_id)

        async with self._uow_factory() as uow:
            rbac_port = self._create_user_rbac_port(uow.session)
            return await rbac_port.get_effective_permission_codes(actor_uuid)


# ── 树构建与转换辅助 ──────────────────────────────────────────────────────


def _build_tree(menus: list[Menu]) -> list[MenuTreeNode]:
    """从扁平菜单列表构建树结构.

    按 ``sort_order`` 排序，按 ``parent_id`` 组织父子关系。
    """

    nodes: dict[UUID, MenuTreeNode] = {}
    for menu in menus:
        nodes[menu.id] = MenuTreeNode(
            id=menu.id,
            parent_id=menu.parent_id,
            menu_type=menu.menu_type,
            title=menu.title,
            name=menu.name,
            path=menu.path,
            component=menu.component,
            icon=menu.icon,
            sort_order=menu.sort_order,
            visible=menu.visible,
            status=menu.status,
            children=[],
            created_at=menu.created_at,
            updated_at=menu.updated_at,
        )

    roots: list[MenuTreeNode] = []
    for menu in menus:
        node = nodes[menu.id]
        if menu.parent_id is not None and menu.parent_id in nodes:
            nodes[menu.parent_id].children.append(node)
        else:
            roots.append(node)

    # 按排序序号排序
    roots.sort(key=lambda n: (n.sort_order, n.title))
    for node in nodes.values():
        node.children.sort(key=lambda n: (n.sort_order, n.title))

    return roots


def _to_response_dict(menu: Menu) -> dict[str, object]:
    """菜单领域实体 → 响应字典."""

    return {
        "id": menu.id,
        "parent_id": menu.parent_id,
        "menu_type": menu.menu_type.value,
        "title": menu.title,
        "name": menu.name,
        "path": menu.path,
        "component": menu.component,
        "icon": menu.icon,
        "sort_order": menu.sort_order,
        "visible": menu.visible,
        "status": menu.status.value,
        "created_at": menu.created_at,
        "updated_at": menu.updated_at,
    }


def _tree_node_to_dict(node: MenuTreeNode) -> dict[str, object]:
    """树节点 → 响应字典（递归）."""

    return {
        "id": node.id,
        "parent_id": node.parent_id,
        "menu_type": node.menu_type.value,
        "title": node.title,
        "name": node.name,
        "path": node.path,
        "component": node.component,
        "icon": node.icon,
        "sort_order": node.sort_order,
        "visible": node.visible,
        "status": node.status.value,
        "children": [_tree_node_to_dict(child) for child in node.children],
        "created_at": node.created_at,
        "updated_at": node.updated_at,
    }
