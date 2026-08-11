# ADR-0004：Copier 模板 uv.lock 协同策略

- **状态**：accepted
- **日期**：2026-08-12

## 背景

SPEC 30.3 要求将仓库转换为 Copier 模板，派生项目需保留可用的 uv.lock。

核心问题：uv.lock 的根包条目 `name = "apex-admin"` 在生成新项目时必须更新为派生项目的 `project_slug`。两种候选策略：

**方案 A — uv.lock.jinja 渲染**
- 将 uv.lock 重命名为 uv.lock.jinja，用 Jinja2 渲染根包名。
- 问题：uv.lock 包含 1500+ 行 TOML，其中大量 `{}` 和 `[]` 字符（wheel hash、URL 等）会与 Jinja2 语法冲突，需要逐行转义。

**方案 B — 后处理脚本替换 + uv lock 重生成**
- 排除 uv.lock，在 Copier `_tasks` 中运行 `uv lock` 重新生成。
- 问题：`copier copy` 时需要网络访问解析依赖，离线场景不可用。

**方案 C — 后处理脚本直接替换 uv.lock 中的根包名**
- 保留 uv.lock，在后处理脚本中将 `name = "apex-admin"` 替换为 `name = "{project_slug}"`。
- 依赖不变时 uv.lock 结构完全一致，仅根包名不同。

## 决策

采用 **方案 C**。

Spike 验证步骤：
1. 将 pyproject.toml 和 uv.lock 中的 `apex-admin` 替换为 `my-backend`。
2. 运行 `uv lock --check` → 退出码 0（90 个包在 1ms 内解析）。
3. 运行 `uv sync --frozen` → 退出码 0（全部依赖安装成功）。

结论：uv.lock 的根包名是一个独立字段，替换不影响依赖解析结果。无需网络访问，无需重新解析。

## 后果

- `_copier_postprocess.py` 负责在生成项目中替换 uv.lock 的根包名。
- 如果未来 uv.lock 格式发生变化（如引入 content-hash），需重新评估此方案。
- 派生项目的 uv.lock 与模板的 uv.lock 在依赖层面完全一致，差异仅在根包名。
