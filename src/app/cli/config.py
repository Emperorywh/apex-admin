"""config show 命令（SPEC §25.1）。

加载部署配置并输出脱敏后的摘要，敏感字段以 ``***`` 掩码，
数据库 URL 隐藏凭据部分。不泄露任何密钥明文。
"""

from __future__ import annotations

import json

from app.config.settings import Settings


def config_show() -> int:
    """输出脱敏后的运行配置摘要（SPEC §25.1）。

    加载部署配置，调用 :meth:`Settings.to_safe_summary` 生成脱敏摘要，
    以 JSON 格式输出到 stdout。

    Returns:
        退出码：成功返回 0，配置加载失败时异常传播由 CLI 入口处理
    """
    settings = Settings()  # type: ignore[call-arg]  # pydantic-settings 从环境变量加载
    summary = settings.to_safe_summary()
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0
