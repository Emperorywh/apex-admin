"""CSPRNG Token 生成器 — SPEC 12.1 / 12.2.

SPEC 12.1:
  - Access Token 使用密码学安全随机数生成器产生至少 256 bit 熵。

SPEC 12.2:
  - Refresh Token 使用至少 256 bit 熵。

使用 Python 标准库 ``secrets`` 模块作为 CSPRNG 源（基于操作系统提供
的密码学安全随机数生成器）。Token 以 URL-safe Base64 编码输出，
便于在 HTTP 头部和 Cookie 中使用。
"""

from __future__ import annotations

import secrets

TOKEN_ENTROPY_BYTES: int = 32
"""Token 随机字节数 — 32 字节 = 256 bit 熵，SPEC 12.1 / 12.2。"""


def generate_token() -> str:
    """生成密码学安全的随机 Token — SPEC 12.1 / 12.2.

    使用 ``secrets.token_urlsafe`` 从操作系统 CSPRNG 生成 32 字节
    随机数据（256 bit 熵），以 URL-safe Base64 编码为 43 字符字符串。

    返回:
        URL-safe Base64 编码的随机 Token 字符串，含至少 256 bit 熵。
    """

    return secrets.token_urlsafe(TOKEN_ENTROPY_BYTES)
