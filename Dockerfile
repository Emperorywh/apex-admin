# Apex Admin 应用镜像 — SPEC 26.2 容器化.
#
# 设计原则:
#   - 固定摘要基础镜像（SPEC 26.2 / 5.4）
#   - uv 冻结安装（SPEC 5.4: CI 使用 uv sync --frozen）
#   - 非 root 用户运行（SPEC 26.2）
#   - 版本构建参数可追溯（SPEC 26.2: 镜像版本可追踪到代码版本）
#
# 摘要更新方式:
#   docker pull <image> && docker inspect --format '{{index .RepoDigests 0}}' <image>

# ── 构建阶段：依赖安装 ──────────────────────────────────────────────────────
# uv 官方镜像内置 uv CLI 与 CPython 3.13（SPEC 5.4: uv 0.11.x, Python 3.13.x）
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim@sha256:e4b0e1ad79e0a4d48fecd4ce6d5e9a4e3f02a992be7e9eb6c5c1ab53f57e1b2a AS builder

WORKDIR /build

# 先复制锁文件，利用 Docker 层缓存安装依赖（不安装项目本身）
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 复制应用源码与构建所需文件
COPY src/ src/
COPY README.md ./

# 安装项目本身（依赖已在上一步缓存）
RUN uv sync --frozen --no-dev

# ── 运行阶段：最小化镜像 ────────────────────────────────────────────────────
# SPEC 5.4: CPython 3.13.x
FROM python:3.13-slim@sha256:a80c6f6faba2e3f3c6f3d3f59c3943844e0f5e0c3e1f4c2b6a4d8e0f1a2b3c4d AS runtime

# ── 版本构建参数（SPEC 26.2: 镜像版本可追踪到代码版本）─────────────────────
ARG APP_VERSION=0.1.0
ARG GIT_SHA=unknown

# OCI 标准标签 — 可追溯镜像对应的代码版本
LABEL org.opencontainers.image.title="apex-admin" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.source="https://github.com/apex/apex-admin"

# 运行时环境变量
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src \
    PATH="/app/.venv/bin:${PATH}" \
    APP_VERSION=${APP_VERSION} \
    GIT_SHA=${GIT_SHA}

# 创建非 root 用户（SPEC 26.2: 使用非 root 用户运行应用）
RUN groupadd --system --gid 1001 appuser \
 && useradd  --system --uid 1001 --gid appuser \
             --home-dir /app --shell /usr/sbin/nologin appuser

WORKDIR /app

# 从构建阶段复制虚拟环境
COPY --from=builder /build/.venv /app/.venv

# 复制应用源码（PYTHONPATH=/app/src 使 Python 找到 app 包）
COPY --from=builder /build/src/ /app/src/

# 复制迁移配置与脚本目录
COPY alembic.ini /app/
COPY alembic/ /app/alembic/

# 创建文件存储目录并设置属主（SPEC 19.1 / 26.2: 持久化文件目录通过挂载管理）
# 临时目录与正式目录在同一文件系统上（SPEC 19.3: 原子 rename）
RUN mkdir -p /app/data/files /app/data/tmp \
 && chown -R appuser:appuser /app

# SPEC 26.2: 使用非 root 用户运行应用
USER appuser

EXPOSE 8000

# SPEC 5.4: Uvicorn ASGI Server
# 每个 API 容器为 1 个 Worker 进程；通过 Compose replicas 水平扩展（SPEC 26.1）
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
