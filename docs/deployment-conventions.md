# 部署约定 — HTTPS 终结与可信代理头

> SPEC 23.1 / 26.3 / 24.3

本文档记录生产环境的 HTTPS 终结、反向代理头信任边界与生产日志输出约定。
这些约定在 G4 阶段（Production Ready）落地，G1–G3 不强制执行。

## 1. HTTPS 由 Nginx 终结

**约定：** 应用进程自身不处理 TLS 证书。HTTPS 在 Nginx 反向代理层终结，
Nginx 与 API 容器之间使用明文 HTTP 通信。

```
客户端 ──HTTPS──→ Nginx ──HTTP──→ FastAPI（uvicorn）
```

**原因：**

- 证书管理与续期集中在 Nginx 层，API 进程无需感知证书。
- API Worker 可专注于业务逻辑，不承担 TLS 握手的 CPU 开销。
- 符合 SPEC 26.3: "固定使用 Nginx stable 官方镜像负责 HTTPS"。

**约束：**

- API 容器只监听内部网络接口，不直接暴露到公网。
- Nginx 配置中必须设置 `proxy_ssl_certificate` 和 `proxy_ssl_certificate_key`。
- Nginx 与 API 之间的通信必须位于 Docker Compose 的隔离网络内。

## 2. 可信代理头信任边界

**约定：** 应用仅接受来自配置的可信代理来源（`APEX_TRUSTED_PROXIES`）的
X-Forwarded-* 代理头。非可信来源发送的代理头被完全忽略。

**配置方式：**

```bash
# 设置可信代理 IP（通常是 Nginx 容器的 IP 或内部网段）
APEX_TRUSTED_PROXIES=127.0.0.1,10.0.0.0/8
```

**信任规则：**

| 请求来源 | X-Forwarded-For 处理 | X-Forwarded-Proto 处理 |
| --- | --- | --- |
| 在 `TRUSTED_PROXIES` 中 | 采纳最左侧值作为客户端 IP | 采纳作为原始协议 |
| 不在 `TRUSTED_PROXIES` 中 | **忽略**，使用直接连接 IP | **忽略**，使用连接协议 |

**安全保证：** 直连客户端伪造 `X-Forwarded-For: 1.2.3.4` 头时，
若客户端 IP 不在 `TRUSTED_PROXIES` 中，伪造头完全不生效。
应用记录和使用的客户端 IP 始终是直接连接的真实来源 IP。

**部署约束（SPEC 26.3）：**

- API 容器只接受来自 Nginx 网络的代理流量。
- `TRUSTED_PROXIES` 必须设置为仅包含 Nginx 容器的 IP 地址或网段。
- 禁止将 `TRUSTED_PROXIES` 设置为 `0.0.0.0/0`（信任所有来源），
  这等同于禁用代理头验证。

## 3. 可信 Host 白名单

**约定：** 生产环境必须配置可信 Host 白名单，拒绝 Host 头不在白名单中的请求。

**配置方式：**

```bash
APEX_TRUSTED_HOSTS=admin.example.com,api.example.com
```

**安全保证：** 生产环境禁止使用通配 `*`。Settings 构造时校验，
通配或缺白名单将导致应用启动失败。

## 4. CORS 来源白名单

**约定：** 生产环境 CORS 必须使用明确的来源白名单。

**配置方式：**

```bash
APEX_ALLOWED_ORIGINS=https://admin.example.com,https://www.example.com
```

**安全保证：** 生产环境禁止使用通配 `*`。Settings 构造时校验，
通配或缺白名单将导致应用启动失败。

## 5. 生产日志输出约定

**约定：** 生产环境日志固定向标准输出（stdout）输出一行一个 JSON 对象的
结构化日志。

- API Worker **不**直接使用进程内 `RotatingFileHandler` 写共享文件。
- 落盘文件由进程外日志收集器（如 Docker 容器日志驱动、Fluentd 等）统一写入和轮转。
- 本地直接进程（开发/诊断）只输出到终端。

**输出格式示例：**

```json
{"timestamp":"2026-08-12T10:00:00Z","level":"info","event":"request","method":"GET","path":"/api/v1/meta","status_code":200,"duration_ms":1.23,"environment":"production","request_id":"a1b2c3d4"}
```

每行严格为一条 JSON 对象，不含换行符（换行符在日志内容中被转义为 `\n`）。

## 6. 请求体大小限制

**约定：** HTTP 层对请求体大小施加限制，防止超大请求体导致的资源耗尽。

| 请求类型 | 限制项 | 默认值 |
| --- | --- | --- |
| 常规请求（JSON API） | `APEX_MAX_REQUEST_BODY_SIZE` | 1 MiB |
| 上传请求（multipart/form-data） | `APEX_MAX_UPLOAD_BODY_SIZE` | 50 MiB |

超过限制的请求返回 HTTP 413（Payload Too Large）。

上传接口的文件大小和数量在应用层进一步限制（`APEX_FILE_MAX_SIZE_BYTES`、
`APEX_FILE_MAX_UPLOAD_COUNT`），HTTP 层的请求体限制是第一道防线。

## 7. /metrics 端点暴露边界

> SPEC 24.2: "指标接口受到访问限制"

**约定：** `/metrics` 端点通过部署配置令牌保护，且 **不经 Nginx 对外暴露**。
仅允许内网（Docker Compose 隔离网络）的 Prometheus 实例通过令牌抓取。

### 访问控制

- `/metrics` 要求 `Authorization: Bearer <token>` 头，令牌值必须与
  `APEX_METRICS_TOKEN` 精确匹配。
- 无有效令牌时返回 HTTP 403。
- 生产环境 **必须** 设置 `APEX_METRICS_TOKEN`（未设置时启动失败）。

### Nginx 配置约定

Nginx 反向代理 **不得** 将 `/metrics` 路径代理到 API 容器。
Prometheus 抓取应直接访问 API 容器的内网地址（如 `http://api:8000/metrics`），
绕过 Nginx。

示例 Nginx 配置（显式排除 `/metrics`）：

```nginx
location /metrics {
    return 404;
}
```

### Docker Compose 网络约定

- API 容器仅通过 Docker Compose 内部网络暴露 `/metrics`，
  不映射到宿主机公网端口。
- Prometheus 容器加入同一 Compose 网络，直连 API 容器抓取。

### 配置

| 配置项 | 说明 | 默认值 |
| --- | --- | --- |
| `APEX_METRICS_TOKEN` | /metrics 访问令牌（Bearer Token） | 生产环境必须设置 |
| `APEX_SLOW_REQUEST_THRESHOLD_MS` | 慢请求阈值（毫秒），超限记录结构化日志 | 2000 |
| `APEX_SLOW_QUERY_THRESHOLD_MS` | 慢查询阈值（毫秒），超限记录结构化日志 | 500 |

