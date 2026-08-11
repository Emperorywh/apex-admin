# 部署指南 — 发布停机切换与发布门禁

> SPEC 26.1: 单机部署（G4）

本文档定义 Apex Admin 的生产发布流程，包括停机切换顺序、发布门禁逻辑和回滚步骤。

## 1. 发布前置条件

- Docker Compose v2 已安装（SPEC 5.4）
- `deploy/.env.production` 已配置全部必需环境变量（参见 `deploy/.env.example`）
- 镜像已构建并推送到可用 registry（或本地构建）
- 数据库已备份（SPEC 27.1）

## 2. 停机切换顺序（SPEC 26.1）

发布固定采用**停机切换**（downtime switch），不支持新旧版本混跑。

### 2.1 为什么不使用滚动更新

Apex Admin 使用 Alembic 管理数据库 schema 版本。新旧版本混跑会导致：

- 旧版本 API 在新 schema 上出现运行时错误
- 新版本 API 在旧 schema 上就绪检查失败（revision 不匹配）
- 事务一致性风险

因此发布必须按**停旧版 → 迁移 → 启新版**的顺序执行。

### 2.2 发布步骤

```text
步骤 1: 停止旧版本
    docker compose --env-file .env.production stop api nginx
    → 旧版本 API 停止接受新请求
    → 进行中的请求在优雅关闭超时内完成
    → 数据库连接池释放（lifespan shutdown → engine.dispose()）

步骤 2: 执行数据库迁移
    docker compose --env-file .env.production run --rm migrate
    → 迁移服务执行 alembic upgrade head
    → 迁移成功（退出码 0）后才允许启动新版本 API

步骤 3: 启动新版本
    docker compose --env-file .env.production up -d api nginx
    → 新版本 API 启动
    → 健康检查通过后 Nginx 开始代理流量
```

### 2.3 一键发布（推荐）

`docker compose up` 内置了服务依赖顺序。compose.yaml 中通过 `depends_on` 条件确保：

```text
postgres (service_healthy)
    ↓
migrate (service_completed_successfully)
    ↓
api (service_started)
    ↓
nginx (service_healthy)
```

更新镜像版本后执行：

```bash
docker compose --env-file .env.production up -d --build
```

Compose 会按依赖顺序重启服务。migrate 服务作为一次性容器运行，成功退出后 api 才启动。

## 3. 发布门禁逻辑（SPEC 26.1）

### 3.1 迁移门禁

compose.yaml 中 api 服务配置：

```yaml
api:
  depends_on:
    migrate:
      condition: service_completed_successfully
```

- migrate 服务执行 `python -m app.cli db upgrade`
- 退出码 0 → `service_completed_successfully` 满足 → api 允许启动
- 退出码非 0 → 条件不满足 → api **不启动**，发布失败

### 3.2 未迁移不就绪

SPEC 26.1: "未执行迁移时新版本就绪检查必须失败。"

`/health/ready` 端点（SPEC 6.2）验证数据库的当前 Alembic revision 与应用 head revision 一致。

- 迁移未执行时，数据库 revision 落后于应用 head revision
- `/health/ready` 返回 HTTP 503
- Nginx 的 `health_check` 依赖 `/health/ready`，不就绪的 API Worker 不接收流量

这意味着即使 compose 强制启动了 API 容器，未迁移的数据库会使就绪检查持续失败，
服务无法对外提供请求。

### 3.3 就绪检查内容

```
GET /health/ready
→ 200: 数据库可达 AND 当前 revision == 应用 head revision
→ 503: 数据库不可达 OR revision 不匹配
```

## 4. 优雅关闭（SPEC 26.1）

### 4.1 应用层

应用使用 FastAPI Lifespan 管理生命周期（SPEC 6.1）：

- **关闭阶段**: `await engine.dispose()` 释放数据库连接池中的全部连接
- Lifespan 上下文管理器确保关闭钩子在应用退出前执行

源码位置: `src/app/main.py` — `lifespan` 函数

### 4.2 容器层

Docker Compose 发送 `SIGTERM` 后：

1. uvicorn 收到信号，停止接受新请求
2. 进行中的请求在超时窗口内完成（默认 30 秒）
3. FastAPI Lifespan shutdown 钩子执行，释放数据库连接池
4. 进程退出

## 5. 回滚

### 5.1 回滚到旧版本

```bash
# 1. 停止新版本
docker compose --env-file .env.production stop api nginx

# 2. 回滚数据库迁移（如迁移可逆）
docker compose --env-file .env.production run --rm migrate python -m app.cli db downgrade -1

# 3. 使用旧版本镜像重启
APP_VERSION=<previous-version> docker compose --env-file .env.production up -d api nginx
```

### 5.2 回滚注意事项

- 数据库迁移必须可逆（Alembic downgrade），否则只能从备份恢复
- 回滚后 `/health/ready` 的 revision 检查会验证回滚后的 revision 与旧版本匹配
- 丢失的增量数据无法通过回滚恢复，需从备份恢复（SPEC 27.3）

## 6. Docker 构建与验证门禁

本机无 Docker 环境，以下验证项移交 TASK-035 CI 门禁执行：

- `docker compose config --quiet` — compose 配置有效性
- 镜像构建 — Dockerfile 构建成功
- 容器内 `nginx -t` — Nginx 配置有效性
- 非 root 用户验证 — `docker exec <container> id` 输出 uid != 0
- 健康检查 — 全部容器达到预期终态

SPEC 28.6 / 34.4 要求以上全部通过才允许标记 G4 完成。
