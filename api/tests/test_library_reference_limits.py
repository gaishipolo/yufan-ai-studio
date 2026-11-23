"""资产引用检查的「超上限」行为：宁可报错，不许静默截断。

## 为什么这条很重要

`library/references.py` 等三个 checker 的返回值**直接决定「这条资产能不能
彻底删除」**。丢掉一部分待查 id = 少查一批引用 = 「查不到 = 没人引用」——
而这三个文件的开头整段都在论证这个等式不成立：

> `ask_library_references()` 在来源不齐时抛 503，调用方（`purge`）必须拒绝
> 删除。这里如果退化成「查不到 = 没人引用」，一次装配故障就会让「不可逆
> 删除」静默放行，事后只能靠备份找回。

所以「为了省事把多出来的 id 切掉」是最危险的一种优化：它不会报错、不会
留下日志，只会让不可逆删除在**漏查**的前提下放行。

## 现状核实（2026-11-23）

三个 checker 都曾用 `[:_MAX_IDS]` 静默切片（`library` 200 个、
`task` / `image` 各 50 个）。**当前调用链上截断不可达**：所有
`ask_library_references()` 调用点都只传一个 id（`library/service.py` 的详情
展示、`library/lifecycle.py` 的 `purge` 与 `purge_expired`），而彻底删除
（`purge`）**刻意不在批量动作里**（`library/bulk.py` 文件头：「不可逆操作一次
点错十条就没救了」）。

也就是说这条不是「现在正在丢数据」，而是「机制上留着丢数据的口子，且这个
口子一旦被批量 purge 打开就没人会看见」。本文件把它焊死。
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.exceptions import Unavailable

SRC = Path(__file__).resolve().parents[1] / "src"

#: 三个引用检查来源：模块路径 → 该来源的单次检查上限。
#: 上限的来源是性能护栏（一次最多发这么多条 LIKE 查询），不是功能语义。
CHECKERS: dict[str, tuple[str, int]] = {
    "modules/library/references.py": ("DownloadJobReferences", 200),
    "modules/task/library_references.py": ("TaskLibraryReferences", 50),
    "modules/image/library_references.py": ("ImageJobReferences", 50),
}


def _session():
    """一个够用的 session —— 超限判断发生在任何查询之前。"""
    from db.session import get_session_factory

    return get_session_factory()()


def _ids(count: int) -> list[str]:
    return [f"asset_{index:04d}" for index in range(count)]


# --------------------------------------------------------------------------- #
# 行为：超上限即报错
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("relative", sorted(CHECKERS))
def test_超上限时报错而不是静默截断(relative: str) -> None:
    """传超上限的 id 必须抛错。

    静默截断的后果是「漏查 = 没人引用」→ 不可逆删除放行，且没有任何痕迹。
    """
    dotted = relative.removesuffix(".py").replace("/", ".")
    module = __import__(dotted, fromlist=["*"])
    checker_name, limit = CHECKERS[relative]
    checker = getattr(module, checker_name)()

    async def run() -> None:
        async with _session() as session:
            await checker.references(session, _ids(limit + 1))

    with pytest.raises(Unavailable) as excinfo:
        asyncio.run(run())
    message = str(excinfo.value)
    # 消息要能让人知道「超了多少」和「上限是多少」
    assert str(limit) in message, message
    assert str(limit + 1) in message, message


@pytest.mark.parametrize("relative", sorted(CHECKERS))
def test_恰好等于上限时不报错(relative: str) -> None:
    """上限是**边界**，不是陷阱：正好等于上限必须正常查完。"""
    dotted = relative.removesuffix(".py").replace("/", ".")
    module = __import__(dotted, fromlist=["*"])
    checker_name, limit = CHECKERS[relative]
    checker = getattr(module, checker_name)()

    async def run() -> dict:
        async with _session() as session:
            return await checker.references(session, _ids(limit))

    assert asyncio.run(run()) == {}


# --------------------------------------------------------------------------- #
# 守卫：源码里不许再出现静默切片
# --------------------------------------------------------------------------- #
def test_引用检查里没有静默截断() -> None:
    """扫源码：三个 checker 里不许出现 `[:_MAX_IDS]` 这类静默切片。

    钉的是**形状**（切片赋值）而不是具体变量名 —— 将来有人把
    `_MAX_IDS` 改名或换成别的上限，这条依然会红。
    """
    offenders: list[str] = []
    for relative in CHECKERS:
        path = SRC / relative
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Subscript):
                continue
            if not isinstance(node.slice, ast.Slice):
                continue
            # `xxx[:LIMIT]` —— 上界是切片且不是 None，就是截断
            upper = node.slice.upper
            if upper is None:
                continue
            offenders.append(f"{relative}:{node.lineno}")
    assert not offenders, (
        "引用检查里出现了静默截断（超上限的 id 被丢掉 = 漏查 = 不可逆删除放行）-> "
        + "; ".join(offenders)
        + "；超上限应当报错（fail-closed）"
    )


def test_彻底删除不在批量动作里() -> None:
    """`purge`（彻底删除）不得出现在批量动作中。

    这条不是 M7 的原话，但它决定了 M7 的严重性：批量删除若包含不可逆的
    `purge`，「一次点错十条就没救了」，而且引用检查会收到一批 id —— 那正是
    `_MAX_IDS` 上限存在的场景。仓库的原始设计明确把它排除在外
    （`library/bulk.py` 文件头：「`purge`（彻底删除）不在批量动作里」）。
    """
    path = SRC / "modules" / "library" / "bulk.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    purge_calls: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "purge":
            purge_calls.append(f"bulk.py:{node.lineno}")

    assert not purge_calls, (
        "批量动作里出现了 purge（彻底删除）-> " + "; ".join(purge_calls)
        + "；不可逆操作应当单条走"
    )


def test_单条删除路径的引用检查仍然生效(client: TestClient, api: str) -> None:
    """这条闸门不能变成「为了不截断而把引用检查关掉」。

    钉住端到端行为：资产被视频任务引用时，彻底删除被拦（409），
    并说清是哪个任务在用。
    """
    from library_support import upload_image

    asset = upload_image(client, api, {}, name="guarded.png", color="seagreen")

    created = client.post(
        f"{api}/tasks",
        json={
            "prompt": "引用了这张图的任务",
            "title": "引用了这张图的任务",
            "model": "libtv-wan3-replacement",
            "referenceLibraryIds": [asset["id"]],
        },
    )
    assert created.status_code == 201, created.text
    task_id = created.json()["data"]["id"]

    try:
        trashed = client.delete(f"{api}/library/assets/{asset['id']}", headers={})
        assert trashed.status_code == 200, trashed.text

        blocked = client.delete(f"{api}/library/trash/{asset['id']}", headers={})
        assert blocked.status_code == 409, blocked.text
        assert any(task_id in item for item in blocked.json()["details"]["references"])
    finally:
        _delete_tasks([task_id])


def _delete_tasks(task_ids: list[str]) -> None:
    async def run() -> None:
        from sqlalchemy import delete

        from db.session import get_session_factory
        from modules.task.models import VideoTask

        async with get_session_factory()() as session:
            await session.execute(delete(VideoTask).where(VideoTask.id.in_(task_ids)))
            await session.commit()

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 精确性：子串粗筛必须被二次精确比对兜住
# ---------------------------------------------------------------------------


def _video_task_with_ids(asset_ids: list[str]) -> str:
    async def run() -> str:
        from db.session import get_session_factory
        from modules.task.models import VideoTask

        async with get_session_factory()() as session:
            task = VideoTask(
                title="粗筛兜底用例",
                prompt="粗筛兜底用例",
                model="libtv-wan3-replacement",
                author_id="system",
                reference_library_ids=asset_ids,
            )
            session.add(task)
            await session.commit()
            return task.id

    return asyncio.run(run())


def _image_job_with_refs(refs: list[dict[str, str]]) -> str:
    import json

    async def run() -> str:
        from db.session import get_session_factory
        from modules.image.models import ImageJob

        async with get_session_factory()() as session:
            job = ImageJob(
                name="粗筛兜底用例",
                prompt="粗筛兜底用例",
                owner_id="system",
                reference_json=json.dumps(refs),
            )
            session.add(job)
            await session.commit()
            return job.id

    return asyncio.run(run())


def _delete_image_jobs(job_ids: list[str]) -> None:
    async def run() -> None:
        from sqlalchemy import delete

        from db.session import get_session_factory
        from modules.image.models import ImageJob

        async with get_session_factory()() as session:
            await session.execute(delete(ImageJob).where(ImageJob.id.in_(job_ids)))
            await session.commit()

    asyncio.run(run())


def _check_video(asset_ids: list[str]) -> dict[str, list[str]]:
    from modules.task.library_references import TaskLibraryReferences

    async def run() -> dict[str, list[str]]:
        async with _session() as session:
            found = await TaskLibraryReferences().references(session, asset_ids)
            return {key: list(value) for key, value in found.items()}

    return asyncio.run(run())


def _check_image(asset_ids: list[str]) -> dict[str, list[str]]:
    from modules.image.library_references import ImageJobReferences

    async def run() -> dict[str, list[str]]:
        async with _session() as session:
            found = await ImageJobReferences().references(session, asset_ids)
            return {key: list(value) for key, value in found.items()}

    return asyncio.run(run())


def test_视频侧的粗筛不能直接当结论(client: TestClient) -> None:
    """子串粗筛必须被二次精确比对兜住。

    JSON 列没法按元素查（sqlite / PG 语法不同），所以两个 JSON checker
    只能先按子串粗筛，再在 Python 里精确比对。粗筛是「字符串里恰好出现了
    这个 id」——**比目标 id 更长的另一个 id 也会被捞出来**。

    如果哪天有人把二次精确比对删掉（看着像冗余：粗筛已经捞到了），粗筛
    就直接变成了结论：一条**根本不含**该资产的任务会把它报成引用来源 →
    那条资产被永久挡在「彻底删除」之外，而且看不出原因。

    所以这里钉的是**行为**：查一个只是被别的 id 包含的字符串，不该有
    引用来源；而真正在清单里的那个 id，必须仍然报得出来（否则这条用例
    可能因为「什么都没查到」而假绿）。
    """
    task_id = _video_task_with_ids(["prefix-asset-1-suffix"])
    try:
        found = _check_video(["asset-1", "prefix-asset-1-suffix"])
    finally:
        _delete_tasks([task_id])

    assert "asset-1" not in found, found
    assert "prefix-asset-1-suffix" in found, found


def test_生图侧的粗筛不能直接当结论(client: TestClient) -> None:
    """同上，生图那支的 JSON 里还混着快照字段，粗筛捞出来的行更多。"""
    job_id = _image_job_with_refs(
        [
            {
                "assetId": "prefix-asset-1-suffix",
                "storageKey": "library/x.png",
                "mimeType": "image/png",
            }
        ]
    )
    try:
        found = _check_image(["asset-1", "prefix-asset-1-suffix"])
    finally:
        _delete_image_jobs([job_id])

    assert "asset-1" not in found, found
    assert "prefix-asset-1-suffix" in found, found
