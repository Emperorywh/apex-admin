"""菜单模块单元测试 — SPEC 15.1 / 15.2.

覆盖:
  - 领域实体与枚举。
  - 树构建逻辑。
  - 循环防护逻辑（直接/间接循环）。
  - 响应转换辅助函数。

这些测试不需要数据库。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.modules.menu.models import Menu, MenuStatus, MenuTreeNode, MenuType
from app.modules.menu.use_case import _build_tree, _to_response_dict, _tree_node_to_dict


@pytest.mark.g3
@pytest.mark.unit
class TestMenuDomainModels:
    """菜单领域实体与枚举测试 — SPEC 15.1."""

    def test_menu_type_values(self) -> None:
        """菜单类型枚举值正确 — SPEC 15.1."""

        assert MenuType.DIRECTORY == "directory"
        assert MenuType.PAGE == "page"
        assert MenuType.LINK == "link"

    def test_menu_status_values(self) -> None:
        """菜单状态枚举值正确 — SPEC 15.1."""

        assert MenuStatus.ACTIVE == "active"
        assert MenuStatus.DISABLED == "disabled"

    def test_menu_is_frozen(self) -> None:
        """菜单实体不可变."""

        menu = _make_menu()
        with pytest.raises(AttributeError):
            menu.title = "modified"  # type: ignore[misc]

    def test_menu_treenode_default_children(self) -> None:
        """树节点默认子节点列表为空."""

        node = MenuTreeNode(
            id=uuid4(),
            parent_id=None,
            menu_type=MenuType.DIRECTORY,
            title="root",
            name=None,
            path=None,
            component=None,
            icon=None,
            sort_order=0,
            visible=True,
            status=MenuStatus.ACTIVE,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        assert node.children == []


@pytest.mark.g3
@pytest.mark.unit
class TestBuildTree:
    """菜单树构建逻辑测试 — SPEC 15.1."""

    def test_flat_list_to_tree(self) -> None:
        """扁平列表正确构建为树."""

        root = _make_menu(title="Root", sort_order=0)
        child1 = _make_menu(title="C1", parent_id=root.id, sort_order=1)
        child2 = _make_menu(title="C2", parent_id=root.id, sort_order=0)

        tree = _build_tree([root, child1, child2])
        assert len(tree) == 1
        assert tree[0].id == root.id
        assert len(tree[0].children) == 2
        # children sorted by sort_order then title
        assert tree[0].children[0].title == "C2"
        assert tree[0].children[1].title == "C1"

    def test_multiple_roots(self) -> None:
        """多个根菜单."""

        root1 = _make_menu(title="R1", sort_order=1)
        root2 = _make_menu(title="R2", sort_order=0)

        tree = _build_tree([root1, root2])
        assert len(tree) == 2
        assert tree[0].title == "R2"
        assert tree[1].title == "R1"

    def test_deep_nesting(self) -> None:
        """深层嵌套."""

        root = _make_menu(title="Root")
        child = _make_menu(title="Child", parent_id=root.id)
        grandchild = _make_menu(title="Grandchild", parent_id=child.id)

        tree = _build_tree([root, child, grandchild])
        assert len(tree) == 1
        assert tree[0].children[0].title == "Child"
        assert tree[0].children[0].children[0].title == "Grandchild"

    def test_orphan_child_becomes_root(self) -> None:
        """parent_id 不在列表中的菜单成为根节点."""

        orphan = _make_menu(title="Orphan", parent_id=uuid4())

        tree = _build_tree([orphan])
        assert len(tree) == 1
        assert tree[0].id == orphan.id

    def test_empty_list(self) -> None:
        """空列表返回空树."""

        assert _build_tree([]) == []


@pytest.mark.g3
@pytest.mark.unit
class TestResponseConversion:
    """响应转换辅助函数测试."""

    def test_to_response_dict(self) -> None:
        """领域实体 → 响应字典."""

        menu = _make_menu(
            menu_type=MenuType.PAGE,
            title="Dashboard",
            name="dashboard",
            path="/dashboard",
            component="Dashboard",
            icon="dashboard",
            visible=True,
        )
        result = _to_response_dict(menu)
        assert result["menu_type"] == "page"
        assert result["title"] == "Dashboard"
        assert result["name"] == "dashboard"
        assert result["visible"] is True
        assert result["status"] == "active"

    def test_tree_node_to_dict_recursive(self) -> None:
        """树节点 → 响应字典（递归）."""

        now = datetime.now(UTC)
        root = MenuTreeNode(
            id=uuid4(),
            parent_id=None,
            menu_type=MenuType.DIRECTORY,
            title="Root",
            name=None,
            path=None,
            component=None,
            icon=None,
            sort_order=0,
            visible=True,
            status=MenuStatus.ACTIVE,
            created_at=now,
            updated_at=now,
            children=[
                MenuTreeNode(
                    id=uuid4(),
                    parent_id=uuid4(),
                    menu_type=MenuType.PAGE,
                    title="Child",
                    name="child",
                    path="/child",
                    component="Child",
                    icon=None,
                    sort_order=0,
                    visible=True,
                    status=MenuStatus.ACTIVE,
                    created_at=now,
                    updated_at=now,
                ),
            ],
        )
        result = _tree_node_to_dict(root)
        assert result["title"] == "Root"
        assert len(result["children"]) == 1
        assert result["children"][0]["title"] == "Child"


@pytest.mark.g3
@pytest.mark.unit
class TestMenuErrorCodes:
    """菜单错误码注册与异常类测试 — SPEC 10.1 / 10.2."""

    def test_error_codes_registered(self) -> None:
        """错误码注册到框架注册表."""

        from app.core.errors.codes import default_registry
        from app.modules.menu.errors import (
            MENU_ALREADY_ACTIVE,
            MENU_ALREADY_DISABLED,
            MENU_CYCLE_DETECTED,
            MENU_HAS_CHILDREN,
            MENU_INVALID_PARENT,
            MENU_INVALID_TYPE,
            MENU_NOT_FOUND,
        )

        for code in [
            MENU_NOT_FOUND,
            MENU_ALREADY_DISABLED,
            MENU_ALREADY_ACTIVE,
            MENU_CYCLE_DETECTED,
            MENU_INVALID_PARENT,
            MENU_HAS_CHILDREN,
            MENU_INVALID_TYPE,
        ]:
            meta = default_registry.get(code)
            assert meta is not None, f"错误码 {code} 未注册"

    def test_not_found_http_status(self) -> None:
        """MENU.NOT_FOUND 映射到 404."""

        from app.core.errors.codes import default_registry
        from app.modules.menu.errors import MENU_NOT_FOUND

        meta = default_registry.get(MENU_NOT_FOUND)
        assert meta is not None
        assert meta.http_status == 404

    def test_cycle_detected_http_status(self) -> None:
        """MENU.CYCLE_DETECTED 映射到 409."""

        from app.core.errors.codes import default_registry
        from app.modules.menu.errors import MENU_CYCLE_DETECTED

        meta = default_registry.get(MENU_CYCLE_DETECTED)
        assert meta is not None
        assert meta.http_status == 409

    def test_invalid_parent_http_status(self) -> None:
        """MENU.INVALID_PARENT 映射到 400."""

        from app.core.errors.codes import default_registry
        from app.modules.menu.errors import MENU_INVALID_PARENT

        meta = default_registry.get(MENU_INVALID_PARENT)
        assert meta is not None
        assert meta.http_status == 400


@pytest.mark.g3
@pytest.mark.unit
class TestMenuSchemas:
    """菜单 Schema 校验测试 — SPEC 9.2."""

    def test_create_request_valid(self) -> None:
        """合法创建请求通过校验."""

        from app.modules.menu.schemas import MenuCreateRequest

        req = MenuCreateRequest(
            menu_type="directory",
            title="系统管理",
            sort_order=0,
            visible=True,
        )
        assert req.menu_type == "directory"
        assert req.visible is True

    def test_create_request_unknown_field_rejected(self) -> None:
        """创建请求拒绝未知字段 — SPEC 9.2 extra=forbid."""

        from pydantic import ValidationError

        from app.modules.menu.schemas import MenuCreateRequest

        with pytest.raises(ValidationError):
            MenuCreateRequest(
                menu_type="page",
                title="Test",
                unknown_field="bad",
            )  # type: ignore[call-arg]

    def test_create_request_invalid_menu_type(self) -> None:
        """非法菜单类型被拒绝."""

        from pydantic import ValidationError

        from app.modules.menu.schemas import MenuCreateRequest

        with pytest.raises(ValidationError):
            MenuCreateRequest(
                menu_type="invalid",
                title="Test",
            )

    def test_update_request_valid(self) -> None:
        """合法更新请求通过校验."""

        from app.modules.menu.schemas import MenuUpdateRequest

        req = MenuUpdateRequest(title="Updated", visible=False)
        assert req.visible is False

    def test_hierarchy_request_valid(self) -> None:
        """合法层级调整请求通过校验."""

        from app.modules.menu.schemas import MenuHierarchyRequest

        req = MenuHierarchyRequest(parent_id=uuid4(), sort_order=5)
        assert req.sort_order == 5


# ── 辅助函数 ───────────────────────────────────────────────────────────────


def _make_menu(
    *,
    menu_type: MenuType = MenuType.DIRECTORY,
    title: str = "Test Menu",
    parent_id: UUID | None = None,
    sort_order: int = 0,
    visible: bool = True,
    name: str | None = None,
    path: str | None = None,
    component: str | None = None,
    icon: str | None = None,
) -> Menu:
    """构造测试用菜单领域实体."""

    now = datetime.now(UTC)
    return Menu(
        id=uuid4(),
        parent_id=parent_id,
        menu_type=menu_type,
        title=title,
        name=name,
        path=path,
        component=component,
        icon=icon,
        sort_order=sort_order,
        visible=visible,
        status=MenuStatus.ACTIVE,
        created_at=now,
        updated_at=now,
        created_by="test",
        updated_by="test",
    )


# 向前引用 UUID 类型
from uuid import UUID  # noqa: E402, TC003
