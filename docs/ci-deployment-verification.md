# CI 部署验收工作流手动验证指引 — SPEC 34.4 / 28.6

> 本文档指导用户在 GitHub Actions 页面手动验证 Docker 部署验收工作流。

## 1. 前置条件

- 当前 Run 分支已推送到 `origin`
- GitHub Actions 已启用（仓库 Settings → Actions → General → Allow all actions）
- 仓库有足够的 Actions 运行额度

## 2. 推送 Run 分支

```bash
# 将当前 Run 分支推送到 origin
git push origin apex-coding-agent/RUN-670b375c-8e98-46da-8fb6-0739db6f45aa
```

## 3. 打开 GitHub Actions 页面

1. 在浏览器中打开仓库的 GitHub 页面
2. 点击顶部 **Actions** 标签页
3. 在左侧工作流列表中找到 **Deploy Acceptance (G4)** 工作流
4. 点击进入最新的运行记录

## 4. 核对工作流 Job 通过状态

工作流包含以下 Job，全部应为绿色（✓）:

| 顺序 | Job 名称 | 说明 | 对应 SPEC 条目 |
|------|----------|------|----------------|
| 1 | 生成模板项目 | Copier 模板生成实例 | 30.3 / 34.4 |
| 2 | 本地 g4 测试子集 | 不依赖 Docker 的 g4 测试 | 34.4 |
| 3 | Compose 配置校验 | `docker compose config --quiet` | 28.6 / 34.4 |
| 4 | 镜像构建与容器检查 | 构建、nginx -t、非 root、无开发密钥 | 26.2 / 34.4 |
| 5 | 全栈集成测试 | Worker 一致性、HTTPS、限流、发布门禁等 | 34.4 全部 |

### 4.1 Job 依赖顺序

```
generate ──┬── g4-local（并行）
           │
           └── compose-validate ── image-checks ── stack-integration
```

- `g4-local` 和 Docker 验收链（`compose-validate → image-checks → stack-integration`）可并行运行
- `stack-integration` 必须等待 `image-checks` 通过后才执行

### 4.2 全栈集成测试覆盖的 34.4 条目

展开 **全栈集成测试** Job 的日志，确认以下步骤均为绿色:

1. **启动 Compose 全栈** — PostgreSQL、migrate、≥2 API Worker、Nginx 全部启动
2. **验证容器健康状态** — API Worker 数量 ≥ 2
3. **创建管理员用户** — 通过 CLI 创建测试管理员
4. **运行部署验收集成测试** — `deploy_acceptance.py` 运行结果:
   - 双 API Worker 一致性（会话吊销/权限变更/文件访问跨 Worker 生效）
   - HTTPS 重定向、安全头、Host 白名单、CORS、登录限流
   - 发布门禁（migrate 成功后 /health/ready 返回 200）
   - 优雅关闭（SIGTERM 后请求完成，连接释放）
   - 备份恢复演练与 RPO/RTO 报告
   - 私有文件禁止绕过授权下载
5. **清理 Compose 全栈** — `if: always` 确保容器和卷被清理

## 5. 同时验证 CI 工作流

在同一 Actions 页面，确认 **CI** 工作流也通过（G1-G3 静态检查与测试套件）。

## 6. 故障排查

如果工作流失败:

1. **生成模板项目失败**: 检查 Git 工作树是否干净，Copier 是否可用
2. **Compose 配置校验失败**: 检查 `.env.test` 中的环境变量是否满足必填校验
3. **镜像构建失败**: 检查 Dockerfile 基础镜像摘要是否有效
4. **nginx -t 失败**: 检查 Nginx 配置语法
5. **全栈集成测试失败**: 展开日志查看具体失败项，常见原因:
   - 健康检查超时: API 启动失败，检查环境变量
   - 登录失败: 管理员用户创建失败
   - 限流未触发: Nginx 配置或测试时序问题
6. **清理失败**: 手动在 Runner 上执行 `docker compose down -v`

## 7. 验收标准

工作流全部 Job 通过（绿色）即表示 SPEC 34.4 的 Docker 依赖条目在 CI 中验证通过。
结合本地静态验证（pytest --collect-only、本地 g4 子集、YAML 解析），全部 34.4 验收条件满足。
