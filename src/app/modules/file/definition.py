"""文件管理模块 ModuleDefinition — SPEC 5.5 / 19.1 / 19.2 / 19.3 / 19.4.

此文件公开文件管理模块的全部声明信息（SPEC 5.5）:
  - 模块编码 ``file``
  - 权限点、错误码、审计动作、受保护资源类型
  - Router 列表
  - 公开 Application Port（``FileReferencePort`` 供业务模块 retain/release 引用，
    ``FileReadPort`` 供业务模块读取附件流）
  - Alembic 迁移版本目录

SPEC 19.1: 本地文件存储。
SPEC 19.2: 上传与下载。
SPEC 19.3: 文件状态机。
SPEC 19.4: 业务引用与授权边界。

导入此模块时自动注册错误码到框架注册表（通过 ``errors.py``）。
"""

from __future__ import annotations

from app.core.modules.definition import ModuleDefinition
from app.modules.file.errors import (
    FILE_EXTENSION_NOT_ALLOWED,
    FILE_FORBIDDEN,
    FILE_HAS_REFERENCES,
    FILE_INVALID_TRANSITION,
    FILE_NOT_FOUND,
    FILE_NOT_READY,
    FILE_STORAGE_ERROR,
    FILE_TOO_LARGE,
    FILE_TYPE_FORGED,
    FILE_TYPE_NOT_ALLOWED,
    FILE_UPLOAD_COUNT_EXCEEDED,
)
from app.modules.file.port import FileReadPort, FileReferencePort
from app.modules.file.router import router as file_router

# ── 声明常量 ────────────────────────────────────────────────────────────────

#: 模块编码 — 全局唯一且稳定（SPEC 5.5）。
MODULE_CODE = "file"

#: API Tag — 用于 OpenAPI 分组（SPEC 9.6），全局唯一。
MODULE_API_TAG = "file"

#: 权限点 — SPEC 5.5 / 23.5: 所有管理接口具有权限点。
PERMISSION_FILE_READ = "file:manage:read"
PERMISSION_FILE_WRITE = "file:manage:write"

#: 审计动作 — SPEC 18.2 / 19.2: 记录操作模块和动作。
AUDIT_FILE_UPLOAD = "file.upload"
AUDIT_FILE_DELETE = "file.delete"

#: Alembic 迁移版本目录（相对于项目根 / CWD）。
ALEMBIC_VERSION_DIR = "src/app/modules/file/migrations"


# ── ModuleDefinition 实例 ──────────────────────────────────────────────────

MODULE_DEFINITION = ModuleDefinition(
    code=MODULE_CODE,
    api_tag=MODULE_API_TAG,
    # SPEC 5.5: 模块公开的 Application Port。
    # FileReferencePort 供业务模块 retain/release 文件引用（SPEC 19.4）。
    # FileReadPort 供业务模块读取文件附件流（SPEC 19.4）。
    application_ports=(FileReferencePort, FileReadPort),
    required_dependencies=("audit",),  # 审计写入
    optional_dependencies=(),
    routers=(file_router,),
    permission_codes=(
        PERMISSION_FILE_READ,
        PERMISSION_FILE_WRITE,
    ),
    error_codes=(
        FILE_NOT_FOUND,
        FILE_TOO_LARGE,
        FILE_TYPE_NOT_ALLOWED,
        FILE_TYPE_FORGED,
        FILE_UPLOAD_COUNT_EXCEEDED,
        FILE_NOT_READY,
        FILE_INVALID_TRANSITION,
        FILE_HAS_REFERENCES,
        FILE_FORBIDDEN,
        FILE_EXTENSION_NOT_ALLOWED,
        FILE_STORAGE_ERROR,
    ),
    audit_actions=(
        AUDIT_FILE_UPLOAD,
        AUDIT_FILE_DELETE,
    ),
    protected_resource_types=("file",),
    initializers=(),
    management_commands=(),
    event_handlers=(),
    event_codes=(),
    alembic_version_dir=ALEMBIC_VERSION_DIR,
)
