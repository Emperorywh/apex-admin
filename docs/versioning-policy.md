# 项目初始化与基座演进策略 — SPEC 30.3

## 1. Copier 模板初始化

### 1.1 固定使用 Copier 生成新项目

新项目固定使用 Copier 从本模板生成，不支持手工复制作为正式初始化方式。

```bash
copier copy <模板路径> <目标路径>
```

Copier 会提出一组身份问题，根据答案生成完整的派生项目。

### 1.2 身份问题

| 问题 | 默认值 | 用途 |
| --- | --- | --- |
| `project_name` | My Backend | 项目显示名称（文档、日志、OpenAPI 标题） |
| `project_slug` | my-backend | 项目标识符（pyproject.toml name、Docker 镜像名、compose 服务标识） |
| `package_name` | app | Python 包名（导入名） |
| `urn_namespace` | urn:my-backend | RFC 9457 problem type URI 命名空间 |
| `config_prefix` | APP_ | 环境变量前缀 |
| `ext_enabled` | false | EXT 扩展模块开关（机制预留） |

### 1.3 生成后产物

- `.copier-answers.yml`：记录全部答案、模板来源路径和提交哈希，保留在派生项目根目录中。
- `_copier_postprocess.py`：生成后身份替换脚本，执行完毕后自动删除。
- 所有身份表面（项目名、配置前缀、URN 命名空间、Prometheus 指标名、Cookie 名等）均已替换为答案值。

### 1.4 无演示数据

生成的项目不携带任何演示数据。示例模块（`src/app/modules/example/`）提供完整的 Router → Use Case → Port → Adapter → 迁移 → 权限点 → 错误码 → 审计 → 测试接入演示，但不包含预置的业务记录。

### 1.5 EXT 扩展开关

`ext_enabled` 是 EXT 模块的统一开关（SPEC 31）。当前无可用 EXT 模块，该开关为机制预留。未来每个 EXT 模块将拥有独立启用开关、依赖组、迁移目录和验收测试。

---

## 2. 语义化版本与 Git Tag 发布

### 2.1 版号语义（SPEC 30.3）

基座使用 [SemVer](https://semver.org/) 语义化版本和 Git Tag 发布：

| 版本类型 | 包含内容 | 兼容性 |
| --- | --- | --- |
| **Patch** (0.1.0 → 0.1.1) | 兼容性修复和安全修复 | 完全兼容，无需修改派生项目代码 |
| **Minor** (0.1.0 → 0.2.0) | 新增默认关闭的能力、向后兼容的改进 | 向后兼容；新增能力默认关闭，不影响现有行为 |
| **Major** (0.x → 1.0.0) | 破坏式架构调整 | 不兼容；需要显式迁移或重新生成项目 |

### 2.2 Git Tag 发布流程

1. 更新 `pyproject.toml` 中的 `version` 字段。
2. 更新 `uv.lock`（运行 `uv lock`）。
3. 创建 Git Tag：`git tag -a v0.2.0 -m "Release 0.2.0"`。
4. 推送 Tag：`git push origin v0.2.0`。
5. 发布说明记录变更范围、受影响版本和迁移指引。

### 2.3 安全修复发布

安全修复必须发布：
- **受影响版本范围**（如 `>=0.1.0, <0.1.3`）。
- **修复版本**（如 `0.1.3`）。
- **可独立移植的变更说明**（明确哪些文件需要修改、修改内容是什么）。

---

## 3. 派生项目更新流程

### 3.1 copier update 获取 Patch / Minor 更新

派生项目通过 `copier update` 获取 Patch 或 Minor 更新：

```bash
copier update
```

Copier 会读取 `.copier-answers.yml` 中记录的模板版本，与当前模板对比，生成差异。

### 3.2 差异人工评审

`copier update` 产生的差异**必须人工评审**：

1. 审查每个变更文件，确认变更与发布说明一致。
2. 检查是否有冲突（派生项目修改过的文件与模板更新冲突）。
3. 解决冲突后，重新运行当前门槛验收（G1 → G2 → G3 → G4）。
4. 只有全部验收通过后，才允许合并更新。

### 3.3 Major 版本升级

Major 版本**不提供旧架构 fallback、双写或兼容层**。

升级路径：
1. 阅读发布说明中的迁移指引。
2. 依据迁移说明**显式升级**现有项目，或**重新生成项目**并迁移业务代码。
3. 不存在自动化迁移工具或兼容模式——这是一个有意的设计决策（SPEC 30.3 / 32）。

### 3.4 回答保留

`.copier-answers.yml` 必须保留在派生项目中。它记录了：
- 模板来源路径
- 模板提交哈希（`_commit`）
- 生成时的全部答案

这些信息使 `copier update` 能够计算差异并应用更新。

---

## 4. uv.lock 协同策略

uv.lock 的模板策略记录于 [ADR-0004](adr/ADR-0004-uv-lock-copier-strategy.md)。

**摘要**：生成时由 `_copier_postprocess.py` 将 uv.lock 中的根包名从模板项目名替换为派生项目名。Spike 验证表明此方案无需重新解析依赖，`uv lock --check` 和 `uv sync --frozen` 均通过。

---

## 5. 验证线束

`scripts/verify_generated.py` 是模板验证的单一有界入口：

```bash
uv run python scripts/verify_generated.py --gate g3
```

它负责：生成临时项目（默认答案）→ 就绪检查 → 数据库迁移 → 指定门槛测试 → 标识残留检查 → 清理。

前置条件：Git 工作树干净（Copier 使用 git 已提交状态）。

---

## 6. 模板版本号与 SPEC 对应

| 模板版本 | SPEC 门槛 | 说明 |
| --- | --- | --- |
| 0.1.x | G1–G4 | 初始基座，包含全部核心、安全、管理和生产就绪能力 |

模板版本号与 `pyproject.toml` 中的 `version` 字段一致。
