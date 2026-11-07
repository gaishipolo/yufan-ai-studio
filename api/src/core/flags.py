"""功能开关：一个名字 → 一个布尔值，部署时决定。

## 它解决什么

「新功能出问题，先关掉」如果只能靠回滚部署，MTTR 就等于一次发版的时间。
开关把「关闭」变成改一个环境变量 + 重启（或者由配置中心推送，未来接）。

## 它不做什么

- **不做灰度**：没有按用户 / 按比例的分流。需要灰度就该在网关或实验平台做，
  在业务代码里按 user_id 取模会污染领域逻辑，也没法运营。
- **不做动态热更新**：值在进程启动时读入。改完要重启——这是刻意的，开关状态
  如果能在运行期变化，「这个请求到底走的是哪条分支」就没有日志之外的证据。
- **不替代配置**：`METRICS_ENABLED`、`WORKER_ENABLED` 这类**结构性**开关仍在
  `Settings` 里，因为它们决定装配，不只是决定走哪个分支。

## 用法

```python
from core.flags import is_on

if is_on("upload.multipart"):
    ...
```

约定：名字用 `领域.动作`，与权限码、事件名同一套命名习惯，便于 grep。
**开关只能有一个出口**：`is_on()`。不要在模块里直接读 `settings.feature_flags`，
否则「系统里有哪些开关、谁在用」就无法用一条命令回答（`app doctor` 会打印全部）。
"""

from __future__ import annotations

from core.config import get_settings


def is_on(name: str, *, default: bool = False) -> bool:
    """开关是否打开。未声明时返回 `default`。

    `default` 只在「这个开关还没进配置」时有意义：新代码带着 `default=True` 上线，
    老部署不会因为缺一行配置就把功能关掉；要关掉必须显式写 `"name": false`。
    """
    return bool(get_settings().feature_flags.get(name, default))


def enabled() -> dict[str, bool]:
    """全部已声明的开关（含显式关闭的），供 doctor 与排查使用。"""
    return dict(get_settings().feature_flags)


def declared_names() -> list[str]:
    return sorted(enabled())


__all__ = ["declared_names", "enabled", "is_on"]
