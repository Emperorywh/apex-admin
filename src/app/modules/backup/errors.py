"""备份与恢复异常定义 — SPEC 27.1 / 27.3.

所有备份异常继承 ``BackupError``，CLI 层捕获后返回非 0 退出码
并输出结构化日志（SPEC 27.1: 备份失败能够被发现）。
"""

from __future__ import annotations


class BackupError(Exception):
    """备份与恢复基础异常."""


class BackupCreationError(BackupError):
    """备份创建失败（数据库不可用、pg_dump 失败、文件复制失败等）。"""


class BackupVerificationError(BackupError):
    """备份验证失败（恢复失败、迁移版本不匹配、完整性检查未通过等）。"""


class ManifestError(BackupError):
    """备份清单读取或校验失败（文件缺失、哈希不匹配、格式错误等）。"""
