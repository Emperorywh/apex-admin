# 数据字典稳定值约定 — SPEC 17.2

## 业务持久化稳定值，而非展示文本

SPEC 17.2: "业务数据持久化稳定值，而不是展示文本"。

### 约定

1. **字典项有两个关键字段：**
   - `label`：显示文本，人类可读，可随 UI 需求或国际化变更。
   - `value`：稳定值，业务模块持久化此值，不随显示文本变更。

2. **业务模块持久化规则：**
   - 业务模块在数据库中存储字典项的 `value`（稳定值），不存储 `label`（展示文本）。
   - 需要展示时，通过稳定值反向查询字典项获取当前 `label`。

3. **稳定值不变性：**
   - 字典项创建后，其 `value` 原则上不变更（更新字典项时虽可修改 `value`，
     但已持久化的业务数据不会自动跟随更新）。
   - 如需变更稳定值，应创建新的字典项并迁移业务数据。

4. **引用登记（SPEC 17.1: 删除保护）：**
   - 业务模块在持久化稳定值时，应通过 `ReferenceRegistryPort` 登记对字典类型的引用。
   - 登记内容：被引用的字典类型编码（`dict_type_code`）、引用方模块编码
     （`module_code`）、引用方资源标识（`resource_id`）。
   - 引用登记使用复合唯一约束保证幂等——重复登记同一三元组不产生重复记录。
   - 被引用登记的字典类型不可删除（返回 `DICT.TYPE_REFERENCED` 冲突错误）。

### 示例

```python
# 业务模块持久化稳定值（非展示文本）
await db.execute(
    "INSERT INTO my_entity (status_dict_value) VALUES (:value)",
    {"value": "active"},  # ← 稳定值，不是 "启用"
)

# 登记引用（SPEC 17.1: 删除保护）
registry = SqlAlchemyReferenceRegistry(session)
await registry.register_reference(
    dict_type_code="user_status",
    module_code="identity",
    resource_id=str(user_id),
    created_at=clock.now(),
)
```
