"""文件状态机 — SPEC 19.3.

状态转换图::

    PENDING → READY → DELETING → DELETED
       │         │
       └─────────┴────→ FAILED

合法转换:
  - PENDING  → READY     （上传完成，原子 rename 后）
  - PENDING  → FAILED    （上传失败）
  - READY    → DELETING  （删除发起，无活动业务引用）
  - READY    → FAILED    （READY 元数据缺少物理文件）
  - DELETING → DELETED   （物理删除成功）
  - DELETING → DELETING  （物理删除失败幂等重试，保持 DELETING）

非法转换:
  - PENDING  → DELETING / DELETED
  - READY    → PENDING / DELETED
  - DELETING → PENDING / READY / FAILED
  - DELETED  → 任何状态（终态）
  - FAILED   → 任何状态（终态）

状态机为纯领域逻辑，不依赖 ORM、数据库或任何基础设施类型（SPEC 5.2）。
"""

from __future__ import annotations

from app.modules.file.errors import FileInvalidTransitionError
from app.modules.file.models import FileStatus

#: 合法状态转换表 — SPEC 19.3.
#:
#: 键为源状态，值为该状态可转换到的目标状态集合。
#: 未在此表中的转换均为非法。
_TRANSITIONS: dict[FileStatus, frozenset[FileStatus]] = {
    FileStatus.PENDING: frozenset({FileStatus.READY, FileStatus.FAILED}),
    FileStatus.READY: frozenset({FileStatus.DELETING, FileStatus.FAILED}),
    FileStatus.DELETING: frozenset({FileStatus.DELETED, FileStatus.DELETING}),
    FileStatus.DELETED: frozenset(),
    FileStatus.FAILED: frozenset(),
}


def can_transition(source: FileStatus, target: FileStatus) -> bool:
    """判断状态转换是否合法 — SPEC 19.3.

    参数:
        source: 源状态。
        target: 目标状态。

    返回:
        转换是否合法。
    """

    allowed = _TRANSITIONS.get(source, frozenset())
    return target in allowed


def transition(source: FileStatus, target: FileStatus) -> None:
    """执行状态转换，非法时抛出 ``FileInvalidTransitionError`` — SPEC 19.3.

    参数:
        source: 源状态。
        target: 目标状态。

    抛出:
        FileInvalidTransitionError: 状态转换非法。
    """

    if not can_transition(source, target):
        raise FileInvalidTransitionError(
            f"非法状态转换: {source.value} → {target.value}",
            source=source.value,
            target=target.value,
        )
