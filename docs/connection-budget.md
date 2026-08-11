# 数据库连接预算 — SPEC 26.1 容量计算

> SPEC 26.1: 数据库最大连接预算必须满足容量公式。

## 1. 预算公式

SPEC 26.1 定义的最大连接预算公式：

```
API Worker × 每 Worker (pool_size + max_overflow) + Worker/Scheduler/管理命令预留
    ≤ PostgreSQL max_connections - 监控与运维预留
```

## 2. 默认配置计算

### 2.1 参数

| 参数 | 默认值 | 来源 |
| --- | --- | --- |
| API Worker 数量 | 2 | SPEC 26.1 / compose.yaml `deploy.replicas: 2` |
| 每 Worker pool_size | 5 | `Settings.DB_POOL_SIZE` / `engine.py` `DEFAULT_POOL_SIZE` |
| 每 Worker max_overflow | 5 | `Settings.DB_MAX_OVERFLOW` / `engine.py` `DEFAULT_MAX_OVERFLOW` |
| PostgreSQL max_connections | 100 | PostgreSQL 默认值 |

### 2.2 计算

```
API 侧峰值连接数 = Worker 数量 × (pool_size + max_overflow)
                 = 2 × (5 + 5)
                 = 2 × 10
                 = 20
```

**API 侧峰值合计 20 个连接。**

### 2.3 预算余量

```
PostgreSQL max_connections              = 100
API 侧峰值                             = -20
────────────────────────────────────────────
剩余可用于管理命令 / 调度器 / 监控 / 运维 =  80
```

- 一次性 migrate 服务在发布窗口内短暂占用 1 个连接（迁移完成后释放）。
- 管理 CLI 命令（如 `auth create-admin`、`files reconcile`）在执行期间短暂占用连接。
- SPEC 明确定时调度器不在 API Worker 内启动（EXT 未建，无调度器进程）。
- 监控连接（如 Prometheus /metrics 抓取）不建立独立数据库连接，复用 API Worker 的连接池。

结论: 默认配置下 20 连接远低于 PostgreSQL 默认 `max_connections=100`，余量充足。

## 3. 修改前的容量计算（SPEC 26.1）

SPEC 26.1: "修改前必须完成容量计算。"

如需增加 Worker 数量或调整连接池参数，必须按以下步骤验证：

### 3.1 计算新配置的 API 侧峰值

```
新 API 侧峰值 = 新 Worker 数量 × (新 pool_size + 新 max_overflow)
```

### 3.2 验证不等式

```
新 API 侧峰值 + 预留 ≤ PostgreSQL max_connections - 监控预留

推荐预留:
  - 管理命令 / migrate:  ≥ 5 连接
  - 监控 / 运维:         ≥ 5 连接
  - 安全余量:            ≥ 10 连接

推荐: 新 API 侧峰值 ≤ max_connections × 0.7
```

### 3.3 示例: 扩展到 4 个 Worker

```
4 × (5 + 5) = 40 ≤ 100 × 0.7 = 70  ✓ 可行
```

### 3.4 示例: 不安全配置

```
8 × (5 + 5) = 80 > 100 × 0.7 = 70  ✗ 需要提高 PostgreSQL max_connections
```

## 4. 相关配置

| 配置项 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| 连接池常驻连接数 | `APEX_DB_POOL_SIZE` | 5 | 每 Worker 的 SQLAlchemy pool_size |
| 连接池溢出上限 | `APEX_DB_MAX_OVERFLOW` | 5 | 每 Worker 的 SQLAlchemy max_overflow |
| Worker 数量 | compose.yaml `deploy.replicas` | 2 | API 容器实例数 |
| PostgreSQL 最大连接 | PostgreSQL `max_connections` | 100 | 需在 PostgreSQL 配置中设置 |

## 5. 连接池行为

- `pool_pre_ping=True`: 使用连接前执行轻量探测，数据库恢复后无需重启进程即可重新获取连接（SPEC 6.2）。
- 连接在 `pool_size` 到 `pool_size + max_overflow` 之间按需创建。
- 优雅关闭时 `engine.dispose()` 释放全部连接（SPEC 6.1 / 26.1）。
