"""视频模板的存储行为与运维盘点。

落表（问题 A）之后，「写前快照 20 份 / 目录 mtime 当代次 / 文件损坏怎么报」
这一整套随着 `catalog.json` 一起消失了，替它们站岗的变成三件事：

1. **固定蓝本全库唯一**——由 `uq_video_templates_pinned` 这条部分唯一索引保证，
   不是应用层「先清后设」。这条必须测在数据库层：应用层顺序一写反就撞约束，
   而撞约束的报错读起来像「保存失败」，不像「蓝本抢重复了」。
2. **空盘点不等于合规**——`scripts/ensure_no_subtitle_prompt.py` 读到零条模板时
   必须报错退出。一个连错库的盘点最不该给的就是「全部提示词都合规」。
3. **封装进度目录仍按 `TEMPLATE_PACKAGER_STATE_DIR`**——与落表无关，顺手留着。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from video_template_support import put, record, remove, run

from db.session import get_session_factory
from modules.template_pack.models import VideoTemplate

API_ROOT = Path(__file__).resolve().parents[1]


def test_pinned_is_unique_at_the_database_level() -> None:
    """两条 `pinned=True` 必须被数据库拒绝，而不是「后写的赢」。

    这是「抢蓝本」的兜底：`create` / `pin` 都会先清别人的标记，但只要有一条
    路径漏了（脚本、导入、将来新增的接口），唯一索引就是最后一道闸。
    """

    async def attempt() -> None:
        async with get_session_factory()() as session:
            # 别的用例可能留下过蓝本，先把位子腾出来再验这一条
            await session.execute(
                update(VideoTemplate)
                .where(VideoTemplate.pinned.is_(True))
                .values(pinned=False)
            )
            session.add(
                VideoTemplate(
                    id="pin-guard-a",
                    template_key="pin-guard-a",
                    name="A",
                    prompt="对 {{Mixed 1}} 做替换",
                    pinned=True,
                )
            )
            await session.commit()
        async with get_session_factory()() as session:
            session.add(
                VideoTemplate(
                    id="pin-guard-b",
                    template_key="pin-guard-b",
                    name="B",
                    prompt="对 {{Mixed 1}} 做替换",
                    pinned=True,
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

    try:
        run(attempt())
    finally:
        remove("pin-guard-a")


def test_second_pin_clears_the_first() -> None:
    """`pin()` 换蓝本时，前一条必须落回 `pinned=False`。"""
    from modules.template_pack import video_template_service as service

    put(**record(id="pin-one", templateKey="pin-one"))
    put(**record(id="pin-two", templateKey="pin-two", pinned=False))

    async def flip():
        async with get_session_factory()() as session:
            return await service.pin("pin-two", session)

    flipped = run(flip())
    assert flipped.id == "pin-two"
    assert flipped.pinned is True

    async def read_one():
        async with get_session_factory()() as session:
            return await session.get(VideoTemplate, "pin-one")

    first = run(read_one())
    assert first is not None
    assert first.pinned is False


def _load_subtitle_script():
    spec = importlib.util.spec_from_file_location(
        "ensure_no_subtitle_prompt",
        API_ROOT / "scripts" / "ensure_no_subtitle_prompt.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_subtitle_script_refuses_to_report_compliance_when_table_is_empty(
    monkeypatch, capsys
) -> None:
    """一条模板都没读到时必须报错退出。

    空表的成因通常不是「真的没有模板」，而是连错了库或还没跑
    `app import-video-templates`。这时打印「全部提示词都带『全片无字幕』」
    并返回 0，是这个脚本能给出的最危险的结论。
    """
    script = _load_subtitle_script()

    async def empty() -> list[dict]:
        return []

    monkeypatch.setattr(script, "_load_templates", empty)
    assert script.main([]) == 2
    captured = capsys.readouterr()
    assert "全部提示词都带" not in captured.out
    assert "一条记录都没有" in captured.err


def test_subtitle_script_does_not_claim_compliance_when_the_database_fails(
    monkeypatch, capsys
) -> None:
    """读库异常同样不能退化成「全部合规」。"""
    script = _load_subtitle_script()

    async def boom() -> list[dict]:
        raise RuntimeError("db down")

    monkeypatch.setattr(script, "_load_templates", boom)
    assert script.main([]) == 2
    captured = capsys.readouterr()
    assert "读不到视频模板表" in captured.err


def test_subtitle_script_counts_only_templates_missing_the_marker(
    monkeypatch, capsys
) -> None:
    """盘点口径：缺标记的进 `missing`，已带的跳过；只读模式不写库。"""
    script = _load_subtitle_script()

    async def rows() -> list[dict]:
        return [
            {"id": "a", "name": "已带", "prompt": "全片无字幕。\n正文", "packId": None},
            {"id": "b", "name": "没带", "prompt": "正文", "packId": None},
        ]

    monkeypatch.setattr(script, "_load_templates", rows)
    assert script.main([]) == 0
    out = capsys.readouterr().out
    assert '"missing"' in out
    assert '"id": "b"' in out
    assert "只读盘点" in out
