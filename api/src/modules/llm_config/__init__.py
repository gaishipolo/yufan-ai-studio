"""LLM 配置模块：供应商与模型映射的运维页。

只管**配置**（写 `.env` 的 `LLM_<NAME>_*` 键）；调用协议在 `infra.llm`，
两者边界清楚：这里不认识 messages/usage，那边不认识供应商名。

顶级菜单 `page:llm` 与接口码 `llm_config:*` 分开声明，关页面时接口也跟着关。
"""

from api.module import ModuleSpec, PageSpec
from modules.llm_config.permissions import SPECS
from modules.llm_config.router import router

module = ModuleSpec(
    name="llm_config",
    router=router,
    tags=["LLM 配置"],
    description="LLM 供应商、模型映射与连通验收",
    permissions=SPECS,
    pages=(PageSpec(code="page:llm", name="LLM 配置", route="/llm"),),
)
