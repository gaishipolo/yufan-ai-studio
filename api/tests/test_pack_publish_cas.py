"""发布版号的 CAS：版本文件谁先创建谁算数。

回归的是「远端驱动没有跨实例文件锁」那一段：`catalog_lease` 退化成本机锁后，
两个实例会同时算出同一个版号。旧实现直接 `write()` 覆盖，结果是**两个内容不同
却同版本号**的包——已发布的版本按定义不可变，覆盖等于篡改（T20）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.media import MediaPack
from infra.archive.local import get_archive_settings
from modules.template.pack_service import PackService


@pytest.fixture
def packs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PackService:
    """把归档根指到临时目录：这里的每个用例都会写 `catalog/` 下的文件。"""
    monkeypatch.setattr(get_archive_settings(), "root", str(tmp_path))
    return PackService()


def _draft(name: str = "第一版") -> MediaPack:
    return MediaPack(
        id="pack1",
        owner_id="S",
        kind="product",
        name=name,
        reference_ids=["ref1"],
        cover_reference_id="ref1",
        bindings=[{"role": "front"}],
        supported_scopes=["worn"],
    )


def test_create_is_exclusive(packs: PackService) -> None:
    """底层能力：第二次 `create` 返回 False，且**不改**已有内容。"""
    assert packs.storage.create("catalog/probe.json", {"v": 1}) is True
    assert packs.storage.create("catalog/probe.json", {"v": 2}) is False
    assert packs.storage.read("catalog/probe.json") == {"v": 1}


def test_claim_version_skips_the_taken_number(packs: PackService) -> None:
    """两个实例都从 1 开始抢：第二个必须顺延，而不是覆盖第一个。"""
    first = packs._claim_version("pack1", 1, _draft("实例 A 的内容"))
    second = packs._claim_version("pack1", 1, _draft("实例 B 的内容"))

    assert (first, second) == (1, 2)
    stored = packs.storage.read("catalog/pack_versions/pack1/1.json")
    assert isinstance(stored, dict)
    assert stored["name"] == "实例 A 的内容", "已发布版本被后来者覆盖了"


def test_head_never_moves_backwards(packs: PackService) -> None:
    """head 只是投影：晚到的较小版号不能把它拉回旧值。"""
    head_key = "catalog/packs/pack1.json"
    newer = _draft("新版本")
    newer.version = 4
    packs._write_head(head_key, newer)

    older = _draft("旧版本")
    older.version = 3
    packs._write_head(head_key, older)

    stored = packs.storage.read(head_key)
    assert isinstance(stored, dict)
    assert stored["version"] == 4
    assert stored["name"] == "新版本"


def test_claim_version_gives_up_after_repeated_conflicts(packs: PackService) -> None:
    """连着被抢时不能无限自增：让调用方刷新重来，而不是占掉一堆版号。"""
    from core.exceptions import Conflict

    draft = _draft()
    # 1..5 全部先占满，第 6 次（重试上限 5）应当放弃
    for version in range(1, 6):
        draft.version = version
        packs.storage.create(f"catalog/pack_versions/pack1/{version}.json", {})

    with pytest.raises(Conflict):
        packs._claim_version("pack1", 1, draft)


def test_create_never_leaves_a_truncated_file_when_the_writer_dies(
    packs: PackService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """写到一半崩掉：目标名必须**根本没出现**，而不是留一份读不出来的 JSON。

    `create()` 是发布版号与选主租约的仲裁者。旧实现用 `open("xb")` 直接写目标：
    `xb` 保证独占，但它把「占住这个名字」和「写完内容」绑在同一段代码里——
    崩在中间就留下一份截断的 JSON，`read()` 抛 `JSONDecodeError`，而 `create()`
    之后**永远**返回 False（文件已存在）。那个版号成了永久黑洞，只能人工去删。
    """
    from infra.archive.local import LocalArchiveStorage

    storage = LocalArchiveStorage()
    target = tmp_path / "catalog" / "v9.json"
    original = Path.write_bytes

    def half_then_die(self: Path, data: bytes) -> None:
        original(self, data[:4])  # 先落一半
        raise OSError("磁盘满了")

    monkeypatch.setattr(Path, "write_bytes", half_then_die)
    with pytest.raises(OSError):
        storage.create("catalog/v9.json", {"version": 9})
    monkeypatch.setattr(Path, "write_bytes", original)

    assert not target.exists(), "崩溃后目标名不该出现（否则这个版号永久不可用）"
    assert not list(target.parent.glob("*.tmp")), "临时文件也要清掉，不能留给下一轮"


def test_create_still_produces_complete_content(packs: PackService) -> None:
    """换实现不能把内容写坏：读回来必须是完整 JSON，且目录里不留临时文件。"""
    assert packs.storage.create("catalog/probe2.json", {"v": 1, "名字": "完整"}) is True
    assert packs.storage.read("catalog/probe2.json") == {"v": 1, "名字": "完整"}
