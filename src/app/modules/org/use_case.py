"""组织模块 Use Case — SPEC 5.2 / 5.6 / 5.7 / 14.1 / 14.2 / 14.3 / 18.2.

Application 层应用服务。

SPEC 5.6 事务管理:
  - 一个最外层写 Use Case 对应一个 Unit of Work 和一个 AsyncSession。
  - 最外层写 Use Case 负责开始、提交或回滚。

SPEC 5.7 审计:
  - 部门/岗位变更通过 AuditPort 写审计，并与业务事务共同提交
    （SPEC 5.7 / 18.2）。

SPEC 14.1 部门管理:
  - 创建/树查询/详情/更新/启用禁用/层级与排序调整/负责人设置/删除。
  - 循环防护：调整层级时校验目标父部门不是自身子树。
  - 并发防护：使用事务级咨询锁序列化并发层级调整。
  - 删除保护：有子部门或用户时拒绝删除。

SPEC 14.2 岗位管理:
  - 创建/查询/更新/启用禁用/删除。
  - 为用户分配岗位（幂等）、移除用户岗位。
  - 岗位不直接替代角色和权限。

SPEC 14.3 用户组织关系:
  - 用户主部门关系设置与解除。
  - 用户离职/禁用时组织关系按规则处理（事件处理器）。

Use Case 在每个写方法中:
  1. 创建新 UoW。
  2. 从 UoW 的 session 构造 Repository Adapter 和审计 Adapter。
  3. 执行业务逻辑。
  4. 显式调用 AuditPort 写审计（同事务提交）。
  5. 提交事务（异常时 ``__aexit__`` 自动回滚）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.modules.audit.diff import FieldWhitelist, generate_diff
from app.modules.audit.models import AuditEntry
from app.modules.org.adapter import SqlAlchemyOrgRepository
from app.modules.org.errors import (
    DepartmentAlreadyActiveError,
    DepartmentAlreadyDisabledError,
    DepartmentCycleError,
    DepartmentDisabledError,
    DepartmentHasChildrenError,
    DepartmentHasUsersError,
    DepartmentNotFoundError,
    InvalidParentError,
    PostAlreadyActiveError,
    PostAlreadyDisabledError,
    PostDisabledError,
    PostHasUsersError,
    PostNotFoundError,
    UserDepartmentNotFoundError,
    UserPostNotFoundError,
)
from app.modules.org.models import (
    Department,
    DepartmentStatus,
    DepartmentTreeNode,
    Post,
    PostStatus,
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
    from app.modules.org.port import OrgRepository
    from app.modules.org.schemas import (
        DepartmentCreateRequest,
        DepartmentHierarchyRequest,
        DepartmentLeaderRequest,
        DepartmentUpdateRequest,
        PostCreateRequest,
        PostUpdateRequest,
    )


# ── 部门审计字段白名单 — SPEC 18.2 ──────────────────────────────────────────

DEPARTMENT_FIELD_WHITELIST = FieldWhitelist(
    module="org",
    resource_type="department",
    fields=frozenset(
        {
            "display_name",
            "description",
            "parent_id",
            "status",
            "sort_order",
            "leader_id",
        },
    ),
)

# ── 岗位审计字段白名单 — SPEC 18.2 ──────────────────────────────────────────

POST_FIELD_WHITELIST = FieldWhitelist(
    module="org",
    resource_type="post",
    fields=frozenset(
        {
            "display_name",
            "description",
            "status",
            "sort_order",
        },
    ),
)


class OrgUseCase:
    """组织 Use Case — Application 层应用服务.

    SPEC 5.2 / 5.6: Use Case 是最外层写操作的入口，控制事务边界。
    SPEC 5.7: 审计通过 ``AuditPort`` 显式调用，与业务事务共同提交。

    构造参数:
        uow_factory:          UoW 工厂。
        clock:                时钟 Port。
        id_generator:         标识生成器 Port。
        audit_factory:        审计 Port 工厂。
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

    def _create_repo(self, session: AsyncSession) -> OrgRepository:
        """从 session 构造组织 Repository Adapter — SPEC 5.6."""

        return SqlAlchemyOrgRepository(session)

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
        resource_type: str = "department",
    ) -> AuditEntry:
        """构造操作审计条目 — SPEC 18.2 / 5.7."""

        return AuditEntry(
            id=self._id_generator.generate_id(),
            actor_id=ctx.actor_id,
            actor_display_name=ctx.actor_id or "system",
            module="org",
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
    def _dept_state(dept: Department) -> dict[str, str | int | None]:
        """提取审计白名单字段状态 — SPEC 18.2."""

        return {
            "display_name": dept.display_name,
            "description": dept.description,
            "parent_id": str(dept.parent_id) if dept.parent_id else None,
            "status": dept.status.value,
            "sort_order": dept.sort_order,
            "leader_id": str(dept.leader_id) if dept.leader_id else None,
        }

    @staticmethod
    def _post_state(post: Post) -> dict[str, str | int | None]:
        """提取岗位审计白名单字段状态 — SPEC 18.2."""

        return {
            "display_name": post.display_name,
            "description": post.description,
            "status": post.status.value,
            "sort_order": post.sort_order,
        }

    # ── 部门管理 ──────────────────────────────────────────────────────────

    async def create_department(
        self,
        ctx: UseCaseContext,
        request: DepartmentCreateRequest,
    ) -> dict[str, object]:
        """创建部门 — SPEC 14.1.

        如果指定了父部门，校验父部门存在且处于启用状态。
        如果指定了负责人，通过 identity 模块 Port 校验用户存在性。
        """

        now = self._clock.now()
        dept_id = self._id_generator.generate_id()

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            # 校验父部门
            if request.parent_id is not None:
                parent = await repo.get_department_by_id(request.parent_id)
                if parent is None:
                    raise InvalidParentError(
                        f"父部门 {request.parent_id} 不存在",
                    )
                if parent.status == DepartmentStatus.DISABLED:
                    raise InvalidParentError(
                        f"父部门 {parent.code} 已禁用，不能作为父部门",
                    )

            # 校验负责人存在性 — SPEC 5.2 跨模块
            if request.leader_id is not None:
                user_auth = self._create_user_auth_port(uow.session)
                status = await user_auth.get_status_by_id(request.leader_id)
                if status is None:
                    from app.modules.identity.errors import UserNotFoundError

                    raise UserNotFoundError(str(request.leader_id))

            department = Department(
                id=dept_id,
                code=request.code,
                display_name=request.display_name,
                description=request.description,
                parent_id=request.parent_id,
                status=DepartmentStatus.ACTIVE,
                sort_order=request.sort_order,
                leader_id=request.leader_id,
                created_at=now,
                updated_at=now,
                created_by=ctx.actor_id,
                updated_by=ctx.actor_id,
            )

            await repo.add_department(department)

            diff = generate_diff(
                DEPARTMENT_FIELD_WHITELIST,
                before=None,
                after=self._dept_state(department),
            )
            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="org.dept.create",
                    resource_id=str(dept_id),
                    resource_display_name=department.display_name,
                    diff=diff,
                ),
            )

            await uow.commit()
            return _to_response_dict(department)

    async def get_department_tree(
        self,
        ctx: UseCaseContext,
        *,
        include_disabled: bool = True,
    ) -> list[dict[str, object]]:
        """查询部门树 — SPEC 14.1.

        返回完整的部门树结构。默认包含禁用部门（管理员需要看到完整结构）。
        当 ``include_disabled=False`` 时，禁用部门及其子树被排除。

        SPEC 14.1: "部门禁用后树查询可见性符合文档规则"。
        """

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            departments = await repo.list_all_departments(
                include_disabled=include_disabled,
            )
            tree = _build_tree(departments)
            return [_tree_node_to_dict(node) for node in tree]

    async def get_department_detail(
        self,
        ctx: UseCaseContext,
        department_id: UUID,
    ) -> dict[str, object]:
        """查询部门详情（含子部门数量）— SPEC 14.1."""

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            dept = await repo.get_department_by_id(department_id)
            if dept is None:
                raise DepartmentNotFoundError(str(department_id))

            child_count = await repo.count_children(department_id)
            return _to_detail_dict(dept, child_count)

    async def update_department(
        self,
        ctx: UseCaseContext,
        department_id: UUID,
        request: DepartmentUpdateRequest,
    ) -> dict[str, object]:
        """更新部门基本信息 — SPEC 14.1.

        层级调整使用独立端点 ``adjust_hierarchy``。
        """

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            existing = await repo.get_department_by_id(department_id)
            if existing is None:
                raise DepartmentNotFoundError(str(department_id))

            before_state = self._dept_state(existing)

            updated = Department(
                id=existing.id,
                code=existing.code,
                display_name=request.display_name,
                description=request.description,
                parent_id=existing.parent_id,
                status=existing.status,
                sort_order=existing.sort_order,
                leader_id=existing.leader_id,
                created_at=existing.created_at,
                updated_at=self._clock.now(),
                created_by=existing.created_by,
                updated_by=ctx.actor_id,
            )
            await repo.save_department(updated)

            after_state = self._dept_state(updated)
            diff = generate_diff(
                DEPARTMENT_FIELD_WHITELIST,
                before=before_state,
                after=after_state,
            )
            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="org.dept.update",
                    resource_id=str(department_id),
                    resource_display_name=updated.display_name,
                    diff=diff,
                ),
            )

            await uow.commit()
            return _to_response_dict(updated)

    async def enable_department(
        self,
        ctx: UseCaseContext,
        department_id: UUID,
    ) -> dict[str, object]:
        """启用部门 — SPEC 14.1."""

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            existing = await repo.get_department_by_id(department_id)
            if existing is None:
                raise DepartmentNotFoundError(str(department_id))
            if existing.status == DepartmentStatus.ACTIVE:
                raise DepartmentAlreadyActiveError(str(department_id))

            before_state = self._dept_state(existing)

            updated = Department(
                id=existing.id,
                code=existing.code,
                display_name=existing.display_name,
                description=existing.description,
                parent_id=existing.parent_id,
                status=DepartmentStatus.ACTIVE,
                sort_order=existing.sort_order,
                leader_id=existing.leader_id,
                created_at=existing.created_at,
                updated_at=self._clock.now(),
                created_by=existing.created_by,
                updated_by=ctx.actor_id,
            )
            await repo.save_department(updated)

            after_state = self._dept_state(updated)
            diff = generate_diff(
                DEPARTMENT_FIELD_WHITELIST,
                before=before_state,
                after=after_state,
            )
            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="org.dept.enable",
                    resource_id=str(department_id),
                    resource_display_name=updated.display_name,
                    diff=diff,
                ),
            )

            await uow.commit()
            return _to_response_dict(updated)

    async def disable_department(
        self,
        ctx: UseCaseContext,
        department_id: UUID,
    ) -> dict[str, object]:
        """禁用部门 — SPEC 14.1.

        SPEC 14.1: "部门禁用后树查询可见性符合文档规则"。
        禁用部门在树查询中标记为禁用但默认仍可见（管理员需要看到完整结构）。
        """

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            existing = await repo.get_department_by_id(department_id)
            if existing is None:
                raise DepartmentNotFoundError(str(department_id))
            if existing.status == DepartmentStatus.DISABLED:
                raise DepartmentAlreadyDisabledError(str(department_id))

            before_state = self._dept_state(existing)

            updated = Department(
                id=existing.id,
                code=existing.code,
                display_name=existing.display_name,
                description=existing.description,
                parent_id=existing.parent_id,
                status=DepartmentStatus.DISABLED,
                sort_order=existing.sort_order,
                leader_id=existing.leader_id,
                created_at=existing.created_at,
                updated_at=self._clock.now(),
                created_by=existing.created_by,
                updated_by=ctx.actor_id,
            )
            await repo.save_department(updated)

            after_state = self._dept_state(updated)
            diff = generate_diff(
                DEPARTMENT_FIELD_WHITELIST,
                before=before_state,
                after=after_state,
            )
            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="org.dept.disable",
                    resource_id=str(department_id),
                    resource_display_name=updated.display_name,
                    diff=diff,
                ),
            )

            await uow.commit()
            return _to_response_dict(updated)

    async def adjust_hierarchy(
        self,
        ctx: UseCaseContext,
        department_id: UUID,
        request: DepartmentHierarchyRequest,
    ) -> dict[str, object]:
        """调整部门层级与排序 — SPEC 14.1.

        循环防护（SPEC 14.1: "防止形成循环层级"）:
          1. 直接循环：目标父部门是自身 → 拒绝。
          2. 间接循环：目标父部门是自身后代 → 拒绝。
          3. 并发防护：事务级咨询锁序列化并发层级调整，
             防止两个并发事务同时通过检查后形成循环。

        校验目标父部门存在且处于启用状态（禁用部门不能作为父部门）。
        """

        now = self._clock.now()

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            # SPEC 14.1: 事务级咨询锁——序列化并发层级调整
            await repo.acquire_hierarchy_lock()

            existing = await repo.get_department_by_id(department_id)
            if existing is None:
                raise DepartmentNotFoundError(str(department_id))

            before_state = self._dept_state(existing)

            # 校验新父部门
            if request.parent_id is not None:
                # 直接循环：目标父部门是自身
                if request.parent_id == department_id:
                    raise DepartmentCycleError(
                        "不能将部门设为自身的子部门",
                    )

                # 间接循环：目标父部门是自身后代
                descendant_ids = await repo.get_descendant_ids(department_id)
                if request.parent_id in descendant_ids:
                    raise DepartmentCycleError(
                        "不能将部门移动到自身后代下，会形成循环层级",
                    )

                # 校验父部门存在且启用
                parent = await repo.get_department_by_id(request.parent_id)
                if parent is None:
                    raise InvalidParentError(
                        f"父部门 {request.parent_id} 不存在",
                    )
                if parent.status == DepartmentStatus.DISABLED:
                    raise InvalidParentError(
                        f"父部门 {parent.code} 已禁用，不能作为父部门",
                    )

            updated = Department(
                id=existing.id,
                code=existing.code,
                display_name=existing.display_name,
                description=existing.description,
                parent_id=request.parent_id,
                status=existing.status,
                sort_order=request.sort_order,
                leader_id=existing.leader_id,
                created_at=existing.created_at,
                updated_at=now,
                created_by=existing.created_by,
                updated_by=ctx.actor_id,
            )
            await repo.save_department(updated)

            after_state = self._dept_state(updated)
            diff = generate_diff(
                DEPARTMENT_FIELD_WHITELIST,
                before=before_state,
                after=after_state,
            )
            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="org.dept.adjust_hierarchy",
                    resource_id=str(department_id),
                    resource_display_name=updated.display_name,
                    diff=diff,
                ),
            )

            await uow.commit()
            return _to_response_dict(updated)

    async def set_leader(
        self,
        ctx: UseCaseContext,
        department_id: UUID,
        request: DepartmentLeaderRequest,
    ) -> dict[str, object]:
        """设置部门负责人 — SPEC 14.1.

        负责人引用用户 ID，通过 identity 模块 Port 校验用户存在性
        （SPEC 5.2 跨模块）。设为 null 清除负责人。
        """

        now = self._clock.now()

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            existing = await repo.get_department_by_id(department_id)
            if existing is None:
                raise DepartmentNotFoundError(str(department_id))

            # 校验负责人存在性 — SPEC 5.2 跨模块
            if request.leader_id is not None:
                user_auth = self._create_user_auth_port(uow.session)
                status = await user_auth.get_status_by_id(request.leader_id)
                if status is None:
                    from app.modules.identity.errors import UserNotFoundError

                    raise UserNotFoundError(str(request.leader_id))

            before_state = self._dept_state(existing)

            updated = Department(
                id=existing.id,
                code=existing.code,
                display_name=existing.display_name,
                description=existing.description,
                parent_id=existing.parent_id,
                status=existing.status,
                sort_order=existing.sort_order,
                leader_id=request.leader_id,
                created_at=existing.created_at,
                updated_at=now,
                created_by=existing.created_by,
                updated_by=ctx.actor_id,
            )
            await repo.save_department(updated)

            after_state = self._dept_state(updated)
            diff = generate_diff(
                DEPARTMENT_FIELD_WHITELIST,
                before=before_state,
                after=after_state,
            )
            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="org.dept.set_leader",
                    resource_id=str(department_id),
                    resource_display_name=updated.display_name,
                    diff=diff,
                ),
            )

            await uow.commit()
            return _to_response_dict(updated)

    async def delete_department(
        self,
        ctx: UseCaseContext,
        department_id: UUID,
    ) -> None:
        """删除部门 — SPEC 14.1.

        SPEC 14.1: "有用户或子部门时的删除规则明确"。
        - 存在子部门时拒绝删除（``ORG.DEPT_HAS_CHILDREN``）。
        - 存在用户关联时拒绝删除（``ORG.DEPT_HAS_USERS``）。
        """

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            existing = await repo.get_department_by_id(department_id)
            if existing is None:
                raise DepartmentNotFoundError(str(department_id))

            # 删除保护：子部门
            child_count = await repo.count_children(department_id)
            if child_count > 0:
                raise DepartmentHasChildrenError(str(department_id))

            # 删除保护：用户（TASK-020 实现后生效）
            user_count = await repo.count_users_in_department(department_id)
            if user_count > 0:
                raise DepartmentHasUsersError(str(department_id))

            await repo.delete_department_by_id(department_id)

            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="org.dept.delete",
                    resource_id=str(department_id),
                    resource_display_name=existing.display_name,
                ),
            )

            await uow.commit()

    # ── 岗位管理 — SPEC 14.2 ─────────────────────────────────────────────

    async def create_post(
        self,
        ctx: UseCaseContext,
        request: PostCreateRequest,
    ) -> dict[str, object]:
        """创建岗位 — SPEC 14.2.

        SPEC 14.2: "岗位不直接替代角色和权限"。
        """

        now = self._clock.now()
        post_id = self._id_generator.generate_id()

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            post = Post(
                id=post_id,
                code=request.code,
                display_name=request.display_name,
                description=request.description,
                status=PostStatus.ACTIVE,
                sort_order=request.sort_order,
                created_at=now,
                updated_at=now,
                created_by=ctx.actor_id,
                updated_by=ctx.actor_id,
            )
            await repo.add_post(post)

            diff = generate_diff(
                POST_FIELD_WHITELIST,
                before=None,
                after=self._post_state(post),
            )
            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="org.post.create",
                    resource_id=str(post_id),
                    resource_display_name=post.display_name,
                    diff=diff,
                    resource_type="post",
                ),
            )

            await uow.commit()
            return _to_post_response_dict(post)

    async def list_posts(
        self,
        ctx: UseCaseContext,
        *,
        include_disabled: bool = True,
        offset: int = 0,
        limit: int = 100,
    ) -> dict[str, object]:
        """查询岗位列表 — SPEC 14.2."""

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            posts, total = await repo.list_posts(
                include_disabled=include_disabled,
                offset=offset,
                limit=limit,
            )
            return {
                "items": [_to_post_response_dict(p) for p in posts],
                "total": total,
            }

    async def get_post_detail(
        self,
        ctx: UseCaseContext,
        post_id: UUID,
    ) -> dict[str, object]:
        """查询岗位详情 — SPEC 14.2."""

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            post = await repo.get_post_by_id(post_id)
            if post is None:
                raise PostNotFoundError(str(post_id))
            user_count = await repo.count_users_for_post(post_id)
            result = _to_post_response_dict(post)
            result["user_count"] = user_count
            return result

    async def update_post(
        self,
        ctx: UseCaseContext,
        post_id: UUID,
        request: PostUpdateRequest,
    ) -> dict[str, object]:
        """更新岗位基本信息 — SPEC 14.2."""

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            existing = await repo.get_post_by_id(post_id)
            if existing is None:
                raise PostNotFoundError(str(post_id))

            before_state = self._post_state(existing)

            updated = Post(
                id=existing.id,
                code=existing.code,
                display_name=request.display_name,
                description=request.description,
                status=existing.status,
                sort_order=existing.sort_order,
                created_at=existing.created_at,
                updated_at=self._clock.now(),
                created_by=existing.created_by,
                updated_by=ctx.actor_id,
            )
            await repo.save_post(updated)

            after_state = self._post_state(updated)
            diff = generate_diff(
                POST_FIELD_WHITELIST,
                before=before_state,
                after=after_state,
            )
            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="org.post.update",
                    resource_id=str(post_id),
                    resource_display_name=updated.display_name,
                    diff=diff,
                    resource_type="post",
                ),
            )

            await uow.commit()
            return _to_post_response_dict(updated)

    async def enable_post(
        self,
        ctx: UseCaseContext,
        post_id: UUID,
    ) -> dict[str, object]:
        """启用岗位 — SPEC 14.2."""

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            existing = await repo.get_post_by_id(post_id)
            if existing is None:
                raise PostNotFoundError(str(post_id))
            if existing.status == PostStatus.ACTIVE:
                raise PostAlreadyActiveError(str(post_id))

            before_state = self._post_state(existing)

            updated = Post(
                id=existing.id,
                code=existing.code,
                display_name=existing.display_name,
                description=existing.description,
                status=PostStatus.ACTIVE,
                sort_order=existing.sort_order,
                created_at=existing.created_at,
                updated_at=self._clock.now(),
                created_by=existing.created_by,
                updated_by=ctx.actor_id,
            )
            await repo.save_post(updated)

            after_state = self._post_state(updated)
            diff = generate_diff(
                POST_FIELD_WHITELIST,
                before=before_state,
                after=after_state,
            )
            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="org.post.enable",
                    resource_id=str(post_id),
                    resource_display_name=updated.display_name,
                    diff=diff,
                    resource_type="post",
                ),
            )

            await uow.commit()
            return _to_post_response_dict(updated)

    async def disable_post(
        self,
        ctx: UseCaseContext,
        post_id: UUID,
    ) -> dict[str, object]:
        """禁用岗位 — SPEC 14.2."""

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            existing = await repo.get_post_by_id(post_id)
            if existing is None:
                raise PostNotFoundError(str(post_id))
            if existing.status == PostStatus.DISABLED:
                raise PostAlreadyDisabledError(str(post_id))

            before_state = self._post_state(existing)

            updated = Post(
                id=existing.id,
                code=existing.code,
                display_name=existing.display_name,
                description=existing.description,
                status=PostStatus.DISABLED,
                sort_order=existing.sort_order,
                created_at=existing.created_at,
                updated_at=self._clock.now(),
                created_by=existing.created_by,
                updated_by=ctx.actor_id,
            )
            await repo.save_post(updated)

            after_state = self._post_state(updated)
            diff = generate_diff(
                POST_FIELD_WHITELIST,
                before=before_state,
                after=after_state,
            )
            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="org.post.disable",
                    resource_id=str(post_id),
                    resource_display_name=updated.display_name,
                    diff=diff,
                    resource_type="post",
                ),
            )

            await uow.commit()
            return _to_post_response_dict(updated)

    async def delete_post(
        self,
        ctx: UseCaseContext,
        post_id: UUID,
    ) -> None:
        """删除岗位 — SPEC 14.2.

        SPEC 14.2: 存在用户岗位关联时拒绝删除（``ORG.POST_HAS_USERS``）。
        """

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            existing = await repo.get_post_by_id(post_id)
            if existing is None:
                raise PostNotFoundError(str(post_id))

            user_count = await repo.count_users_for_post(post_id)
            if user_count > 0:
                raise PostHasUsersError(str(post_id))

            await repo.delete_post_by_id(post_id)

            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="org.post.delete",
                    resource_id=str(post_id),
                    resource_display_name=existing.display_name,
                    resource_type="post",
                ),
            )

            await uow.commit()

    # ── 用户组织关系 — SPEC 14.2 / 14.3 ─────────────────────────────────

    async def assign_user_department(
        self,
        ctx: UseCaseContext,
        user_id: UUID,
        department_id: UUID,
    ) -> dict[str, object]:
        """设置用户主部门 — SPEC 14.3.

        SPEC 14.3: "用户具有明确的主部门"。
        基座默认仅主部门。如果用户已有主部门，抛出
        ``UserAlreadyHasDepartmentError``。

        校验部门存在且处于启用状态。
        通过 identity 模块 Port 校验用户存在性（SPEC 5.2 跨模块）。
        """

        now = self._clock.now()

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            # 校验部门存在且启用
            dept = await repo.get_department_by_id(department_id)
            if dept is None:
                raise DepartmentNotFoundError(str(department_id))
            if dept.status == DepartmentStatus.DISABLED:
                raise DepartmentDisabledError(str(department_id))

            # 校验用户存在性 — SPEC 5.2 跨模块
            user_auth = self._create_user_auth_port(uow.session)
            status = await user_auth.get_status_by_id(user_id)
            if status is None:
                from app.modules.identity.errors import UserNotFoundError

                raise UserNotFoundError(str(user_id))

            await repo.set_user_department(
                user_id,
                department_id,
                created_by=ctx.actor_id,
                created_at=now,
            )

            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="org.user.assign_department",
                    resource_id=str(user_id),
                    resource_display_name=str(department_id),
                    resource_type="user_org",
                ),
            )

            await uow.commit()
            return {
                "user_id": user_id,
                "department_id": department_id,
                "is_primary": True,
            }

    async def remove_user_department(
        self,
        ctx: UseCaseContext,
        user_id: UUID,
    ) -> None:
        """移除用户的主部门 — SPEC 14.3."""

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            removed = await repo.remove_user_department(user_id)
            if not removed:
                raise UserDepartmentNotFoundError(str(user_id))

            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="org.user.remove_department",
                    resource_id=str(user_id),
                    resource_display_name=None,
                    resource_type="user_org",
                ),
            )

            await uow.commit()

    async def assign_user_post(
        self,
        ctx: UseCaseContext,
        user_id: UUID,
        post_id: UUID,
    ) -> dict[str, object]:
        """为用户分配岗位 — SPEC 14.2.

        SPEC 14.2: "为用户分配岗位"。
        分配幂等——已存在时返回成功（无操作）。
        校验岗位存在且启用，用户存在。
        """

        now = self._clock.now()

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            # 校验岗位存在且启用
            post = await repo.get_post_by_id(post_id)
            if post is None:
                raise PostNotFoundError(str(post_id))
            if post.status == PostStatus.DISABLED:
                raise PostDisabledError(str(post_id))

            # 校验用户存在性 — SPEC 5.2 跨模块
            user_auth = self._create_user_auth_port(uow.session)
            user_status = await user_auth.get_status_by_id(user_id)
            if user_status is None:
                from app.modules.identity.errors import UserNotFoundError

                raise UserNotFoundError(str(user_id))

            created = await repo.assign_user_post(
                user_id,
                post_id,
                created_by=ctx.actor_id,
                created_at=now,
            )

            if created:
                await audit.record_audit(
                    self._make_audit_entry(
                        ctx,
                        action="org.user.assign_post",
                        resource_id=str(user_id),
                        resource_display_name=str(post_id),
                        resource_type="user_org",
                    ),
                )

            await uow.commit()
            return {"user_id": user_id, "post_id": post_id}

    async def remove_user_post(
        self,
        ctx: UseCaseContext,
        user_id: UUID,
        post_id: UUID,
    ) -> None:
        """移除用户岗位 — SPEC 14.2.

        SPEC 14.2: "移除用户岗位"。
        """

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            removed = await repo.remove_user_post(user_id, post_id)
            if not removed:
                raise UserPostNotFoundError(f"{user_id}/{post_id}")

            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="org.user.remove_post",
                    resource_id=str(user_id),
                    resource_display_name=str(post_id),
                    resource_type="user_org",
                ),
            )

            await uow.commit()

    async def get_user_org_info(
        self,
        ctx: UseCaseContext,
        user_id: UUID,
    ) -> dict[str, object]:
        """查询用户组织关系（部门 + 岗位）— SPEC 14.3 / 11.1.

        供跨模块聚合——identity 模块在用户详情中调用 org 的公开 Port
        获取此信息（SPEC 5.5 跨模块通过公开 Port）。
        """

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            dept = await repo.get_user_department(user_id)
            posts = await repo.list_user_posts(user_id)
            return {
                "department": _to_dept_info_dict(dept) if dept else None,
                "posts": [_to_post_info_dict(p) for p in posts],
            }


def _build_tree(departments: list[Department]) -> list[DepartmentTreeNode]:
    """从扁平部门列表构建树结构.

    按 ``sort_order`` 排序，按 ``parent_id`` 组织父子关系。
    """

    nodes: dict[UUID, DepartmentTreeNode] = {}
    for dept in departments:
        nodes[dept.id] = DepartmentTreeNode(
            id=dept.id,
            code=dept.code,
            display_name=dept.display_name,
            description=dept.description,
            parent_id=dept.parent_id,
            status=dept.status,
            sort_order=dept.sort_order,
            leader_id=dept.leader_id,
            children=[],
            created_at=dept.created_at,
            updated_at=dept.updated_at,
        )

    roots: list[DepartmentTreeNode] = []
    for dept in departments:
        node = nodes[dept.id]
        if dept.parent_id is not None and dept.parent_id in nodes:
            nodes[dept.parent_id].children.append(node)
        else:
            roots.append(node)

    # 按排序序号排序
    roots.sort(key=lambda n: (n.sort_order, n.display_name))
    for node in nodes.values():
        node.children.sort(key=lambda n: (n.sort_order, n.display_name))

    return roots


def _to_response_dict(dept: Department) -> dict[str, object]:
    """部门领域实体 → 响应字典."""

    return {
        "id": dept.id,
        "code": dept.code,
        "display_name": dept.display_name,
        "description": dept.description,
        "parent_id": dept.parent_id,
        "status": dept.status.value,
        "sort_order": dept.sort_order,
        "leader_id": dept.leader_id,
        "created_at": dept.created_at,
        "updated_at": dept.updated_at,
    }


def _to_detail_dict(dept: Department, child_count: int) -> dict[str, object]:
    """部门领域实体 → 详情响应字典."""

    result = _to_response_dict(dept)
    result["child_count"] = child_count
    return result


def _tree_node_to_dict(node: DepartmentTreeNode) -> dict[str, object]:
    """树节点 → 响应字典（递归）."""

    return {
        "id": node.id,
        "code": node.code,
        "display_name": node.display_name,
        "description": node.description,
        "parent_id": node.parent_id,
        "status": node.status.value,
        "sort_order": node.sort_order,
        "leader_id": node.leader_id,
        "children": [_tree_node_to_dict(child) for child in node.children],
        "created_at": node.created_at,
        "updated_at": node.updated_at,
    }


def _to_post_response_dict(post: Post) -> dict[str, object]:
    """岗位领域实体 → 响应字典."""

    return {
        "id": post.id,
        "code": post.code,
        "display_name": post.display_name,
        "description": post.description,
        "status": post.status.value,
        "sort_order": post.sort_order,
        "created_at": post.created_at,
        "updated_at": post.updated_at,
    }


def _to_dept_info_dict(info: object) -> dict[str, object]:
    """用户部门投影 → 响应字典."""

    return {
        "department_id": info.department_id,  # type: ignore[attr-defined]
        "department_code": info.department_code,  # type: ignore[attr-defined]
        "department_name": info.department_name,  # type: ignore[attr-defined]
        "is_primary": info.is_primary,  # type: ignore[attr-defined]
    }


def _to_post_info_dict(info: object) -> dict[str, object]:
    """用户岗位投影 → 响应字典."""

    return {
        "post_id": info.post_id,  # type: ignore[attr-defined]
        "post_code": info.post_code,  # type: ignore[attr-defined]
        "post_name": info.post_name,  # type: ignore[attr-defined]
    }
