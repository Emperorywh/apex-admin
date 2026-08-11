"""组织模块单元测试 — SPEC 14.1 / 18.2 / 5.7.

覆盖:
  - 领域实体、状态枚举与 Schema 验证（不连接数据库）。
  - 错误码注册与异常类型。
  - Schema 拒绝未知字段（extra="forbid"）。
  - 树构建逻辑（纯函数，不连接数据库）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.modules.org.errors import (
    ORG_DEPT_ALREADY_ACTIVE,
    ORG_DEPT_ALREADY_DISABLED,
    ORG_DEPT_ALREADY_EXISTS,
    ORG_DEPT_CYCLE_DETECTED,
    ORG_DEPT_HAS_CHILDREN,
    ORG_DEPT_HAS_USERS,
    ORG_DEPT_INVALID_PARENT,
    ORG_DEPT_NOT_FOUND,
    DepartmentAlreadyActiveError,
    DepartmentAlreadyDisabledError,
    DepartmentAlreadyExistsError,
    DepartmentCycleError,
    DepartmentHasChildrenError,
    DepartmentHasUsersError,
    DepartmentNotFoundError,
    InvalidParentError,
)
from app.modules.org.models import (
    Department,
    DepartmentStatus,
)
from app.modules.org.schemas import (
    DepartmentCreateRequest,
    DepartmentHierarchyRequest,
    DepartmentLeaderRequest,
    DepartmentUpdateRequest,
)
from app.modules.org.use_case import _build_tree

# ═══════════════════════════════════════════════════════════════════════════════
# 领域实体与状态枚举
# ═══════════════════════════════════════════════════════════════════════════════


def _make_department(
    *,
    status: DepartmentStatus = DepartmentStatus.ACTIVE,
    parent_id: UUID | None = None,
    leader_id: UUID | None = None,
) -> Department:
    """构造测试用部门实体。"""

    now = datetime(2026, 1, 1, tzinfo=UTC)
    return Department(
        id=uuid4(),
        code="engineering",
        display_name="工程部",
        description="研发部门",
        parent_id=parent_id,
        status=status,
        sort_order=0,
        leader_id=leader_id,
        created_at=now,
        updated_at=now,
        created_by="admin",
        updated_by="admin",
    )


@pytest.mark.g3
@pytest.mark.unit
class TestDepartmentStatus:
    """部门状态枚举 — SPEC 8.3 / 14.1."""

    def test_active_value(self) -> None:
        assert DepartmentStatus.ACTIVE.value == "active"

    def test_disabled_value(self) -> None:
        assert DepartmentStatus.DISABLED.value == "disabled"

    def test_from_string(self) -> None:
        assert DepartmentStatus("active") == DepartmentStatus.ACTIVE
        assert DepartmentStatus("disabled") == DepartmentStatus.DISABLED

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError):
            DepartmentStatus("invalid")


@pytest.mark.g3
@pytest.mark.unit
class TestDepartmentEntity:
    """部门领域实体 — SPEC 14.1."""

    def test_department_is_frozen(self) -> None:
        dept = _make_department()
        with pytest.raises(AttributeError):
            dept.display_name = "changed"  # type: ignore[misc]

    def test_department_fields(self) -> None:
        dept = _make_department()
        assert dept.code == "engineering"
        assert dept.display_name == "工程部"
        assert dept.status == DepartmentStatus.ACTIVE
        assert dept.parent_id is None
        assert dept.leader_id is None


# ═══════════════════════════════════════════════════════════════════════════════
# Schema 验证
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.unit
class TestDepartmentCreateRequest:
    """创建部门请求 Schema — SPEC 9.2."""

    def test_valid_request(self) -> None:
        req = DepartmentCreateRequest(
            code="engineering",
            display_name="工程部",
        )
        assert req.code == "engineering"
        assert req.parent_id is None
        assert req.sort_order == 0

    def test_with_parent_and_leader(self) -> None:
        parent_id = uuid4()
        leader_id = uuid4()
        req = DepartmentCreateRequest(
            code="backend",
            display_name="后端组",
            parent_id=parent_id,
            leader_id=leader_id,
            sort_order=5,
        )
        assert req.parent_id == parent_id
        assert req.leader_id == leader_id
        assert req.sort_order == 5

    def test_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError):
            DepartmentCreateRequest(
                code="test",
                display_name="Test",
                unknown_field="bad",  # type: ignore[call-arg]
            )

    def test_code_pattern_rejects_uppercase(self) -> None:
        with pytest.raises(ValidationError):
            DepartmentCreateRequest(code="Engineering", display_name="E")

    def test_code_pattern_rejects_digit_start(self) -> None:
        with pytest.raises(ValidationError):
            DepartmentCreateRequest(code="1dept", display_name="D")

    def test_code_min_length(self) -> None:
        with pytest.raises(ValidationError):
            DepartmentCreateRequest(code="a", display_name="D")

    def test_display_name_required(self) -> None:
        with pytest.raises(ValidationError):
            DepartmentCreateRequest(code="test")  # type: ignore[call-arg]

    def test_sort_order_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DepartmentCreateRequest(
                code="test",
                display_name="T",
                sort_order=-1,
            )


@pytest.mark.g3
@pytest.mark.unit
class TestDepartmentUpdateRequest:
    """更新部门请求 Schema — SPEC 9.2."""

    def test_valid_request(self) -> None:
        req = DepartmentUpdateRequest(
            display_name="更新名称",
            description="新描述",
        )
        assert req.display_name == "更新名称"

    def test_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError):
            DepartmentUpdateRequest(
                display_name="T",
                code="hack",  # type: ignore[call-arg]
            )


@pytest.mark.g3
@pytest.mark.unit
class TestDepartmentHierarchyRequest:
    """调整层级请求 Schema — SPEC 14.1."""

    def test_root_request(self) -> None:
        req = DepartmentHierarchyRequest(parent_id=None, sort_order=0)
        assert req.parent_id is None

    def test_with_parent(self) -> None:
        pid = uuid4()
        req = DepartmentHierarchyRequest(parent_id=pid, sort_order=3)
        assert req.parent_id == pid
        assert req.sort_order == 3

    def test_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError):
            DepartmentHierarchyRequest(
                parent_id=None,
                sort_order=0,
                unknown="bad",  # type: ignore[call-arg]
            )


@pytest.mark.g3
@pytest.mark.unit
class TestDepartmentLeaderRequest:
    """设置负责人请求 Schema — SPEC 14.1."""

    def test_set_leader(self) -> None:
        leader = uuid4()
        req = DepartmentLeaderRequest(leader_id=leader)
        assert req.leader_id == leader

    def test_clear_leader(self) -> None:
        req = DepartmentLeaderRequest(leader_id=None)
        assert req.leader_id is None

    def test_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError):
            DepartmentLeaderRequest(
                leader_id=None,
                unknown="bad",  # type: ignore[call-arg]
            )


# ═══════════════════════════════════════════════════════════════════════════════
# 错误码与异常
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.unit
class TestDeptErrorCodes:
    """组织模块错误码与异常 — SPEC 10.1 / 10.2."""

    def test_dept_not_found_code(self) -> None:
        assert ORG_DEPT_NOT_FOUND == "ORG.DEPT_NOT_FOUND"
        exc = DepartmentNotFoundError("test")
        assert exc.code == ORG_DEPT_NOT_FOUND

    def test_dept_already_exists_code(self) -> None:
        assert ORG_DEPT_ALREADY_EXISTS == "ORG.DEPT_ALREADY_EXISTS"
        exc = DepartmentAlreadyExistsError("test")
        assert exc.code == ORG_DEPT_ALREADY_EXISTS

    def test_dept_already_disabled_code(self) -> None:
        assert ORG_DEPT_ALREADY_DISABLED == "ORG.DEPT_ALREADY_DISABLED"
        exc = DepartmentAlreadyDisabledError("test")
        assert exc.code == ORG_DEPT_ALREADY_DISABLED

    def test_dept_already_active_code(self) -> None:
        assert ORG_DEPT_ALREADY_ACTIVE == "ORG.DEPT_ALREADY_ACTIVE"
        exc = DepartmentAlreadyActiveError("test")
        assert exc.code == ORG_DEPT_ALREADY_ACTIVE

    def test_dept_has_children_code(self) -> None:
        assert ORG_DEPT_HAS_CHILDREN == "ORG.DEPT_HAS_CHILDREN"
        exc = DepartmentHasChildrenError("test")
        assert exc.code == ORG_DEPT_HAS_CHILDREN

    def test_dept_has_users_code(self) -> None:
        assert ORG_DEPT_HAS_USERS == "ORG.DEPT_HAS_USERS"
        exc = DepartmentHasUsersError("test")
        assert exc.code == ORG_DEPT_HAS_USERS

    def test_dept_cycle_detected_code(self) -> None:
        assert ORG_DEPT_CYCLE_DETECTED == "ORG.DEPT_CYCLE_DETECTED"
        exc = DepartmentCycleError("test")
        assert exc.code == ORG_DEPT_CYCLE_DETECTED

    def test_invalid_parent_code(self) -> None:
        assert ORG_DEPT_INVALID_PARENT == "ORG.DEPT_INVALID_PARENT"
        exc = InvalidParentError("test")
        assert exc.code == ORG_DEPT_INVALID_PARENT


# ═══════════════════════════════════════════════════════════════════════════════
# 树构建逻辑
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.unit
class TestDeptTreeBuild:
    """树构建逻辑 — SPEC 14.1.

    测试 ``_build_tree`` 纯函数的层级组织和排序。
    """

    def _make_dept(
        self,
        *,
        id: UUID,
        code: str,
        parent_id: UUID | None = None,
        sort_order: int = 0,
        status: DepartmentStatus = DepartmentStatus.ACTIVE,
    ) -> Department:
        """构造测试用部门。"""

        now = datetime(2026, 1, 1, tzinfo=UTC)
        return Department(
            id=id,
            code=code,
            display_name=code,
            description=None,
            parent_id=parent_id,
            status=status,
            sort_order=sort_order,
            leader_id=None,
            created_at=now,
            updated_at=now,
            created_by=None,
            updated_by=None,
        )

    def test_empty_list(self) -> None:
        """空部门列表返回空树。"""

        tree = _build_tree([])
        assert tree == []

    def test_single_root(self) -> None:
        """单个根部门返回单节点树。"""

        dept_id = uuid4()
        dept = self._make_dept(id=dept_id, code="root")
        tree = _build_tree([dept])
        assert len(tree) == 1
        assert tree[0].id == dept_id
        assert tree[0].children == []

    def test_parent_child(self) -> None:
        """父子部门正确组织为树。"""

        parent_id = uuid4()
        child_id = uuid4()
        parent = self._make_dept(id=parent_id, code="parent")
        child = self._make_dept(id=child_id, code="child", parent_id=parent_id)
        tree = _build_tree([parent, child])
        assert len(tree) == 1
        assert tree[0].id == parent_id
        assert len(tree[0].children) == 1
        assert tree[0].children[0].id == child_id

    def test_sort_order(self) -> None:
        """同级部门按 sort_order 排序。"""

        root_id = uuid4()
        child_a_id = uuid4()
        child_b_id = uuid4()
        root = self._make_dept(id=root_id, code="root")
        child_b = self._make_dept(
            id=child_b_id,
            code="b",
            parent_id=root_id,
            sort_order=1,
        )
        child_a = self._make_dept(
            id=child_a_id,
            code="a",
            parent_id=root_id,
            sort_order=0,
        )
        tree = _build_tree([root, child_b, child_a])
        assert len(tree) == 1
        children = tree[0].children
        assert children[0].id == child_a_id
        assert children[1].id == child_b_id

    def test_multiple_roots(self) -> None:
        """多个根部门都出现在顶层。"""

        root1_id = uuid4()
        root2_id = uuid4()
        root1 = self._make_dept(id=root1_id, code="r1", sort_order=0)
        root2 = self._make_dept(id=root2_id, code="r2", sort_order=1)
        tree = _build_tree([root1, root2])
        assert len(tree) == 2
        assert tree[0].id == root1_id
        assert tree[1].id == root2_id

    def test_deep_nesting(self) -> None:
        """深层嵌套正确构建。"""

        root_id = uuid4()
        mid_id = uuid4()
        leaf_id = uuid4()
        root = self._make_dept(id=root_id, code="root")
        mid = self._make_dept(id=mid_id, code="mid", parent_id=root_id)
        leaf = self._make_dept(id=leaf_id, code="leaf", parent_id=mid_id)
        tree = _build_tree([root, mid, leaf])
        assert tree[0].children[0].children[0].id == leaf_id

    def test_disabled_dept_in_tree(self) -> None:
        """禁用部门仍在树中（默认可见）。"""

        root_id = uuid4()
        disabled_id = uuid4()
        root = self._make_dept(id=root_id, code="root")
        disabled = self._make_dept(
            id=disabled_id,
            code="disabled",
            parent_id=root_id,
            status=DepartmentStatus.DISABLED,
        )
        tree = _build_tree([root, disabled])
        assert len(tree) == 1
        assert len(tree[0].children) == 1
        assert tree[0].children[0].status == DepartmentStatus.DISABLED

    def test_orphan_treated_as_root(self) -> None:
        """parent_id 指向不存在的部门时，作为根处理。"""

        orphan_id = uuid4()
        orphan = self._make_dept(
            id=orphan_id,
            code="orphan",
            parent_id=uuid4(),
        )
        tree = _build_tree([orphan])
        assert len(tree) == 1
        assert tree[0].id == orphan_id
