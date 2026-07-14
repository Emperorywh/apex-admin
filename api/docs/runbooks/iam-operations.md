# IAM 部署与故障处置 Runbook

## 发布顺序

1. 使用 Migration Role 注入 `MIGRATION_DATABASE_URL`，执行 `pnpm prisma:migrate:deploy`。
2. 使用受控 Bootstrap Role 注入 Seed 配置，执行 `pnpm seed:super-admin`。
3. 使用最小权限 Runtime Role 注入 Runtime 配置，启动 `dist/main`。
4. 检查 `/v1/health/live` 与 `/v1/health/ready`。

Runtime、Migration、Seed 的变量集合彼此独立；不得把 `DATABASE_URL` 当作迁移连接串 fallback。完整非敏感模板见 [`.env.example`](../../.env.example)。

## JWT HS256 换钥

本系统固定单密钥且无 `kid`，不能滚动混用新旧密钥：

1. 停止接收新流量并协调停止全部旧实例。
2. 生成至少 32 随机字节，严格 base64 编码后写入 Secret 管理系统。
3. 同时启动全部使用新 `JWT_ACCESS_SECRET_BASE64` 的实例。
4. 旧 access token 立即失效；未撤销 refresh Session 可以换取新 access。
5. 验证认证指标后销毁旧密钥材料，禁止保留旧密钥 fallback。

## Refresh replay

- `REFRESH_TOKEN_STALE`：5 秒宽限内的并发陈旧请求，不吊销 Session，不清 Cookie。客户端等待共享 Cookie 更新后最多重试一次。
- `REFRESH_TOKEN_REPLAY`：宽限外旧 token 重用，Session 已被持久化吊销，并写入 `SESSION_REVOKED` 与 `REFRESH_REPLAY_DETECTED`。
- 调查时使用独立审计查询 Role 按 `session_id`、`target_user_id`、`correlation_id` 查询，Runtime Role 不具有审计读取权限。

## 常见故障

- `CONCURRENT_MODIFICATION`：检查 PostgreSQL `40001/40P01/55P03`、锁等待和统一锁序；客户端可以重放原请求，服务端不会无限重试。
- `RATE_LIMIT_EXCEEDED`：遵循 `Retry-After`。若副本数大于 1，停止扩容并先实施 Redis 原子限流规格，不启用内存 fallback。
- readiness 失败：检查 Runtime Role 连接、TLS、连接池预算和 PostgreSQL 可用性；不要切换到 Migration Role 运行应用。
- Bootstrap 冲突：同邮箱若不是 ACTIVE SUPER_ADMIN 必须人工核查，不允许脚本自动改角色、启用或覆盖密码。

禁止在日志或工单中粘贴密码、Authorization、Cookie、明文 refresh token、token hash 或数据库连接串。
