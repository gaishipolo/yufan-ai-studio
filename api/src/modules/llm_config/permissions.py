"""LLM 配置页权限点（前缀与模块目录名一致）。

页面码 `page:llm` 与接口码分开：关掉页面时接口也跟着关。
写权限不挂在读上 —— 能看供应商列表（含密钥指纹）的人未必该能改网关和密钥。
"""

from core.permissions import PermissionSpec

PERM_READ = "llm_config:read"
PERM_WRITE = "llm_config:write"
PAGE = "page:llm"

SPECS = (
    PermissionSpec(
        PERM_READ,
        "查看 LLM 供应商与模型配置",
        group="运维",
        pages=(PAGE,),
    ),
    PermissionSpec(
        PERM_WRITE,
        "修改 LLM 供应商、网关、模型映射并发起连通测试（写权限可用已存密钥出网，只授予运维）",
        group="运维",
        pages=(PAGE,),
    ),
)

__all__ = ["PAGE", "PERM_READ", "PERM_WRITE", "SPECS"]
