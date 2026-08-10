"""用户模块错误码与异常 — SPEC 10.1 / 10.2 / 11.1.

SPEC 10.2:
  - 错误码全局唯一且稳定。
  - 错误码按 ``<MODULE>.<REASON>`` 格式划分命名空间。
  - 错误码与展示文案分离。
  - 每个错误码具有含义、HTTP 状态码和适用场景。

模块错误码在导入时注册到框架默认注册表 ``default_registry``，
使 API 边界的异常处理器能查找对应的 HTTP 状态码和含义元数据
（SPEC 10.1: "在 API 边界统一完成异常到 HTTP 响应的转换"）。
同一错误码也在 ``ModuleDefinition`` 中声明，供 ``modules validate``
检测全局重复（SPEC 5.5）。
"""

from __future__ import annotations

from app.core.errors.codes import default_registry
from app.core.errors.exceptions import ConflictError, NotFoundError

# ── 错误码常量 ──────────────────────────────────────────────────────────────
#
# SPEC 5.5: "业务错误码固定为 ``<MODULE>.<REASON>``，
# 只允许大写字母、数字和下划线"。

#: 用户不存在 — 按 ID 查询或操作用户但未找到。
USER_NOT_FOUND = "USER.NOT_FOUND"

#: 用户名已存在 — 创建用户时用户名已被占用（SPEC 8.4: 冲突错误稳定编码）。
USER_ALREADY_EXISTS = "USER.ALREADY_EXISTS"

#: 用户已禁用 — 尝试禁用已处于禁用状态的用户。
USER_ALREADY_DISABLED = "USER.ALREADY_DISABLED"

#: 用户已启用 — 尝试启用已处于启用状态的用户。
USER_ALREADY_ACTIVE = "USER.ALREADY_ACTIVE"

#: 旧密码不正确 — 自助改密时提供的旧密码与存储哈希不匹配。
USER_INVALID_OLD_PASSWORD = "USER.INVALID_OLD_PASSWORD"

#: 用户已有审计记录，禁止物理删除（SPEC 11.3）。
USER_HAS_AUDIT_RECORDS = "USER.HAS_AUDIT_RECORDS"


# ── 模块异常类 ──────────────────────────────────────────────────────────────


class UserNotFoundError(NotFoundError):
    """用户不存在 — HTTP 404.

    按 ID 查询或操作用户但未找到时使用。
    继承 ``NotFoundError``（HTTP 404），覆写错误码为模块专属编码。
    """

    code = USER_NOT_FOUND


class UserAlreadyExistsError(ConflictError):
    """用户名冲突 — HTTP 409.

    创建用户时用户名已被其他用户占用。
    继承 ``ConflictError``（HTTP 409），覆写错误码为稳定冲突编码
    （SPEC 8.4: "冲突错误具有明确的业务错误码"）。
    """

    code = USER_ALREADY_EXISTS


class UserAlreadyDisabledError(ConflictError):
    """用户已禁用 — HTTP 409.

    尝试禁用已处于禁用状态的用户，属于状态冲突。
    """

    code = USER_ALREADY_DISABLED


class UserAlreadyActiveError(ConflictError):
    """用户已启用 — HTTP 409.

    尝试启用已处于启用状态的用户，属于状态冲突。
    """

    code = USER_ALREADY_ACTIVE


class UserInvalidOldPasswordError(ConflictError):
    """旧密码不正确 — HTTP 409.

    自助改密时提供的旧密码与存储的 Argon2id 哈希不匹配。
    使用 HTTP 409 而非 401，因为这是状态校验失败而非认证失败
    （用户已通过认证，仅是旧密码值不正确）。
    """

    code = USER_INVALID_OLD_PASSWORD


class UserHasAuditRecordsError(ConflictError):
    """用户已有审计记录，禁止物理删除 — HTTP 409.

    SPEC 11.3: "已产生审计记录的用户不得因物理删除导致审计信息失真"。
    已产生审计记录的用户物理删除被拒绝，应优先使用禁用替代删除。
    """

    code = USER_HAS_AUDIT_RECORDS


# ── 注册到框架默认注册表 ──────────────────────────────────────────────────
#
# 导入此模块时自动注册。注册表拒绝重复注册和非法格式（SPEC 10.2）。
# composition/modules.py 导入 definition.py → definition.py 导入 errors.py
# 从而在应用启动前完成注册。

default_registry.register(
    USER_NOT_FOUND,
    404,
    meaning="用户不存在",
    scenario="按 ID 查询或操作用户但未找到时使用",
)
default_registry.register(
    USER_ALREADY_EXISTS,
    409,
    meaning="用户名已存在",
    scenario="创建用户时用户名已被占用（SPEC 8.4 唯一约束冲突）",
)
default_registry.register(
    USER_ALREADY_DISABLED,
    409,
    meaning="用户已禁用",
    scenario="尝试禁用已处于禁用状态的用户",
)
default_registry.register(
    USER_ALREADY_ACTIVE,
    409,
    meaning="用户已启用",
    scenario="尝试启用已处于启用状态的用户",
)
default_registry.register(
    USER_INVALID_OLD_PASSWORD,
    409,
    meaning="旧密码不正确",
    scenario="自助改密时提供的旧密码与存储哈希不匹配",
)
default_registry.register(
    USER_HAS_AUDIT_RECORDS,
    409,
    meaning="用户已有审计记录，禁止物理删除",
    scenario="已产生审计记录的用户物理删除被拒绝（SPEC 11.3）",
)
