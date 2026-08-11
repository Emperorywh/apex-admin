# Nginx 反向代理配置说明 — 唯一受支持的配置

> SPEC 26.3 / 23.4 / 24.2

**本文档描述的是 Apex Admin 交付的唯一受支持的 Nginx 反向代理配置。**
不得在部署时添加第二套反向代理配置（SPEC 26.3）。

配置文件位置: `deploy/nginx/apex.conf`
部署挂载位置: `/etc/nginx/conf.d/apex.conf`（由 `docker-compose` 只读挂载）

## 1. HTTPS

应用进程自身不处理 TLS 证书。HTTPS 在 Nginx 反向代理层终结，
Nginx 与 API 容器之间使用明文 HTTP 通信。

```
客户端 ──HTTPS──→ Nginx (443) ──HTTP──→ FastAPI (api:8000)
```

- HTTP 端口 80 自动 301 重定向到 HTTPS。
- TLS 证书挂载在 `/etc/nginx/certs/` 目录（Docker 卷 `certs`）。
- TLS 协议仅允许 TLSv1.2 和 TLSv1.3。

证书文件:

| 文件 | 说明 |
| --- | --- |
| `/etc/nginx/certs/fullchain.pem` | 证书链（含中间证书） |
| `/etc/nginx/certs/privkey.pem` | 私钥 |

> 证书申请自动化不在本配置范围内（SPEC TASK-032 nonGoals）。

## 2. 可信代理头

Nginx 向 API 容器传递以下代理头，API 仅信任来自 `APEX_TRUSTED_PROXIES` 配置的来源:

```
proxy_set_header Host              $host;
proxy_set_header X-Real-IP         $remote_addr;
proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
```

`APEX_TRUSTED_PROXIES` 应设置为仅包含 Nginx 容器地址（默认 `nginx`）。
详见 [部署约定](deployment-conventions.md) 第 2 节。

## 3. 上传大小限制

| 请求类型 | location | client_max_body_size |
| --- | --- | --- |
| 普通 API | `/api/v1/` | 1m |
| 文件上传 | `/api/v1/files` | 50m |
| 文件下载 | `/api/v1/files/{id}/download` | 1m |
| 登录 | `/api/v1/auth/login` | 1m |
| 刷新 | `/api/v1/auth/refresh` | 1m |

上传大小限制与应用层 `APEX_MAX_UPLOAD_BODY_SIZE`（50 MiB）一致，
作为 HTTP 层第一道防线（SPEC 23.1）。

## 4. 限流规则（SPEC 23.4）

单机固定由 Nginx `limit_req` 执行入口 IP 限流，应用层不重复实现。

| 接口 | zone | rate | 说明 |
| --- | --- | --- | --- |
| `POST /api/v1/auth/login` | `login_limit` | 10r/m | 每可信客户端 IP 每分钟 10 次 |
| `POST /api/v1/auth/refresh` | `refresh_limit` | 30r/m | 每可信客户端 IP 每分钟 30 次 |

- 限流 key 使用 `$binary_remote_addr`（已由 Nginx 从可信代理链解析为真实客户端 IP）。
- 超出限制返回 HTTP 429（`limit_req_status 429`）。
- 账号维度的失败限制由应用层 PostgreSQL 状态实现（SPEC 12.4），不依赖进程内计数。

## 5. 三类超时配置

SPEC 26.3 要求普通 API 请求超时、上传请求超时和下载流式超时分别显式配置。

| 类型 | location | connect | send | read |
| --- | --- | --- | --- | --- |
| 普通 API | `/api/v1/` | 10s | 30s | 30s |
| 上传请求 | `/api/v1/files` | 10s | 120s | 120s |
| 下载流式 | `/api/v1/files/{id}/download` | 10s | 60s | 300s |
| 登录 | `/api/v1/auth/login` | 10s | 30s | 30s |
| 刷新 | `/api/v1/auth/refresh` | 10s | 30s | 30s |
| 健康检查 | `/health/` | 5s | 10s | 10s |

上传超时允许大文件传输完成；下载流式超时允许流式响应持续传输。

## 6. /metrics 不对外暴露（SPEC 24.2）

```nginx
location = /metrics {
    return 404;
}
```

- `/metrics` 端点 **不经 Nginx 对外暴露**。
- Prometheus 抓取应直接访问 API 容器的内网地址（如 `http://api:8000/metrics`），
  通过 Bearer Token 鉴权（SPEC 24.2）。
- 详见 [部署约定](deployment-conventions.md) 第 7 节。

## 7. 私有文件禁直出（SPEC 26.3）

所有 `location` 块仅通过 `proxy_pass` 代理到上游 API Worker 或直接 `return`。

- 禁止使用 `root` 指令提供静态文件。
- 禁止使用 `alias` 指令映射文件目录。
- 文件下载经 `/api/v1/files/{id}/download` 路由由应用层授权后流式返回，
  Nginx 不绕过应用授权直接暴露文件。

## 8. location 匹配规则

| 优先级 | 匹配规则 | location |
| --- | --- | --- |
| 1 | 精确匹配 | `= /metrics` |
| 2 | 精确匹配 | `= /api/v1/auth/login` |
| 3 | 精确匹配 | `= /api/v1/auth/refresh` |
| 4 | 精确匹配 | `= /api/v1/files` |
| 5 | 正则匹配 | `~ ^/api/v1/files/[0-9a-fA-F-]+/download$` |
| 6 | 前缀匹配 | `/api/v1/` |
| 7 | 前缀匹配 | `/health/` |
| 8 | 前缀匹配 | `/`（默认 404） |

未声明的路径（`location /`）返回 404，不代理到应用。

## 9. 静态 lint 脚本

`scripts/lint_nginx.py` 校验配置必备指令齐全:

```bash
python scripts/lint_nginx.py
```

退出码 0 表示全部校验通过。`nginx -t` 语法验证移交 TASK-035 CI 门禁在 Docker 容器内执行。

## 10. compose 镜像

Nginx 使用 `nginx:stable` 官方镜像并固定摘要（SPEC 26.1）:

```yaml
nginx:
  image: nginx:stable@sha256:<64-hex-digest>
```

摘要更新方式:

```bash
docker pull nginx:stable
docker inspect --format '{{index .RepoDigests 0}}' nginx:stable
```
