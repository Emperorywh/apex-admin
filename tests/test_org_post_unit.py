"""岗位与用户组织关系单元测试 — SPEC 14.2 / 14.3 / 18.2.

覆盖:
  - 岗位领域实体、状态枚举与 Schema 验证（不连接数据库）。
  - 岗位错误码注册与异常类型。
  - 用户组织关系投影字段。

不连接数据库——纯领域逻辑与 Schema 验证。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.org.errors import (
    ORG_DEPT_DISABLED,
    ORG_POST_ALREADY_ACTIVE,
    ORG_POST_ALREADY_DISABLED,
    ORG_POST_ALREADY_EXISTS,
    ORG_POST_DISABLED,
    ORG_POST_HAS_USERS,
    ORG_POST_NOT_FOUND,
    ORG_USER_ALREADY_HAS_DEPARTMENT,
    ORG_USER_DEPT_NOT_FOUND,
    ORG_USER_POST_DUPLICATE,
    ORG_USER_POST_NOT_FOUND,
    DepartmentDisabledError,
    PostAlreadyActiveError,
    PostAlreadyDisabledError,
    PostAlreadyExistsError,
    PostDisabledError,
    PostHasUsersError,
    PostNotFoundError,
    UserAlreadyHasDepartmentError,
    UserDepartmentNotFoundError,
    UserPostDuplicateError,
    UserPostNotFoundError,
)
from app.modules.org.models import (
    Post,
    PostStatus,
    UserDepartmentInfo,
    UserPostInfo,
)
from app.modules.org.schemas import (
    AssignUserDepartmentRequest,
    AssignUserPostRequest,
    PostCreateRequest,
    PostUpdateRequest,
)

# ═══════════════════════════════════════════════════════════════════════════════
# 岗位领域实体与状态枚举
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.unit
class TestPostModel:
    """岗位领域实体测试 — SPEC 14.2."""

    def test_post_status_values(self) -> None:
        """岗位状态枚举值正确。"""

        assert PostStatus.ACTIVE == "active"
        assert PostStatus.DISABLED == "disabled"

    def test_post_is_frozen(self) -> None:
        """岗位实体不可变。"""

        post = Post(
            id=uuid4(),
            code="dev",
            display_name="开发",
            description=None,
            status=PostStatus.ACTIVE,
            sort_order=0,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            created_by=None,
            updated_by=None,
        )
        with pytest.raises(AttributeError):
            post.display_name = "modified"  # type: ignore[misc]

    def test_user_department_info_fields(self) -> None:
        """用户部门投影字段正确。"""

        info = UserDepartmentInfo(
            department_id=uuid4(),
            department_code="eng",
            department_name="工程部",
            is_primary=True,
        )
        assert info.department_code == "eng"
        assert info.is_primary is True

    def test_user_post_info_fields(self) -> None:
        """用户岗位投影字段正确。"""

        info = UserPostInfo(
            post_id=uuid4(),
            post_code="dev",
            post_name="开发",
        )
        assert info.post_code == "dev"
        assert info.post_name == "开发"


# ═══════════════════════════════════════════════════════════════════════════════
# 岗位 Schema 验证
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.unit
class TestPostSchemas:
    """岗位 Schema 验证测试 — SPEC 9.2 / 14.2."""

    def test_post_create_valid(self) -> None:
        """合法创建请求通过校验。"""

        req = PostCreateRequest(code="engineer", display_name="工程师")
        assert req.code == "engineer"
        assert req.sort_order == 0

    def test_post_create_code_pattern(self) -> None:
        """编码格式校验——必须小写字母开头。"""

        with pytest.raises(ValidationError):
            PostCreateRequest(code="UPPER", display_name="T")
        with pytest.raises(ValidationError):
            PostCreateRequest(code="1bad", display_name="T")

    def test_post_create_unknown_field_rejected(self) -> None:
        """未知字段返回 422。"""

        with pytest.raises(ValidationError):
            PostCreateRequest(
                code="dev",
                display_name="D",
                extra_field="bad",  # type: ignore[call-arg]
            )

    def test_post_update_valid(self) -> None:
        """合法更新请求通过校验。"""

        req = PostUpdateRequest(display_name="新名称", description="desc")
        assert req.display_name == "新名称"

    def test_assign_dept_request_valid(self) -> None:
        """分配部门请求通过校验。"""

        uid = uuid4()
        req = AssignUserDepartmentRequest(department_id=uid)
        assert req.department_id == uid

    def test_assign_post_request_valid(self) -> None:
        """分配岗位请求通过校验。"""

        pid = uuid4()
        req = AssignUserPostRequest(post_id=pid)
        assert req.post_id == pid


# ═══════════════════════════════════════════════════════════════════════════════
# 错误码注册与异常类型
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.unit
class TestPostErrorCodes:
    """岗位错误码与异常类测试 — SPEC 10.2."""

    def test_post_error_codes_registered(self) -> None:
        """岗位错误码已在框架注册表注册。"""

        from app.core.errors.codes import default_registry

        for code in (
            ORG_POST_NOT_FOUND,
            ORG_POST_ALREADY_EXISTS,
            ORG_POST_ALREADY_DISABLED,
            ORG_POST_ALREADY_ACTIVE,
            ORG_POST_HAS_USERS,
            ORG_POST_DISABLED,
            ORG_DEPT_DISABLED,
            ORG_USER_ALREADY_HAS_DEPARTMENT,
            ORG_USER_POST_DUPLICATE,
            ORG_USER_DEPT_NOT_FOUND,
            ORG_USER_POST_NOT_FOUND,
        ):
            entry = default_registry.get(code)
            assert entry is not None, f"{code} 未注册"

    def test_post_exception_types(self) -> None:
        """岗位异常类继承正确基类。"""

        from app.core.errors.exceptions import ConflictError, NotFoundError

        assert issubclass(PostNotFoundError, NotFoundError)
        assert issubclass(PostAlreadyExistsError, ConflictError)
        assert issubclass(PostAlreadyDisabledError, ConflictError)
        assert issubclass(PostAlreadyActiveError, ConflictError)
        assert issubclass(PostHasUsersError, ConflictError)
        assert issubclass(PostDisabledError, ConflictError)
        assert issubclass(DepartmentDisabledError, ConflictError)
        assert issubclass(UserAlreadyHasDepartmentError, ConflictError)
        assert issubclass(UserPostDuplicateError, ConflictError)
        assert issubclass(UserDepartmentNotFoundError, ConflictError)
        assert issubclass(UserPostNotFoundError, ConflictError)

    def test_post_exception_codes(self) -> None:
        """岗位异常类携带正确错误码。"""

        assert PostNotFoundError.code == ORG_POST_NOT_FOUND
        assert PostAlreadyExistsError.code == ORG_POST_ALREADY_EXISTS
        assert PostDisabledError.code == ORG_POST_DISABLED
        assert UserPostNotFoundError.code == ORG_USER_POST_NOT_FOUND


# ═══════════════════════════════════════════════════════════════════════════════
# AC-2: 无跨模块 ORM 访问 — 事件处理器通过 Port 而非 ORM 直接访问
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.unit
class TestNoCrossModuleOrmAccess:
    """跨模块 ORM 访问边界测试 — SPEC 5.2 / 5.5.

    SPEC 5.5: "禁止跨模块直接操作对方的数据表、ORM 模型和内部函数"。
    identity 模块不导入 org 的 ORM 模型，通过 Port 投影聚合。
    """

    def test_identity_use_case_not_import_org_orm(self) -> None:
        """identity Use Case 不导入 org.orm（ORM 模型）。"""

        import inspect

        from app.modules.identity import use_case as identity_uc

        source = inspect.getsource(identity_uc)
        assert "from app.modules.org.orm" not in source
        assert "import app.modules.org.orm" not in source

    def test_identity_use_case_imports_only_port(self) -> None:
        """identity Use Case 仅引用 org 的 Port（TYPE_CHECKING），不引用 Adapter。"""

        import inspect

        from app.modules.identity import use_case as identity_uc

        source = inspect.getsource(identity_uc)
        # UserOrgPort 是 Port 类型，仅用于类型标注
        assert "UserOrgPort" in source
        # 不直接引用 org 的 Adapter 或 ORM
        assert "SqlAlchemyOrgRepository" not in source
        assert "from app.modules.org.adapter" not in source
