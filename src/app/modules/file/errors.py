"""文件管理模块错误码与异常 — SPEC 10.1 / 10.2 / 19.2 / 19.3 / 19.4.

SPEC 10.2:
  - 错误码全局唯一且稳定。
  - 错误码按 ``<MODULE>.<REASON>`` 格式划分命名空间。
  - 错误码与展示文案分离。

模块错误码在导入时注册到框架默认注册表 ``default_registry``。
"""

from __future__ import annotations

from app.core.errors.codes import default_registry
from app.core.errors.exceptions import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ParameterError,
    SystemError,
)

# ── 错误码常量 ──────────────────────────────────────────────────────────────

#: 文件不存在 — 按 ID 查询或操作文件但未找到。
FILE_NOT_FOUND = "FILE.NOT_FOUND"

#: 文件超过大小限制 — SPEC 19.2: 限制单文件大小。
FILE_TOO_LARGE = "FILE.TOO_LARGE"

#: 文件类型不被允许 — SPEC 19.2: 使用白名单校验允许的文件类型。
FILE_TYPE_NOT_ALLOWED = "FILE.TYPE_NOT_ALLOWED"

#: 文件类型伪造 — 扩展名、声明类型与内容特征不一致（SPEC 19.2）。
FILE_TYPE_FORGED = "FILE.TYPE_FORGED"

#: 上传文件数量超过限制 — SPEC 19.2: 限制单次上传数量。
FILE_UPLOAD_COUNT_EXCEEDED = "FILE.UPLOAD_COUNT_EXCEEDED"

#: 文件不是 READY 状态，不可下载 — SPEC 19.3: API 只允许下载 READY 文件。
FILE_NOT_READY = "FILE.NOT_READY"

#: 文件状态转换非法 — SPEC 19.3: 状态机不允许的转换。
FILE_INVALID_TRANSITION = "FILE.INVALID_TRANSITION"

#: 文件存在活动业务引用，不可删除 — SPEC 19.4。
FILE_HAS_REFERENCES = "FILE.HAS_REFERENCES"

#: 文件操作权限不足 — 跨用户访问被拒绝（SPEC 19.4）。
FILE_FORBIDDEN = "FILE.FORBIDDEN"

#: 文件扩展名不被支持。
FILE_EXTENSION_NOT_ALLOWED = "FILE.EXTENSION_NOT_ALLOWED"

#: 文件物理写入失败 — 磁盘错误或路径问题。
FILE_STORAGE_ERROR = "FILE.STORAGE_ERROR"


# ── 模块异常类 ──────────────────────────────────────────────────────────────


class FileNotFoundError(NotFoundError):
    """文件不存在 — HTTP 404."""

    code = FILE_NOT_FOUND


class FileTooLargeError(ConflictError):
    """文件超过大小限制 — HTTP 409.

    SPEC 19.2: "限制单文件大小"。
    """

    code = FILE_TOO_LARGE


class FileTypeNotAllowedError(ConflictError):
    """文件类型不被允许 — HTTP 409.

    SPEC 19.2: "使用白名单校验允许的文件类型"。
    """

    code = FILE_TYPE_NOT_ALLOWED


class FileTypeError(ParameterError):
    """文件类型伪造 — 扩展名、声明类型与内容特征不一致.

    SPEC 19.2: "同时检查扩展名、声明类型和必要的文件内容特征"。
    HTTP 状态码 400（参数错误）。
    """

    code = FILE_TYPE_FORGED


class FileUploadCountExceededError(ConflictError):
    """上传文件数量超过限制 — HTTP 409.

    SPEC 19.2: "限制单次上传数量"。
    """

    code = FILE_UPLOAD_COUNT_EXCEEDED


class FileNotReadyError(ConflictError):
    """文件不是 READY 状态，不可下载 — HTTP 409.

    SPEC 19.3: "API 只允许下载 READY 文件"。
    """

    code = FILE_NOT_READY


class FileInvalidTransitionError(ConflictError):
    """文件状态转换非法 — HTTP 409.

    SPEC 19.3: 状态机不允许的转换。
    """

    code = FILE_INVALID_TRANSITION

    def __init__(
        self,
        message: str,
        *,
        source: str | None = None,
        target: str | None = None,
    ) -> None:
        """初始化状态转换错误，携带源/目标状态信息."""

        super().__init__(message)
        self.source_status = source
        self.target_status = target


class FileHasReferencesError(ConflictError):
    """文件存在活动业务引用，不可删除 — HTTP 409.

    SPEC 19.4: "删除前必须在事务中确认没有活动业务引用"。
    """

    code = FILE_HAS_REFERENCES


class FileForbiddenError(AuthorizationError):
    """文件操作权限不足 — 跨用户访问被拒绝.

    SPEC 19.4: "通用文件管理接口只允许上传者管理临时文件或拥有文件管理权限
    的管理员访问"。
    HTTP 状态码 403。
    """

    code = FILE_FORBIDDEN


class FileExtensionNotAllowedError(ConflictError):
    """文件扩展名不被支持 — HTTP 409."""

    code = FILE_EXTENSION_NOT_ALLOWED


class FileStorageError(SystemError):
    """文件物理写入失败 — HTTP 500.

    磁盘错误或路径问题。
    """

    code = FILE_STORAGE_ERROR


# ── 注册到框架默认注册表 ──────────────────────────────────────────────────

default_registry.register(
    FILE_NOT_FOUND,
    404,
    meaning="文件不存在",
    scenario="按 ID 查询或操作文件但未找到",
)
default_registry.register(
    FILE_TOO_LARGE,
    409,
    meaning="文件超过大小限制",
    scenario="上传文件大小超过配置的最大限制（SPEC 19.2）",
)
default_registry.register(
    FILE_TYPE_NOT_ALLOWED,
    409,
    meaning="文件类型不被允许",
    scenario="上传文件类型不在白名单中（SPEC 19.2）",
)
default_registry.register(
    FILE_TYPE_FORGED,
    400,
    meaning="文件类型伪造",
    scenario="扩展名、声明类型与文件内容特征不一致（SPEC 19.2）",
)
default_registry.register(
    FILE_UPLOAD_COUNT_EXCEEDED,
    409,
    meaning="上传文件数量超过限制",
    scenario="单次上传文件数量超过配置的最大值（SPEC 19.2）",
)
default_registry.register(
    FILE_NOT_READY,
    409,
    meaning="文件不是 READY 状态，不可下载",
    scenario="尝试下载非 READY 状态的文件（SPEC 19.3）",
)
default_registry.register(
    FILE_INVALID_TRANSITION,
    409,
    meaning="文件状态转换非法",
    scenario="尝试执行状态机不允许的状态转换（SPEC 19.3）",
)
default_registry.register(
    FILE_HAS_REFERENCES,
    409,
    meaning="文件存在活动业务引用，不可删除",
    scenario="尝试删除仍被业务引用的文件（SPEC 19.4）",
)
default_registry.register(
    FILE_FORBIDDEN,
    403,
    meaning="文件操作权限不足",
    scenario="跨用户访问文件被拒绝（SPEC 19.4）",
)
default_registry.register(
    FILE_EXTENSION_NOT_ALLOWED,
    409,
    meaning="文件扩展名不被支持",
    scenario="上传文件扩展名不在白名单中（SPEC 19.2）",
)
default_registry.register(
    FILE_STORAGE_ERROR,
    500,
    meaning="文件物理写入失败",
    scenario="磁盘错误或路径问题导致文件写入失败",
)
