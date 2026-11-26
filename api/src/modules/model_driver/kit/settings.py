"""驱动配置的 mtime 指纹缓存 —— 兼容层。

实现上移到 `infra/env_settings.py`：白名单（image / library 模块）也要用这套
热生效机制，而业务模块之间禁止 import（`tests/test_architecture.py`）。

本文件保留原 import 路径，五个驱动与 `app doctor` 的调用点都不用改。
新代码请直接从 `infra.env_settings` 导入。
"""

from infra.env_settings import clear_cache, config_fingerprint, vendor_settings

__all__ = ["clear_cache", "config_fingerprint", "vendor_settings"]
