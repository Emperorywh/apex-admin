"""文件管理模块 API 契约测试 — SPEC 19.2 / 19.3 / 19.4 / 28.4.

覆盖:
  - 上传 API 契约（multipart/form-data）。
  - 下载 API 契约（Content-Disposition RFC 5987）。
  - 跨用户下载被拒绝（403）。
  - 通用文件管理接口权限。
  - 上传/删除审计。

使用 TestClient 对真实应用发请求，覆盖认证/权限依赖以聚焦路由契约。
连接真实 PostgreSQL（SPEC 28.2）。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from sqlalchemy import text
from starlette.testclient import TestClient

from app.application.context import UseCaseContext
from app.composition.modules import MODULE_VERSION_LOCATIONS
from app.core.config import Environment, Settings
from app.infrastructure.db.engine import create_db_engine
from app.main import create_app
from app.modules.auth.dependencies import get_authenticated_context_async
from app.modules.auth.permission import ActorAuthorization, get_actor_authorization

if TYPE_CHECKING:
    from collections.abc import Iterator


# ── 迁移与清理 ─────────────────────────────────────────────────────────────


async def _apply_migrations(database_url: str) -> None:
    """对测试数据库执行 alembic upgrade head。"""

    from alembic import command

    from app.infrastructure.db.migrations import get_alembic_config

    config = get_alembic_config(
        database_url=database_url,
        version_locations=MODULE_VERSION_LOCATIONS,
    )
    await asyncio.to_thread(lambda: command.upgrade(config, "head"))


async def _cleanup_tables(database_url: str) -> None:
    """清理文件与审计表。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM file_references"))
            await conn.execute(text("DELETE FROM file_metadata"))
            await conn.execute(text("DELETE FROM audit_logs"))
    finally:
        await engine.dispose()


# ── fixture ───────────────────────────────────────────────────────────────

_TEST_ACTOR_ID = "00000000-0000-0000-0000-0000000000ff"
_OTHER_ACTOR_ID = "00000000-0000-0000-0000-0000000000ee"

_SUPER_ADMIN_CTX = UseCaseContext(
    request_id="test-file-api-req",
    actor_id=_TEST_ACTOR_ID,
)


def _super_admin_auth_override() -> ActorAuthorization:
    """模拟超管授权。"""

    return ActorAuthorization(
        ctx=_SUPER_ADMIN_CTX,
        permissions=frozenset(),
        is_super_admin=True,
    )


def _super_admin_ctx_override() -> UseCaseContext:
    """模拟认证上下文。"""

    return _SUPER_ADMIN_CTX


def _other_user_ctx_override() -> UseCaseContext:
    """模拟其他用户认证上下文。"""

    return UseCaseContext(
        request_id="test-file-api-req",
        actor_id=_OTHER_ACTOR_ID,
    )


def _other_user_auth_override() -> ActorAuthorization:
    """模拟其他非管理员用户。"""

    return ActorAuthorization(
        ctx=UseCaseContext(
            request_id="test-file-api-req",
            actor_id=_OTHER_ACTOR_ID,
        ),
        permissions=frozenset(),
        is_super_admin=False,
    )


@pytest.fixture(scope="module")
def migrated_database_url(database_url: str) -> Iterator[str]:
    """对测试数据库执行迁移。"""

    asyncio.run(_apply_migrations(database_url))
    yield database_url


@pytest.fixture(autouse=True)
def _clean_tables(migrated_database_url: str) -> Iterator[None]:
    """每个测试前后清理全部表。"""

    asyncio.run(_cleanup_tables(migrated_database_url))
    yield
    asyncio.run(_cleanup_tables(migrated_database_url))


@pytest.fixture()
def api_client(
    migrated_database_url: str,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[TestClient]:
    """创建带超管权限的 TestClient。"""

    storage_root = tmp_path_factory.mktemp("file_storage")
    settings = Settings(
        ENVIRONMENT=Environment.TESTING,
        DATABASE_URL=migrated_database_url,
        FILE_STORAGE_ROOT=str(storage_root),
    )
    app = create_app(settings)
    app.dependency_overrides[get_authenticated_context_async] = (
        _super_admin_ctx_override
    )
    app.dependency_overrides[get_actor_authorization] = _super_admin_auth_override
    with TestClient(app) as client:
        yield client


@pytest.fixture()
def other_user_client(
    migrated_database_url: str,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[TestClient]:
    """创建非管理员用户的 TestClient。"""

    storage_root = tmp_path_factory.mktemp("file_storage_other")
    settings = Settings(
        ENVIRONMENT=Environment.TESTING,
        DATABASE_URL=migrated_database_url,
        FILE_STORAGE_ROOT=str(storage_root),
    )
    app = create_app(settings)
    app.dependency_overrides[get_authenticated_context_async] = _other_user_ctx_override
    app.dependency_overrides[get_actor_authorization] = _other_user_auth_override
    with TestClient(app) as client:
        yield client


# ── 辅助 ─────────────────────────────────────────────────────────────────


def _png_bytes() -> bytes:
    """最小化合法 PNG 文件内容。"""

    header = b"\x89PNG\r\n\x1a\n"
    ihdr = (
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    )
    ihdr_crc = b"\xa9\x00\x00\x00"
    idat = b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
    idat_crc = b"\x00\x00\x00\x00"
    iend = b"\x00\x00\x00\x00IEND\xaeB`\x82"
    return header + ihdr + ihdr_crc + idat + idat_crc + iend


# ═══════════════════════════════════════════════════════════════════════════════
# 上传 API
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.api
class TestUploadAPI:
    """上传 API 契约 — SPEC 19.2 / 19.3."""

    def test_upload_png_file(self, api_client: TestClient) -> None:
        """上传 PNG 文件返回 201 和正确元数据."""

        response = api_client.post(
            "/api/v1/files",
            files={"files": ("test.png", _png_bytes(), "image/png")},
        )
        assert response.status_code == 201
        data = response.json()
        assert len(data) == 1
        assert data[0]["status"] == "ready"
        assert data[0]["originalName"] == "test.png"
        assert data[0]["fileExtension"] == "png"

    def test_upload_txt_file(self, api_client: TestClient) -> None:
        """上传文本文件."""

        response = api_client.post(
            "/api/v1/files",
            files={"files": ("notes.txt", b"Hello World", "text/plain")},
        )
        assert response.status_code == 201
        data = response.json()
        assert data[0]["status"] == "ready"
        assert data[0]["fileExtension"] == "txt"

    def test_upload_forged_type_rejected(self, api_client: TestClient) -> None:
        """伪造文件类型被拒绝（400）."""

        # PNG 内容伪装为 JPG
        response = api_client.post(
            "/api/v1/files",
            files={"files": ("fake.jpg", _png_bytes(), "image/jpeg")},
        )
        assert response.status_code == 400

    def test_upload_disallowed_extension_rejected(
        self,
        api_client: TestClient,
    ) -> None:
        """不允许的扩展名被拒绝（409）."""

        response = api_client.post(
            "/api/v1/files",
            files={"files": ("malware.exe", b"MZ\x90\x00", "application/octet-stream")},
        )
        assert response.status_code == 409


# ═══════════════════════════════════════════════════════════════════════════════
# 下载 API
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.api
class TestDownloadAPI:
    """下载 API 契约 — SPEC 19.2 / 19.3 / 19.4."""

    def test_download_file_with_content_disposition(
        self,
        api_client: TestClient,
    ) -> None:
        """下载文件含 RFC 5987 Content-Disposition."""

        # 上传
        upload_resp = api_client.post(
            "/api/v1/files",
            files={"files": ("download.txt", b"Download me", "text/plain")},
        )
        file_id = upload_resp.json()[0]["id"]

        # 下载
        response = api_client.get(f"/api/v1/files/{file_id}/download")
        assert response.status_code == 200
        assert response.content == b"Download me"
        cd = response.headers.get("content-disposition", "")
        assert "filename*=UTF-8''download.txt" in cd

    def test_download_unicode_filename(self, api_client: TestClient) -> None:
        """非 ASCII 文件名 Content-Disposition RFC 5987 编码."""

        upload_resp = api_client.post(
            "/api/v1/files",
            files={"files": ("中文文件.txt", b"Unicode", "text/plain")},
        )
        file_id = upload_resp.json()[0]["id"]

        response = api_client.get(f"/api/v1/files/{file_id}/download")
        assert response.status_code == 200
        cd = response.headers.get("content-disposition", "")
        assert "filename*=UTF-8''" in cd
        assert "%E4%B8%AD" in cd  # '中' 的 UTF-8 编码

    def test_cross_user_download_forbidden(
        self,
        api_client: TestClient,
        other_user_client: TestClient,
    ) -> None:
        """跨用户下载被拒绝（403）— SPEC 19.4."""

        # admin 上传
        upload_resp = api_client.post(
            "/api/v1/files",
            files={"files": ("secret.txt", b"secret", "text/plain")},
        )
        file_id = upload_resp.json()[0]["id"]

        # 其他用户下载（非 admin）
        response = other_user_client.get(f"/api/v1/files/{file_id}/download")
        assert response.status_code == 403

    def test_download_nonexistent_returns_not_found(
        self,
        api_client: TestClient,
    ) -> None:
        """下载不存在的文件返回 404."""

        response = api_client.get(f"/api/v1/files/{uuid4()}/download")
        assert response.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# 查询与删除 API
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.api
class TestFileManagementAPI:
    """文件管理 API 契约 — SPEC 19.1 / 19.3 / 19.4."""

    def test_get_file_detail(self, api_client: TestClient) -> None:
        """查询文件详情."""

        upload_resp = api_client.post(
            "/api/v1/files",
            files={"files": ("detail.txt", b"content", "text/plain")},
        )
        file_id = upload_resp.json()[0]["id"]

        response = api_client.get(f"/api/v1/files/{file_id}")
        assert response.status_code == 200
        assert response.json()["id"] == file_id

    def test_list_my_files(self, api_client: TestClient) -> None:
        """查询当前用户文件列表."""

        api_client.post(
            "/api/v1/files",
            files={"files": ("list1.txt", b"a", "text/plain")},
        )
        api_client.post(
            "/api/v1/files",
            files={"files": ("list2.txt", b"b", "text/plain")},
        )

        response = api_client.get("/api/v1/files")
        assert response.status_code == 200
        assert len(response.json()) >= 2

    def test_delete_file(self, api_client: TestClient) -> None:
        """删除文件置 DELETING."""

        upload_resp = api_client.post(
            "/api/v1/files",
            files={"files": ("delete.txt", b"del", "text/plain")},
        )
        file_id = upload_resp.json()[0]["id"]

        response = api_client.delete(f"/api/v1/files/{file_id}")
        assert response.status_code == 204

        # 验证状态
        detail = api_client.get(f"/api/v1/files/{file_id}")
        assert detail.json()["status"] == "deleting"

    def test_cross_user_delete_forbidden(
        self,
        api_client: TestClient,
        other_user_client: TestClient,
    ) -> None:
        """跨用户删除被拒绝（403）— SPEC 19.4."""

        upload_resp = api_client.post(
            "/api/v1/files",
            files={"files": ("protected.txt", b"p", "text/plain")},
        )
        file_id = upload_resp.json()[0]["id"]

        response = other_user_client.delete(f"/api/v1/files/{file_id}")
        assert response.status_code == 403
