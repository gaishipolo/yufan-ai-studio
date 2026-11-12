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


def test_dropped_series_are_visible_in_the_scrape_output() -> None:
    """撞上限必须能被抓到，不能只写进本机日志。

    `_admit` 早就记了 `_dropped` 并打 WARNING，但 `/metrics` 输出里没有任何痕迹：
    面板上只表现为「某个接口没有数据」，值班的人分不清是没流量、还是被丢了。
    Prometheus 抓的是文本，所以丢弃量本身也得是能抓的一条序列。
    """
    registry = Registry()
    counter = registry.counter("demo_total", "演示", ("kind",), max_series=1)
    counter.inc(kind="a")
    counter.inc(kind="b")  # 丢弃

    text = registry.render()
    assert "app_metrics_series_dropped_total" in text
    assert 'app_metrics_series_dropped_total{metric="demo_total"} 1' in text


def test_no_drop_metric_when_nothing_was_dropped() -> None:
    """没丢就别往输出里塞噪声：零值序列会让人以为上限一直在生效。"""
    registry = Registry()
    registry.counter("demo_total", "演示", ("kind",)).inc(kind="a")
    assert "app_metrics_series_dropped_total" not in registry.render()


def test_http_request_counter_can_hold_every_route_with_its_status_codes() -> None:
    """`http_requests_total` 的基数 = 路由数 × 状态码数，不能沿用 500 的默认值。

    140 个操作，只要平均每个踩到 4 个状态码（200 + 4xx/5xx 的几个），就到 560 ——
    超过默认 500 以后，**后注册的接口会整条丢样本**，且丢的是随机的：
    取决于哪个接口先被访问。这条用例把「按当前路由规模算够不够」钉住。
    """
    import json
    from pathlib import Path

    from api.instrumentation import REQUESTS

    openapi = json.loads(
        (Path(__file__).resolve().parents[1] / "docs" / "openapi.json").read_text("utf-8")
    )
    operations = sum(
        1
        for item in openapi["paths"].values()
        for method in item
        if method in ("get", "post", "put", "patch", "delete")
    )
    # 每个操作预留 8 个状态码：200/201/202 + 400/401/403/404/409/422/500 里挑 8 个
    assert REQUESTS.max_series >= operations * 8, (
        f"http_requests_total 上限 {REQUESTS.max_series} 装不下 {operations} 个操作 × 8 个状态码，"
        "超出的接口会静默丢样本"
    )


def test_redeclaring_metric_takes_the_stricter_max_series() -> None:
    """重复声明时 `max_series` **取更严的那个**，不取「先声明的」。

    丢掉后来的 `max_series` 意味着上限写在哪个模块里取决于 import 顺序：
    模块 A 声明 500、模块 B 声明 2000，A 后 import 时上限就变成 2000——
    B 写的上限对 A 生效，反过来也一样。取 min 让「谁声明都一样」。
    """
    registry = Registry()
    registry.counter("demo_total", "演示计数", ("kind",), max_series=2000)
    registry.counter("demo_total", "演示计数", ("kind",), max_series=500)

    metric = registry.counter("demo_total", "演示计数", ("kind",))
    assert metric.max_series == 500


def test_redeclaring_metric_keeps_the_stricter_limit_effective() -> None:
    """收紧后的上限必须真的生效：新序列该丢就丢。"""
    registry = Registry()
    registry.counter("demo_total", "演示计数", ("kind",), max_series=10)
    registry.counter("demo_total", "演示计数", ("kind",), max_series=2)

    counter = registry.counter("demo_total", "演示计数", ("kind",))
    counter.inc(kind="a")
    counter.inc(kind="b")
    counter.inc(kind="c")  # 超限，丢弃

    assert counter.series_count() == 2
    assert counter.dropped_count() == 1
    assert 'app_metrics_series_dropped_total{metric="demo_total"} 1' in registry.render()
