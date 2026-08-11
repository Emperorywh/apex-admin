"""跨模块数据完整性检查 — SPEC 25.3.

SPEC 25.3: ``uv run python -m app.cli data check`` 检查角色权限关系、
菜单和部门循环、失效或孤立关联数据。

此模块是诊断工具，使用原始 SQL 跨模块查询表完整性。这不是业务逻辑，
而是运维诊断——检查跨模块关联表（无数据库外键约束的关联）的引用完整性，
以及自引用层级表（menu_menus、org_departments）中的循环。

检查项:
  1. 菜单循环 — ``menu_menus.parent_id`` 自引用形成循环。
  2. 部门循环 — ``org_departments.parent_id`` 自引用形成循环。
  3. 孤立用户角色关系 — ``rbac_user_roles.user_id`` 引用不存在的用户
     （跨模块无外键约束）。
  4. 孤立角色菜单关系 — ``menu_role_menus.role_id`` 引用不存在的角色
     （跨模块无外键约束）。
  5. 孤立用户部门关系 — ``org_user_departments.user_id`` 引用不存在的用户
     （跨模块无外键约束）。
  6. 孤立用户岗位关系 — ``org_user_posts.user_id`` 引用不存在的用户
     （跨模块无外键约束）。
  7. 失效部门负责人 — ``org_departments.leader_id`` 引用不存在的用户
     （跨模块无外键约束）。

``data check`` 仅报告，不修复（SPEC nonGoals: 不实现自动修复）。
健康库退出码 0；发现问题退出码非 0 并报告具体位置。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class CheckIssue:
    """数据完整性问题记录.

    属性:
        check:    检查项名称（如 ``"menu_cycle"``、``"orphaned_user_role"``）。
        location: 问题位置（如 ``"menu_menus: id=<uuid> parent_id=<uuid>"``）。
        detail:   问题详细描述。
    """

    check: str
    location: str
    detail: str


@dataclass(frozen=True)
class DataCheckResult:
    """数据检查结果.

    属性:
        issues: 发现的全部问题列表。
    """

    issues: list[CheckIssue] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        """数据库是否健康（无问题）。"""

        return len(self.issues) == 0


# ── 纯函数: 循环检测 ────────────────────────────────────────────────────────


def detect_cycles(
    edges: dict[str, str | None],
) -> list[tuple[str, ...]]:
    """检测有向图中的循环 — 纯函数.

    给定 ``{id: parent_id}`` 映射，检测所有循环路径。

    对每个未处理节点，沿 parent_id 链遍历。若链上遇到当前路径中的节点，
    则发现循环；若遇到已完全处理的节点，则安全终止。

    参数:
        edges: ``{节点ID: 父节点ID}`` 映射，父节点为 None 表示根节点。

    返回:
        循环路径列表，每条路径为节点 ID 序列。
        例如 ``[("a", "b", "c")]`` 表示 a→b→c→a 形成循环。
    """

    WHITE, BLACK = 0, 1
    color: dict[str, int] = {node: WHITE for node in edges}
    cycles: list[tuple[str, ...]] = []

    for start_node in edges:
        if color[start_node] == BLACK:
            continue

        # 从 start_node 出发沿 parent 链遍历
        path: list[str] = []
        path_set: set[str] = set()
        current: str | None = start_node

        while current is not None:
            if current in path_set:
                # 当前节点已在路径中——发现循环
                cycle_start = path.index(current)
                cycle = tuple(path[cycle_start:])
                cycles.append(cycle)
                break

            if color.get(current, WHITE) == BLACK:
                # 到达已完全处理的节点——不会产生新循环
                break

            path.append(current)
            path_set.add(current)
            current = edges.get(current)

        # 标记此轮遍历的所有节点为已处理
        for node in path:
            color[node] = BLACK

    return cycles


# ── 异步检查实现 ────────────────────────────────────────────────────────────


async def _check_menu_cycles(session: AsyncSession) -> list[CheckIssue]:
    """检查菜单 parent_id 层级循环 — SPEC 25.3."""

    from sqlalchemy import text

    rows = (
        await session.execute(
            text("SELECT id::text, parent_id::text FROM menu_menus"),
        )
    ).fetchall()

    edges: dict[str, str | None] = {
        str(row[0]): str(row[1]) if row[1] is not None else None for row in rows
    }
    cycles = detect_cycles(edges)

    return [
        CheckIssue(
            check="menu_cycle",
            location=f"menu_menus: cycle {' -> '.join(cycle + (cycle[0],))}",
            detail=f"菜单层级存在循环: {' → '.join(cycle + (cycle[0],))}",
        )
        for cycle in cycles
    ]


async def _check_dept_cycles(session: AsyncSession) -> list[CheckIssue]:
    """检查部门 parent_id 层级循环 — SPEC 25.3."""

    from sqlalchemy import text

    rows = (
        await session.execute(
            text("SELECT id::text, parent_id::text FROM org_departments"),
        )
    ).fetchall()

    edges: dict[str, str | None] = {
        str(row[0]): str(row[1]) if row[1] is not None else None for row in rows
    }
    cycles = detect_cycles(edges)

    return [
        CheckIssue(
            check="dept_cycle",
            location=f"org_departments: cycle {' -> '.join(cycle + (cycle[0],))}",
            detail=f"部门层级存在循环: {' → '.join(cycle + (cycle[0],))}",
        )
        for cycle in cycles
    ]


async def _check_orphaned_user_roles(session: AsyncSession) -> list[CheckIssue]:
    """检查孤立用户角色关系 — rbac_user_roles.user_id 引用不存在的用户.

    SPEC 5.5: ``rbac_user_roles.user_id`` 跨模块引用 identity 模块，
    无数据库外键约束。
    """

    from sqlalchemy import text

    rows = (
        await session.execute(
            text(
                "SELECT ur.user_id::text, ur.role_id::text "
                "FROM rbac_user_roles ur "
                "LEFT JOIN users u ON u.id = ur.user_id "
                "WHERE u.id IS NULL",
            ),
        )
    ).fetchall()

    return [
        CheckIssue(
            check="orphaned_user_role",
            location=(f"rbac_user_roles: user_id={row[0]} role_id={row[1]}"),
            detail="用户角色关系引用了不存在的用户（孤立关联）",
        )
        for row in rows
    ]


async def _check_orphaned_role_menus(session: AsyncSession) -> list[CheckIssue]:
    """检查孤立角色菜单关系 — menu_role_menus.role_id 引用不存在的角色.

    SPEC 5.5: ``menu_role_menus.role_id`` 跨模块引用 rbac 模块，
    无数据库外键约束。
    """

    from sqlalchemy import text

    rows = (
        await session.execute(
            text(
                "SELECT rm.role_id::text, rm.menu_id::text "
                "FROM menu_role_menus rm "
                "LEFT JOIN rbac_roles r ON r.id = rm.role_id "
                "WHERE r.id IS NULL",
            ),
        )
    ).fetchall()

    return [
        CheckIssue(
            check="orphaned_role_menu",
            location=(f"menu_role_menus: role_id={row[0]} menu_id={row[1]}"),
            detail="角色菜单关系引用了不存在的角色（失效关联）",
        )
        for row in rows
    ]


async def _check_orphaned_user_departments(
    session: AsyncSession,
) -> list[CheckIssue]:
    """检查孤立用户部门关系 — org_user_departments.user_id 引用不存在的用户.

    SPEC 5.5: ``org_user_departments.user_id`` 跨模块引用 identity 模块，
    无数据库外键约束。
    """

    from sqlalchemy import text

    rows = (
        await session.execute(
            text(
                "SELECT ud.user_id::text, ud.department_id::text "
                "FROM org_user_departments ud "
                "LEFT JOIN users u ON u.id = ud.user_id "
                "WHERE u.id IS NULL",
            ),
        )
    ).fetchall()

    return [
        CheckIssue(
            check="orphaned_user_department",
            location=(f"org_user_departments: user_id={row[0]} department_id={row[1]}"),
            detail="用户部门关系引用了不存在的用户（孤立关联）",
        )
        for row in rows
    ]


async def _check_orphaned_user_posts(session: AsyncSession) -> list[CheckIssue]:
    """检查孤立用户岗位关系 — org_user_posts.user_id 引用不存在的用户.

    SPEC 5.5: ``org_user_posts.user_id`` 跨模块引用 identity 模块，
    无数据库外键约束。
    """

    from sqlalchemy import text

    rows = (
        await session.execute(
            text(
                "SELECT up.user_id::text, up.post_id::text "
                "FROM org_user_posts up "
                "LEFT JOIN users u ON u.id = up.user_id "
                "WHERE u.id IS NULL",
            ),
        )
    ).fetchall()

    return [
        CheckIssue(
            check="orphaned_user_post",
            location=(f"org_user_posts: user_id={row[0]} post_id={row[1]}"),
            detail="用户岗位关系引用了不存在的用户（孤立关联）",
        )
        for row in rows
    ]


async def _check_invalid_dept_leader(session: AsyncSession) -> list[CheckIssue]:
    """检查失效部门负责人 — org_departments.leader_id 引用不存在的用户.

    SPEC 5.5: ``org_departments.leader_id`` 跨模块引用 identity 模块，
    无数据库外键约束。
    """

    from sqlalchemy import text

    rows = (
        await session.execute(
            text(
                "SELECT d.id::text, d.leader_id::text "
                "FROM org_departments d "
                "LEFT JOIN users u ON u.id = d.leader_id "
                "WHERE d.leader_id IS NOT NULL AND u.id IS NULL",
            ),
        )
    ).fetchall()

    return [
        CheckIssue(
            check="invalid_dept_leader",
            location=(f"org_departments: id={row[0]} leader_id={row[1]}"),
            detail="部门负责人引用了不存在的用户（失效关联）",
        )
        for row in rows
    ]


async def run_data_check(session: AsyncSession) -> DataCheckResult:
    """执行全部数据完整性检查 — SPEC 25.3.

    按确定性顺序执行所有检查项，聚合结果。
    仅报告，不修复。

    参数:
        session: 当前数据库会话（只读查询）。

    返回:
        检查结果，``healthy=True`` 表示无问题。
    """

    issues: list[CheckIssue] = []

    issues.extend(await _check_menu_cycles(session))
    issues.extend(await _check_dept_cycles(session))
    issues.extend(await _check_orphaned_user_roles(session))
    issues.extend(await _check_orphaned_role_menus(session))
    issues.extend(await _check_orphaned_user_departments(session))
    issues.extend(await _check_orphaned_user_posts(session))
    issues.extend(await _check_invalid_dept_leader(session))

    return DataCheckResult(issues=issues)


def format_data_check_report(result: DataCheckResult) -> str:
    """格式化数据检查报告为可读文本.

    参数:
        result: 数据检查结果。

    返回:
        多行文本报告。
    """

    lines: list[str] = [
        "数据完整性检查报告",
        "=" * 50,
    ]

    if result.healthy:
        lines.append("结果: 通过 — 未发现数据完整性问题")
        lines.append(f"检查项: {7} 项全部通过")
    else:
        lines.append(f"结果: 未通过 — 发现 {len(result.issues)} 个问题")
        lines.append("-" * 50)
        for issue in result.issues:
            lines.append(f"  [{issue.check}] {issue.location}")
            lines.append(f"    {issue.detail}")

    lines.append("=" * 50)
    return "\n".join(lines)
