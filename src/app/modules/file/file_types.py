"""文件类型白名单与校验 — SPEC 19.2.

SPEC 19.2:
  - 使用白名单校验允许的文件类型。
  - 同时检查扩展名、声明类型和必要的文件内容特征。

校验三层:
  1. 扩展名白名单——不在白名单中的扩展名直接拒绝。
  2. 声明 MIME 类型与扩展名映射一致——不一致视为伪造。
  3. 文件内容特征（magic bytes）——与扩展名预期的签名不匹配视为伪造。

此模块为纯领域逻辑，不依赖 ORM 或数据库（SPEC 5.2）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FileTypeSpec:
    """文件类型规格 — 扩展名、MIME 和 magic bytes.

    属性:
        extension:   小写扩展名（不含点），如 ``"jpg"``。
        mime_types:  合法的 MIME 类型集合。
        magic_bytes: 文件头 magic bytes 候选列表（任一匹配即可通过）。
                     为空列表表示此类型无可靠的 magic bytes 签名
                     （如 ``txt``），跳过内容特征校验。
    """

    extension: str
    mime_types: tuple[str, ...]
    magic_bytes: tuple[bytes, ...]


#: 内置文件类型白名单 — 可通过配置覆盖或扩展.
#:
#: magic bytes 签名参考:
#:   - JPEG: FF D8 FF
#:   - PNG:  89 50 4E 47 0D 0A 1A 0A
#:   - GIF:  47 49 46 38 (GIF8)
#:   - PDF:  25 50 44 46 (%PDF)
#:   - ZIP:  50 4B 03 04 / 50 4B 05 06 / 50 4B 07 08
#:   - txt:  无可靠签名，跳过 magic bytes 校验
BUILTIN_FILE_TYPES: dict[str, FileTypeSpec] = {
    "jpg": FileTypeSpec(
        extension="jpg",
        mime_types=("image/jpeg",),
        magic_bytes=(b"\xff\xd8\xff",),
    ),
    "jpeg": FileTypeSpec(
        extension="jpeg",
        mime_types=("image/jpeg",),
        magic_bytes=(b"\xff\xd8\xff",),
    ),
    "png": FileTypeSpec(
        extension="png",
        mime_types=("image/png",),
        magic_bytes=(b"\x89PNG\r\n\x1a\n",),
    ),
    "gif": FileTypeSpec(
        extension="gif",
        mime_types=("image/gif",),
        magic_bytes=(b"GIF87a", b"GIF89a"),
    ),
    "pdf": FileTypeSpec(
        extension="pdf",
        mime_types=("application/pdf",),
        magic_bytes=(b"%PDF",),
    ),
    "txt": FileTypeSpec(
        extension="txt",
        mime_types=("text/plain",),
        magic_bytes=(),  # 无可靠 magic bytes
    ),
    "csv": FileTypeSpec(
        extension="csv",
        mime_types=("text/csv", "text/plain"),
        magic_bytes=(),
    ),
    "zip": FileTypeSpec(
        extension="zip",
        mime_types=("application/zip", "application/x-zip-compressed"),
        magic_bytes=(b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"),
    ),
    "json": FileTypeSpec(
        extension="json",
        mime_types=("application/json", "text/plain"),
        magic_bytes=(),
    ),
}


def extract_extension(filename: str) -> str:
    """从文件名提取小写扩展名（不含点）.

    无扩展名时返回空字符串。
    """

    import os

    _, ext = os.path.splitext(filename)
    return ext.lstrip(".").lower()


def validate_extension(
    extension: str,
    allowed_types: dict[str, FileTypeSpec],
) -> FileTypeSpec:
    """校验扩展名是否在白名单中.

    参数:
        extension:    小写扩展名（不含点）。
        allowed_types: 允许的文件类型白名单。

    返回:
        匹配的 ``FileTypeSpec``。

    抛出:
        FileExtensionNotAllowedError: 扩展名不在白名单中。
    """

    from app.modules.file.errors import FileExtensionNotAllowedError

    spec = allowed_types.get(extension)
    if spec is None:
        raise FileExtensionNotAllowedError(
            f"文件扩展名 '{extension}' 不在允许列表中",
        )
    return spec


def validate_content_type(declared_mime: str, spec: FileTypeSpec) -> None:
    """校验声明的 MIME 类型与扩展名映射是否一致.

    SPEC 19.2: "同时检查扩展名、声明类型和必要的文件内容特征"。

    参数:
        declared_mime: 客户端声明的 Content-Type。
        spec:          文件类型规格。

    抛出:
        FileTypeError: 声明类型与扩展名预期的 MIME 不一致（伪造）。
    """

    from app.modules.file.errors import FileTypeError

    if declared_mime not in spec.mime_types:
        raise FileTypeError(
            f"声明的 MIME 类型 '{declared_mime}' 与扩展名 "
            f"'{spec.extension}' 预期的 {spec.mime_types} 不一致",
        )


def validate_magic_bytes(data_head: bytes, spec: FileTypeSpec) -> None:
    """校验文件内容特征（magic bytes）.

    SPEC 19.2: "必要的文件内容特征"。

    如果该类型无 magic bytes 签名（空元组），跳过校验。
    否则检查文件头是否匹配任一预期签名。

    参数:
        data_head: 文件头部字节（至少覆盖最长签名长度）。
        spec:      文件类型规格。

    抛出:
        FileTypeError: 内容特征与扩展名不一致（伪造）。
    """

    from app.modules.file.errors import FileTypeError

    if not spec.magic_bytes:
        return

    for signature in spec.magic_bytes:
        if data_head.startswith(signature):
            return

    raise FileTypeError(
        f"文件内容特征与扩展名 '{spec.extension}' 不匹配，疑似伪造",
    )
