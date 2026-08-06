"""配置管理（SPEC §7.2 配置分类）。

本包将配置严格分为三类，各自由不同主体管理，禁止混合到同一个无边界配置模块中：

1. 部署配置（Deployment Settings）
   - 管理主体：运维环境（环境变量 / 受控配置文件）
   - 内容：数据库连接 URL、Token HMAC 密钥、敏感配置加密密钥、文件存储根目录、
     运行环境名称、CORS 允许来源等
   - 实现：本包的 :class:`~app.config.settings.Settings`
   - SPEC 条款：§7.1

2. 系统配置（System Settings）
   - 管理主体：后台管理员（通过管理界面）
   - 内容：系统级行为参数，持久化到 PostgreSQL 系统配置表
   - 实现：G3 阶段（SPEC §16），本阶段不实现
   - SPEC 条款：§16、§7.2

3. 业务配置（Business Settings）
   - 管理主体：具体业务模块
   - 内容：模块自身的行为参数，归属于模块自身的数据
   - 实现：各业务模块自行管理，本阶段不实现
   - SPEC 条款：§7.2
"""

from app.config.settings import AppEnv, Settings

__all__ = ["AppEnv", "Settings"]
