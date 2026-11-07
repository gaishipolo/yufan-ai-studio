"""运行指标：进程内注册表 + Prometheus 文本格式暴露。

## 为什么不引 `prometheus_client`

用到的只有三种类型（counter / gauge / histogram），文本格式是**稳定的公开规范**
（`text/plain; version=0.0.4`），一百多行就能正确实现。少一个运行时依赖意味着
更小的镜像、更少的升级风险；而「标签基数上限」这类本项目特有的纪律，自己写才
能直接焊进 API，而不是绕过库的默认行为去打补丁。

## 三条纪律（都是踩过之后写下来的）

1. **标签只能是低基数字段**：`method` / `route` / `status` / `outcome` / `name`。
   把原始 URL、用户 id、job id 当标签会让时间序列数量随流量线性增长——在应用侧
   只表现为「内存慢涨」，在 Prometheus 侧表现为「查询超时 / 抓取失败」，两边都
   很难定位。`max_series` 是兜底，不是许可证：撞上限会丢样本并打一条警告。
2. **每个指标只在导入期声明一次**。同名同标签的重复声明返回既有实例，因此
   `--reload` 与测试里反复 import 都不会抛「重复注册」。
3. **单进程语义**。多 worker 部署时每个进程各暴露一份，聚合交给 Prometheus
   （`sum by (le)` / `sum by (instance)`）。本模块不做跨进程聚合，也不落盘。

## 它不是什么

不是追踪（trace）也不是日志：指标只回答「多少 / 多快 / 有没有堆积」，
「这一次具体错在哪」仍然靠 `X-Request-Id` + 结构化日志（见 `core/logging.py`）。
"""

from __future__ import annotations

import math
import re
import threading
from collections.abc import Mapping, Sequence
from typing import Any

from core.logging import get_logger

logger = get_logger(__name__)

_NAME_RE = re.compile(r"^[A-Za-z_:][A-Za-z0-9_:]*$")
_LABEL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# 单个指标允许的时间序列上限。超过后丢弃**新**标签组合并告警：
# 保留既有序列比「全部重置」可诊断得多（曲线不会断）。
DEFAULT_MAX_SERIES = 500

# HTTP 延迟直方图分桶（秒）。覆盖「缓存命中 1ms」到「慢查询 10s」：
# 分桶太粗看不出 P99，太细则序列数 × 桶数爆炸。这组是行业常用的幂次序列。
HTTP_BUCKETS: tuple[float, ...] = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0,
)

# 任务执行时长（秒）：生图轮询秒级、视频任务分钟级，跨度比 HTTP 大得多
JOB_BUCKETS: tuple[float, ...] = (0.1, 0.5, 1.0, 5.0, 15.0, 60.0, 300.0, 900.0)


def _fmt(value: float) -> str:
    """Prometheus 文本格式的样本值。"""
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "+Inf" if value > 0 else "-Inf"
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return f"{value:.6g}"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


class _Metric:
    """所有指标类型的公共部分：标签校验、序列存储、基数上限。"""

    kind = "untyped"

    def __init__(
        self,
        full_name: str,
        help_text: str,
        label_names: Sequence[str],
        *,
        max_series: int = DEFAULT_MAX_SERIES,
    ) -> None:
        if not _NAME_RE.match(full_name):
            raise ValueError(f"指标名不合法：{full_name!r}")
        for label in label_names:
            if not _LABEL_RE.match(label):
                raise ValueError(f"标签名不合法：{label!r}")
        if len(set(label_names)) != len(label_names):
            raise ValueError(f"标签名重复：{tuple(label_names)!r}")
        self.name = full_name
        self.help = help_text
        self.label_names = tuple(label_names)
        self.max_series = max_series
        self._lock = threading.Lock()
        self._dropped = 0
        # 具体形状由子类决定（Counter/Gauge 存 float，Histogram 存计数列表），
        # 放在基类只为让「基数上限」这套逻辑与子类无关。
        self._samples: dict[tuple[str, ...], Any] = {}

    # -------------------------------------------------------------- 内部
    def _key(self, labels: Mapping[str, str]) -> tuple[str, ...]:
        given = set(labels)
        expected = set(self.label_names)
        if given != expected:
            raise ValueError(
                f"{self.name} 的标签应为 {sorted(expected)}，实际 {sorted(given)}"
            )
        return tuple(str(labels[name]) for name in self.label_names)

    def _admit(self, key: tuple[str, ...]) -> bool:
        """调用方已持锁：判断是否可以接纳一条新序列。"""
        if key in self._samples or len(self._samples) < self.max_series:
            return True
        self._dropped += 1
        if self._dropped in (1, 100, 10_000):
            logger.warning(
                "指标 %s 的标签组合超过上限 %d，已丢弃 %d 个新序列（检查标签是否带了高基数字段）",
                self.name,
                self.max_series,
                self._dropped,
            )
        return False

    def _header(self) -> list[str]:
        return [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} {self.kind}"]

    def _lines(self) -> list[str]:
        raise NotImplementedError

    # -------------------------------------------------------------- 公共
    def render(self) -> list[str]:
        with self._lock:
            return self._header() + self._lines()

    def reset(self) -> None:
        """仅测试使用：清空样本，保留声明（类型与标签契约不变）。"""
        with self._lock:
            self._samples.clear()
            self._dropped = 0

    def series_count(self) -> int:
        with self._lock:
            return len(self._samples)

    def _label_str(self, key: tuple[str, ...]) -> str:
        if not self.label_names:
            return ""
        pairs = ",".join(
            f'{name}="{_escape(value)}"' for name, value in zip(self.label_names, key, strict=True)
        )
        return f"{{{pairs}}}"


class Counter(_Metric):
    """只增不减。重启归零是预期行为（计数器不是持久状态）。"""

    kind = "counter"

    def inc(self, value: float = 1.0, /, **labels: str) -> None:
        key = self._key(labels)
        with self._lock:
            if not self._admit(key):
                return
            self._samples[key] = float(self._samples.get(key, 0.0)) + value

    def value(self, **labels: str) -> float:
        with self._lock:
            return float(self._samples.get(self._key(labels), 0.0))

    def _lines(self) -> list[str]:
        return [
            f"{self.name}{self._label_str(key)} {_fmt(float(value))}"
            for key, value in sorted(self._samples.items())
        ]


class Gauge(_Metric):
    """可增可减的瞬时值。抓取时才更新的应写成「采集器」，见 `api/instrumentation.py`。"""

    kind = "gauge"

    def set(self, value: float, /, **labels: str) -> None:
        key = self._key(labels)
        with self._lock:
            if not self._admit(key):
                return
            self._samples[key] = float(value)

    def inc(self, value: float = 1.0, /, **labels: str) -> None:
        key = self._key(labels)
        with self._lock:
            if not self._admit(key):
                return
            self._samples[key] = float(self._samples.get(key, 0.0)) + value

    def dec(self, value: float = 1.0, /, **labels: str) -> None:
        self.inc(-value, **labels)

    def value(self, **labels: str) -> float:
        with self._lock:
            return float(self._samples.get(self._key(labels), 0.0))

    def _lines(self) -> list[str]:
        return [
            f"{self.name}{self._label_str(key)} {_fmt(float(value))}"
            for key, value in sorted(self._samples.items())
        ]


class Histogram(_Metric):
    """累积直方图。

    `observe()` 用 `+Inf` 兜住所有观测值，因此 `_count` 恒等于观测次数；
    分桶值按 Prometheus 语义**累积**渲染（`le` 小于等于）。
    """

    kind = "histogram"

    def __init__(
        self,
        full_name: str,
        help_text: str,
        label_names: Sequence[str] = (),
        *,
        buckets: Sequence[float] | None = None,
        max_series: int = DEFAULT_MAX_SERIES,
    ) -> None:
        super().__init__(full_name, help_text, label_names, max_series=max_series)
        edges = tuple(sorted(buckets or HTTP_BUCKETS))
        if not edges or any(edge <= 0 for edge in edges):
            raise ValueError("直方图分桶必须为正数且非空")
        self._edges = edges

    def observe(self, value: float, /, **labels: str) -> None:
        key = self._key(labels)
        with self._lock:
            if not self._admit(key):
                return
            data = self._samples.get(key)
            if data is None:
                data = [0.0] * (len(self._edges) + 1) + [0.0, 0.0]
                # 末尾两位：sum、count
                self._samples[key] = data
            for index, edge in enumerate(self._edges):
                if value <= edge:
                    data[index] += 1
            data[len(self._edges)] += 1  # +Inf
            data[-2] += float(value)
            data[-1] += 1

    def count(self, **labels: str) -> float:
        with self._lock:
            data = self._samples.get(self._key(labels))
            return float(data[-1]) if data else 0.0

    def _lines(self) -> list[str]:
        lines: list[str] = []
        for key, data in sorted(self._samples.items()):
            labels = self._label_str(key)
            prefix = f"{self.name}_bucket"
            for index, edge in enumerate(self._edges):
                le = {"le": _fmt(edge)}
                lines.append(f"{prefix}{_merge(labels, le)} {_fmt(data[index])}")
            lines.append(f"{prefix}{_merge(labels, {'le': '+Inf'})} {_fmt(data[len(self._edges)])}")
            lines.append(f"{self.name}_sum{labels} {_fmt(data[-2])}")
            lines.append(f"{self.name}_count{labels} {_fmt(data[-1])}")
        return lines


def _merge(labels: str, extra: Mapping[str, str]) -> str:
    """把 `{a="b"}` 与额外标签合成一个标签集，保证 `le` 排最后（可读性）。"""
    body = labels[1:-1] if labels else ""
    extra_str = ",".join(f'{name}="{_escape(value)}"' for name, value in extra.items())
    joined = ",".join(part for part in (body, extra_str) if part)
    return f"{{{joined}}}"


class Registry:
    """指标注册表：同名同标签返回同一实例。"""

    def __init__(self) -> None:
        self._metrics: dict[str, _Metric] = {}
        self._lock = threading.Lock()

    def _get(
        self,
        name: str,
        cls: type[_Metric],
        help_text: str,
        labels: Sequence[str],
        *,
        buckets: Sequence[float] | None,
        max_series: int,
    ) -> _Metric:
        with self._lock:
            existing = self._metrics.get(name)
            if existing is not None:
                if not isinstance(existing, cls) or existing.label_names != tuple(labels):
                    raise TypeError(
                        f"指标 {name} 已以 {type(existing).__name__}"
                        f"{existing.label_names} 注册，不能改成 {cls.__name__}{tuple(labels)}"
                    )
                return existing
            metric: _Metric
            if cls is Histogram:
                metric = Histogram(
                    name, help_text, labels, buckets=buckets, max_series=max_series
                )
            else:
                metric = cls(name, help_text, labels, max_series=max_series)
            self._metrics[name] = metric
            return metric

    def counter(
        self,
        name: str,
        help_text: str,
        labels: Sequence[str] = (),
        *,
        max_series: int = DEFAULT_MAX_SERIES,
    ) -> Counter:
        metric = self._get(
            name, Counter, help_text, labels, buckets=None, max_series=max_series
        )
        assert isinstance(metric, Counter)
        return metric

    def gauge(
        self,
        name: str,
        help_text: str,
        labels: Sequence[str] = (),
        *,
        max_series: int = DEFAULT_MAX_SERIES,
    ) -> Gauge:
        metric = self._get(name, Gauge, help_text, labels, buckets=None, max_series=max_series)
        assert isinstance(metric, Gauge)
        return metric

    def histogram(
        self,
        name: str,
        help_text: str,
        labels: Sequence[str] = (),
        *,
        buckets: Sequence[float] | None = None,
        max_series: int = DEFAULT_MAX_SERIES,
    ) -> Histogram:
        metric = self._get(
            name, Histogram, help_text, labels, buckets=buckets, max_series=max_series
        )
        assert isinstance(metric, Histogram)
        return metric

    def render(self) -> str:
        with self._lock:
            metrics = [self._metrics[name] for name in sorted(self._metrics)]
        lines: list[str] = []
        for metric in metrics:
            lines.extend(metric.render())
        return "\n".join(lines) + "\n"

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._metrics)

    def reset(self) -> None:
        """仅测试使用：清空全部样本（声明保留）。"""
        with self._lock:
            metrics = list(self._metrics.values())
        for metric in metrics:
            metric.reset()

    def clear(self) -> None:
        """仅测试使用：连声明一起清空，便于验证「注册期声明」逻辑。"""
        with self._lock:
            self._metrics.clear()


#: 进程级注册表。业务模块要加指标就在这里声明（导入期一次）。
METRICS = Registry()

__all__ = [
    "HTTP_BUCKETS",
    "JOB_BUCKETS",
    "METRICS",
    "Counter",
    "Gauge",
    "Histogram",
    "Registry",
]
