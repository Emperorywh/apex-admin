# IAM 模块

`iam` 是账户、密码凭证、设备会话、refresh token 与 RBAC 的单一一致性边界。外部模块只能从 `public-api.ts` 导入稳定契约，不得直接注入仓储或查询 IAM 表。

## 数据流

```text
HTTP DTO
  → Controller
  → 单一 Use Case
  → Domain / UserPolicy
  → IamUnitOfWork 显式事务上下文
  → Prisma Repository + Mapper
  → PostgreSQL 约束
```

- 密码哈希、随机 token 生成和 JWT 签发在数据库事务外。
- 写事务统一遵循 `users → auth_sessions → refresh_tokens` 锁顺序。
- access JWT 是最多 15 分钟的授权快照，Guard 不查数据库。
- refresh replay 使用封闭事务结果，先提交 Session 吊销和审计，再返回业务错误。
- 禁用用户、吊销其全部活跃 Session 和安全审计在同一 Serializable 事务提交。

## 状态归属

- PostgreSQL：User、AuthSession、RefreshToken 与安全审计的唯一事实来源。
- JWT：短期、已签名、不可变的角色和权限快照。
- 请求对象：当前 actor 与 traceId；禁止存入单例成员。
- 内存限流：单副本部署约束，不是跨副本事实来源。

## 对外契约

- `Public`：只跳过 access Guard 和 Permissions Guard。
- `RequirePermissions`：声明路由所需的全部权限。
- `CurrentActor` / `AuthenticatedActor`：只读 access 授权快照。

API 契约见 [`docs/openapi.yaml`](../../../docs/openapi.yaml)，部署与故障处置见 [`docs/runbooks/iam-operations.md`](../../../docs/runbooks/iam-operations.md)。
