"""指标基元：文本格式、累积语义、基数上限。

这些用例故意只测「输出长什么样」和「语义对不对」，不测内部结构：
Prometheus 文本格式是对外契约，改实现时这组断言应该继续成立。
"""

from __future__ import annotations

import pytest

from core.metrics import Registry


def test_counter_and_gauge_render_in_prometheus_format() -> None:
    registry = Registry()
    counter = registry.counter("demo_total", "演示计数", ("kind",))
    counter.inc(kind="a")
    counter.inc(2, kind="a")
    gauge = registry.gauge("demo_depth", "演示瞬时值")
    gauge.set(3)
    gauge.inc()
    gauge.dec(2)

    text = registry.render()
    assert "# TYPE demo_total counter" in text
    assert 'demo_total{kind="a"} 3' in text
    assert "# TYPE demo_depth gauge" in text
    assert "demo_depth 2" in text
    # 每份 exposition 都以换行结尾，追加抓取内容时不会粘行
    assert text.endswith("\n")


def test_histogram_is_cumulative_and_capped_by_inf() -> None:
    registry = Registry()
    histogram = registry.histogram("demo_seconds", "演示耗时", buckets=(0.1, 1.0))
    histogram.observe(0.05)
    histogram.observe(0.5)
    histogram.observe(5.0)

    text = registry.render()
    assert 'demo_seconds_bucket{le="0.1"} 1' in text
    assert 'demo_seconds_bucket{le="1"} 2' in text
    # +Inf 兜住所有观测值：`_count` 必须等于观测次数，否则 P99 会算错
    assert 'demo_seconds_bucket{le="+Inf"} 3' in text
    assert "demo_seconds_count 3" in text
    assert "demo_seconds_sum 5.55" in text


def test_declaring_same_metric_twice_returns_same_instance() -> None:
    registry = Registry()
    first = registry.counter("demo_total", "演示", ("kind",))
    second = registry.counter("demo_total", "演示", ("kind",))
    assert first is second
    first.inc(kind="a")
    assert second.value(kind="a") == 1


def test_conflicting_declaration_is_rejected() -> None:
    registry = Registry()
    registry.counter("demo_total", "演示", ("kind",))
    with pytest.raises(TypeError):
        registry.counter("demo_total", "演示", ("other",))
    with pytest.raises(TypeError):
        registry.gauge("demo_total", "演示", ("kind",))


def test_unknown_or_missing_label_is_rejected() -> None:
    registry = Registry()
    counter = registry.counter("demo_total", "演示", ("kind",))
    with pytest.raises(ValueError):
        counter.inc(other="x")
    with pytest.raises(ValueError):
        counter.inc()


def test_label_values_are_escaped() -> None:
    registry = Registry()
    counter = registry.counter("demo_total", "演示", ("route",))
    counter.inc(route='a"b\\c\nd')
    text = registry.render()
    assert 'route="a\\"b\\\\c\\nd"' in text
    # 转义后的样本必须仍是单行，否则整个抓取会被截断
    assert len([line for line in text.splitlines() if line.startswith("demo_total{")]) == 1


def test_series_cap_drops_new_labels_but_keeps_existing() -> None:
    registry = Registry()
    counter = registry.counter("demo_total", "演示", ("kind",), max_series=2)
    counter.inc(kind="a")
    counter.inc(kind="b")
    counter.inc(kind="c")  # 丢弃
    counter.inc(kind="a")  # 既有序列仍可累加
    assert counter.series_count() == 2
    assert counter.value(kind="a") == 2
    assert counter.value(kind="c") == 0


def test_invalid_names_are_rejected_at_declaration() -> None:
    registry = Registry()
    with pytest.raises(ValueError):
        registry.counter("demo total", "空格不合法")
    with pytest.raises(ValueError):
        registry.counter("demo_total", "演示", ("1kind",))
