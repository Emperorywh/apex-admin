"""冒烟测试.

验证 pytest marker 注册、strict-markers 生效以及应用包可正常导入。
使用 ``g1`` 门槛 marker 与 ``unit`` 类型 marker，符合 SPEC 28 的双标记要求。
"""

import pytest


@pytest.mark.g1
@pytest.mark.unit
def test_app_package_importable() -> None:
    """冒烟测试：应用顶层包可正常导入，证明 src 布局与包结构正确。"""

    import app

    assert app.__name__ == "app"
