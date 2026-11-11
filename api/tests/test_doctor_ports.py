"""`app doctor` 必须把 fail-closed 注入口的缺失报成 FAIL。

这三个注入口的共同点：没注册时服务照常启动、接口照常 200，只有功能静默少一块。
references 那一支早就有对账，ingest 与 generation 曾经是「只在启动日志打一条
INFO」——草稿箱一直是空的、下游发起不了生成，而自检一片绿。
"""

from __future__ import annotations

import asyncio

from api.diagnostics import DoctorReport, _check_ports, run_doctor


def _levels(report: DoctorReport) -> dict[str, str]:
    return {check.name: check.level for check in report.checks}


def test_ports_check_covers_all_three_fail_closed_entries() -> None:
    report = DoctorReport()
    _check_ports(report)

    names = set(_levels(report))
    assert {"ports.references", "ports.ingest", "ports.generation"} <= names
    assert _levels(report)["ports.ingest"] == "PASS"
    assert _levels(report)["ports.generation"] == "PASS"


def test_missing_ingest_is_a_failure_not_a_silent_pass(monkeypatch) -> None:
    """入库端没注册 = 生成成功但写不进草稿箱。这条必须是 FAIL，不能只是 INFO。"""
    monkeypatch.setattr(
        "core.ports.library_ingest.has_library_ingest", lambda: False
    )
    report = DoctorReport()
    _check_ports(report)

    check = {item.name: item for item in report.checks}["ports.ingest"]
    assert check.level == "FAIL"
    assert "草稿箱" in check.message


def test_missing_generation_port_is_a_failure_not_a_silent_pass(monkeypatch) -> None:
    monkeypatch.setattr(
        "core.ports.model_generation.has_model_generation", lambda: False
    )
    report = DoctorReport()
    _check_ports(report)

    check = {item.name: item for item in report.checks}["ports.generation"]
    assert check.level == "FAIL"
    assert "生成" in check.message


def test_real_assembly_passes_the_port_checks() -> None:
    """正常装配下必须是绿的：否则这条自检会变成「永远红」的噪音。"""
    report = asyncio.run(run_doctor(sample=0))
    assert not [check for check in report.failures if check.name.startswith("ports.")]
