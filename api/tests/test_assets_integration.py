"""跨账号：公共原片池、专员发布的封装、调用者的输入快照。

模板库的设定是「借用别人准备好的东西，但只产出自己的记录」，所以跨账号是主线
而不是边界情况。这一组用例把三条门禁钉住：

- 原片只有 `shared=True` 才能被别人拿去用，撤回立刻关门；
- 封装发布后对所有账号可见，停用立刻关门；
- 一旦开始准备，输入就复制进**调用者**的工作区，此后原作者删素材、撤回分享都
  不影响这条记录——否则「历史可复核」只是时间问题。

另外两条是重建时新加的：`stateVersion` 乐观锁（两位同事同时改同一条素材）与
「移入回收站即撤回分享」（否则删掉的原片还挂在公共池里）。
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests_template_helpers import (
    ADMIN,
    API,
    add_pack,
    drop_pack,
    drop_run,
    login,
    png_bytes,
    upload,
    video_bytes,
)

from infra.archive.local import get_archive_settings

EDITOR = "editor"
EDITOR_PASSWORD = "editor@12345"


@pytest.fixture(autouse=True)
def archive_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """每个用例一个归档根：跨账号用例会改分享状态，不能与前一个共用目录。"""
    monkeypatch.setattr(get_archive_settings(), "root", str(tmp_path))
    return tmp_path


def _upload_as(
    client: TestClient, headers: dict[str, str], filename: str, category: str, content: bytes
) -> dict[str, Any]:
    response = client.post(
        f"{API}/assets/upload",
        params={"filename": filename, "category": category},
        content=content,
        headers={"Content-Type": "application/octet-stream", **headers},
    )
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert isinstance(data, dict)
    return data


def _change(
    client: TestClient,
    asset: dict[str, Any],
    headers: dict[str, str],
    *,
    field: str,
    value: object,
    version: int | None = None,
):
    """三个 PUT 的公共写法：`field` 是 `name` / `shared` / `deleted`。

    分享的路径是 `/sharing`（动作名），不是 `/shared`（字段名）：契约里两者
    本来就不同名，写错了会得到 404，看起来像「素材没有了」。
    """
    body = {
        "expectedVersion": asset["stateVersion"] if version is None else version,
        field: value,
    }
    segment = {"shared": "sharing"}.get(field, field)
    return client.put(f"{API}/assets/{asset['id']}/{segment}", json=body, headers=headers)


def _public_ids(client: TestClient, headers: dict[str, str]) -> set[str]:
    response = client.get(f"{API}/assets", params={"scope": "public"}, headers=headers)
    assert response.status_code == 200, response.text
    return {item["id"] for item in response.json()["data"]["items"]}


def _payload(kind: str, source_id: str, pack_id: str, version: int) -> dict[str, Any]:
    return {
        "requestId": str(uuid.uuid4()),
        "kind": kind,
        "sourceId": source_id,
        "packId": pack_id,
        "packVersion": version,
        "replacementScope": "人物上身穿着" if kind == "product" else "",
    }


def _source(client: TestClient, headers: dict[str, str] | None = None) -> dict[str, Any]:
    """一段带音轨的 2 秒原片：`prepare` 会读时长与音轨，手写记录测不出来。"""
    if headers is None:
        return upload(client, "source.mp4", "source", video_bytes(2, audio=True))
    return _upload_as(
        client, headers, "source.mp4", "source", video_bytes(2, audio=True)
    )


# --------------------------------------------------------------------------- #
# 公共原片池
# --------------------------------------------------------------------------- #
def test_sharing_opens_the_public_pool_and_revoking_closes_it(client: TestClient) -> None:
    """分享是「借出去」，撤回是「收回来」，两件事都必须立刻在别人的列表里生效。

    中间那一步还验证了详情接口的口径与列表一致：列表看不到、详情却 200，等于
    把已撤回的原片还能被别人按 id 摸到——撤回就没意义了。
    """
    owner = login(client, "admin", "admin@12345")
    other = login(client, EDITOR, EDITOR_PASSWORD)
    source = _source(client, owner)

    assert source["id"] not in _public_ids(client, other), "默认私有，不该出现在别人的公共池"

    shared = _change(client, source, owner, field="shared", value=True)
    assert shared.status_code == 200, shared.text
    assert source["id"] in _public_ids(client, other)
    assert client.get(f"{API}/assets/{source['id']}", headers=other).status_code == 200

    revoked = _change(client, shared.json()["data"], owner, field="shared", value=False)
    assert revoked.status_code == 200, revoked.text
    assert source["id"] not in _public_ids(client, other)
    assert client.get(f"{API}/assets/{source['id']}", headers=other).status_code == 404


def test_only_the_owner_can_change_sharing(client: TestClient) -> None:
    """不能改别人的素材，而且回 404：回 403 等于确认「这条存在，只是你没权限」。"""
    owner = login(client, "admin", "admin@12345")
    other = login(client, EDITOR, EDITOR_PASSWORD)
    source = _source(client, owner)

    denied = _change(client, source, other, field="shared", value=True)
    assert denied.status_code == 404
    assert source["id"] not in _public_ids(client, owner), "被拒绝的请求不能改到状态"


def test_only_undeleted_sources_can_be_shared(client: TestClient) -> None:
    """参考图不能分享（公共池只放原片），已删除的也不行——它马上就不在了。"""
    owner = login(client, "admin", "admin@12345")
    picture = upload(client, "character.png", "character", png_bytes())
    source = _source(client, owner)

    wrong_category = _change(client, picture, owner, field="shared", value=True)
    assert wrong_category.status_code == 400
    assert "原片" in wrong_category.text

    trashed = _change(client, source, owner, field="deleted", value=True)
    assert trashed.status_code == 200, trashed.text
    deleted = trashed.json()["data"]
    assert deleted["shared"] is False

    after_delete = _change(client, deleted, owner, field="shared", value=True)
    assert after_delete.status_code in (400, 404), "回收站里的原片不能再借出去"


def test_recycle_withdraws_sharing_and_restore_does_not_republish(client: TestClient) -> None:
    """删除要「删干净」：恢复一条已删原片不该顺带把它重新借给全公司。

    如果恢复时把 `shared` 还原成删除前的值，那「删除」就只是一次隐藏，
    用户以为已经收回的素材会在回收站恢复后重新出现在公共池里。
    """
    owner = login(client, "admin", "admin@12345")
    other = login(client, EDITOR, EDITOR_PASSWORD)
    shared = _change(
        client, _source(client, owner), owner, field="shared", value=True
    ).json()["data"]
    assert shared["id"] in _public_ids(client, other)

    trashed = _change(client, shared, owner, field="deleted", value=True).json()["data"]
    assert trashed["shared"] is False
    assert trashed["id"] not in _public_ids(client, other)

    restored = _change(client, trashed, owner, field="deleted", value=False).json()["data"]
    assert restored["shared"] is False
    assert restored["id"] not in _public_ids(client, other)


# --------------------------------------------------------------------------- #
# 写路径的守卫：一次只改一个字段、过期版本不能覆盖
# --------------------------------------------------------------------------- #
def test_change_action_accepts_only_its_own_field(client: TestClient) -> None:
    """三个 PUT 共用一个写路径，所以必须由接口声明「这一次允许改什么」。

    少了这道门，「改名」接口就成了后门：带一个 `shared: true` 就能绕过
    `asset:share` 权限把原片借出去。
    """
    owner = login(client, "admin", "admin@12345")
    source = _source(client, owner)

    smuggled = client.put(
        f"{API}/assets/{source['id']}/name",
        json={"expectedVersion": source["stateVersion"], "name": "改名", "shared": True},
        headers=owner,
    )
    assert smuggled.status_code == 400
    assert "仅提交" in smuggled.text


def test_stale_version_is_rejected_but_same_state_retry_passes(client: TestClient) -> None:
    """乐观锁的两种「版本对不上」要分开处理。

    - 内容真的变了 → 409，让前端刷新后再改（否则后写的覆盖先写的）；
    - 内容与当前一致 → 200 返回事实：这是「响应丢了，前端重发一次」，
      再报冲突会让用户反复重试一个其实已经成功的操作。
    """
    owner = login(client, "admin", "admin@12345")
    source = _source(client, owner)
    fresh = source["stateVersion"]

    renamed = client.put(
        f"{API}/assets/{source['id']}/name",
        json={"expectedVersion": fresh, "name": "第一次改名"},
        headers=owner,
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["data"]["stateVersion"] == fresh + 1

    stale = client.put(
        f"{API}/assets/{source['id']}/name",
        json={"expectedVersion": fresh, "name": "另一个人改的名字"},
        headers=owner,
    )
    assert stale.status_code == 409
    assert "状态已变化" in stale.text

    retry = client.put(
        f"{API}/assets/{source['id']}/name",
        json={"expectedVersion": fresh, "name": "第一次改名"},
        headers=owner,
    )
    assert retry.status_code == 200
    assert retry.json()["data"]["stateVersion"] == fresh + 1, "重试不该再抬版本号"


def test_rename_keeps_the_bytes_and_the_storage_key(client: TestClient) -> None:
    """改名只改索引，不动字节：`storageKey` 是内容地址，重写一遍等于换了个文件。"""
    owner = login(client, "admin", "admin@12345")
    picture = upload(client, "character.png", "character", png_bytes())
    before = client.get(f"{API}/assets/{picture['id']}/file", headers=owner)
    assert before.status_code == 200

    renamed = _change(client, picture, owner, field="name", value="新名字").json()["data"]
    assert renamed["name"] == "新名字"
    # 内容寻址的两个证据：摘要是字节的，路径是摘要派生的，改名都不该动它们
    assert renamed["sha256"] == picture["sha256"]
    assert renamed["path"] == picture["path"]
    assert renamed["sizeBytes"] == picture["sizeBytes"]

    after = client.get(f"{API}/assets/{renamed['id']}/file", headers=owner)
    assert after.content == before.content


# --------------------------------------------------------------------------- #
# 封装发布：对所有账号可见，停用立刻关门
# --------------------------------------------------------------------------- #
async def test_published_pack_is_visible_to_other_accounts(client: TestClient) -> None:
    """专员在自己的账号里发布，别人就能在自己的目录里选到它。"""
    other = login(client, EDITOR, EDITOR_PASSWORD)
    image = upload(client, "character.png", "character", png_bytes())
    pack = await add_pack(
        owner=ADMIN, kind="character", name="跨账号封装", reference_ids=[image["id"]]
    )
    try:
        catalog = client.get(f"{API}/templates/replacement-catalog", headers=other)
        assert pack.id in {item["id"] for item in catalog.json()["data"]["packs"]}
    finally:
        await drop_pack(pack.id)


async def test_disabled_pack_is_hidden_and_refused(client: TestClient) -> None:
    """停用要同时作用在「看得见」和「还能不能用」两处。

    只从目录里藏起来不够：前端缓存里的旧目录、或直接按 id 提交的脚本，仍然会用
    一个已停用的封装去准备。所以 `prepare` 自己也校验状态，文案还要说清原因。
    """
    other = login(client, EDITOR, EDITOR_PASSWORD)
    image = upload(client, "character.png", "character", png_bytes())
    video = _source(client)
    draft = await add_pack(
        owner=ADMIN, kind="character", name="已停用", reference_ids=[image["id"]], status="draft"
    )
    try:
        catalog = client.get(f"{API}/templates/replacement-catalog", headers=other)
        assert draft.id not in {item["id"] for item in catalog.json()["data"]["packs"]}

        refused = client.post(
            f"{API}/templates/replacement-runs",
            json=_payload("character", video["id"], draft.id, 1),
            headers=other,
        )
        assert refused.status_code == 400
        assert "未发布" in refused.text
    finally:
        await drop_pack(draft.id)


# --------------------------------------------------------------------------- #
# 跨账号准备：快照归调用者
# --------------------------------------------------------------------------- #
async def test_borrowed_source_and_pack_create_the_callers_own_snapshot(
    client: TestClient,
) -> None:
    """公共原片 + 别人的封装，产出的记录必须是我自己的。

    这一条同时验证四件事：别人的原片能借（shared）、别人的封装能用（published）、
    记录归我（别人按 id 拿不到）、输入快照的地址指向当次归档而不是素材本身
    （素材被原作者删掉之后仍要能复核当初喂了什么）。
    """
    owner = login(client, "admin", "admin@12345")
    other = login(client, EDITOR, EDITOR_PASSWORD)
    source = _change(
        client, _source(client, owner), owner, field="shared", value=True
    ).json()["data"]
    image = upload(client, "character.png", "character", png_bytes())
    pack = await add_pack(
        owner=ADMIN, kind="character", name="别人的人物", reference_ids=[image["id"]]
    )
    created = client.post(
        f"{API}/templates/replacement-runs",
        json=_payload("character", source["id"], pack.id, pack.version),
        headers=other,
    )
    assert created.status_code == 201, created.text
    run_id = created.json()["data"]["id"]
    try:
        run = {"id": run_id}

        mine = client.get(f"{API}/templates/replacement-runs/{run['id']}", headers=other)
        assert mine.status_code == 200
        assert client.get(
            f"{API}/templates/replacement-runs/{run['id']}", headers=owner
        ).status_code == 404, "记录属于调用者，原作者也不该看到"

        inputs = mine.json()["data"]["inputSummary"]["assets"]
        assert inputs, "准备必须留下输入快照"
        for item in inputs:
            assert item["url"].startswith(f"{API}/templates/replacement-runs/{run['id']}/inputs/")
        snapshot = client.get(inputs[0]["url"], headers=other)
        assert snapshot.status_code == 200, "快照要能被调用者自己取回"
    finally:
        await drop_run(run_id)
        await drop_pack(pack.id)


async def test_revoking_sharing_blocks_new_runs_but_keeps_old_ones(client: TestClient) -> None:
    """收回分享不影响已经开始的记录——它已经复制走了，历史不能因为借出方反悔而消失。

    但新建必须被拒，而且要回 404「停止共享」：前端据此提示「换一段原片」，
    而不是笼统的 500。
    """
    owner = login(client, "admin", "admin@12345")
    other = login(client, EDITOR, EDITOR_PASSWORD)
    shared = _change(
        client, _source(client, owner), owner, field="shared", value=True
    ).json()["data"]
    image = upload(client, "character.png", "character", png_bytes())
    pack = await add_pack(
        owner=ADMIN, kind="character", name="别人的人物", reference_ids=[image["id"]]
    )
    created = client.post(
        f"{API}/templates/replacement-runs",
        json=_payload("character", shared["id"], pack.id, pack.version),
        headers=other,
    )
    assert created.status_code == 201, created.text
    run_id = created.json()["data"]["id"]
    try:
        _change(client, shared, owner, field="shared", value=False)

        again = client.post(
            f"{API}/templates/replacement-runs",
            json=_payload("character", shared["id"], pack.id, pack.version),
            headers=other,
        )
        assert again.status_code == 404
        assert "停止共享" in again.text

        kept = client.get(f"{API}/templates/replacement-runs/{run_id}", headers=other)
        assert kept.status_code == 200
    finally:
        await drop_run(run_id)
        await drop_pack(pack.id)


async def test_referenced_reference_cannot_be_trashed(client: TestClient) -> None:
    """「参考图被删导致准备失败」这条路根本不该存在——删除时就被引用检查挡下。

    拦在删除而不是拦在准备，是有意的：`prepare` 只有在封装已被引用、且素材
    已经消失时才需要处理这个分支，而那种状态只可能来自历史迁移。让日常删除
    通过，等于把「封装里少一张图」变成一条只能靠执行器报 ENOENT 才能发现的故障。
    """
    owner = login(client, "admin", "admin@12345")
    other = login(client, EDITOR, EDITOR_PASSWORD)
    image = upload(client, "character.png", "character", png_bytes())
    # 原片要借出去才轮得到别人准备，否则挡在前面的会是「停止共享」而不是引用检查
    source = _change(
        client, _source(client, owner), owner, field="shared", value=True
    ).json()["data"]
    pack = await add_pack(
        owner=ADMIN, kind="character", name="人物", reference_ids=[image["id"]]
    )
    try:
        trashed = _change(client, image, owner, field="deleted", value=True)
        assert trashed.status_code == 409
        assert "仍被引用" in trashed.text
        assert pack.name in trashed.text, "提示要点名是谁在用，否则用户不知道去哪改"

        still_usable = client.post(
            f"{API}/templates/replacement-runs",
            json=_payload("character", source["id"], pack.id, pack.version),
            headers=other,
        )
        assert still_usable.status_code == 201, still_usable.text
        await drop_run(still_usable.json()["data"]["id"])
    finally:
        await drop_pack(pack.id)


# --------------------------------------------------------------------------- #
# 去重口径：回收站里的记录不该参与「已经传过这份文件」的判断
# --------------------------------------------------------------------------- #
def test_reuploading_a_trashed_file_creates_a_fresh_record(client: TestClient) -> None:
    """删掉再传同一个文件，必须得到一条**新的、可见的**记录。

    `_find_by_digest` 之前不看 `deleted_at`，于是这种情况下它会命中回收站里那条
    已删除的行并原样返回：接口回 201，返回体带着 `deletedAt`，而「我的素材」
    默认不含回收站——用户看到的就是「提示上传成功，列表里没有」。
    而本该拦住并发重复的部分唯一索引那时条件写成 `source != 'archive'`，
    对所有行都不成立，一行也没进过，兜不住这个。
    """
    owner = login(client, "admin", "admin@12345")
    content = video_bytes(2, audio=True)
    first = _upload_as(client, owner, "same.mp4", "source", content)

    trashed = _change(client, first, owner, field="deleted", value=True)
    assert trashed.status_code == 200, trashed.text

    again = _upload_as(client, owner, "same.mp4", "source", content)
    assert again["id"] != first["id"], "回收站里的记录不该被当成「这份文件已经传过」"
    assert again["deletedAt"] is None

    listed = client.get(f"{API}/assets", params={"scope": "mine"}, headers=owner)
    visible = {item["id"] for item in listed.json()["data"]["items"]}
    assert again["id"] in visible, "重新上传的记录必须出现在「我的素材」里"


def test_restoring_a_trashed_duplicate_is_a_conflict_not_a_500(client: TestClient) -> None:
    """「删掉旧的后又传了同一个文件」再点恢复，要给可读冲突。

    新的唯一索引 `(owner_id, sha256, category) WHERE deleted_at IS NULL` 会让这次
    恢复撞库。不先查的话，客户端拿到的是未捕获的 `IntegrityError`（500），
    而它其实是一个完全可解释的业务状态：库里已经有一条一模一样的活跃素材。
    """
    owner = login(client, "admin", "admin@12345")
    content = video_bytes(2, audio=True)
    first = _upload_as(client, owner, "old.mp4", "source", content)
    assert _change(client, first, owner, field="deleted", value=True).status_code == 200

    second = _upload_as(client, owner, "new.mp4", "source", content)
    assert second["id"] != first["id"]

    # 删除操作让 stateVersion 加了一，恢复要带最新的版本号
    latest = client.get(f"{API}/assets/{first['id']}", headers=owner).json()["data"]
    restored = _change(client, latest, owner, field="deleted", value=False)
    assert restored.status_code == 409, restored.text
    assert "内容相同" in restored.text

    # 原来的两条都还在：冲突不该顺手改掉任何一边
    assert client.get(f"{API}/assets/{second['id']}", headers=owner).status_code == 200
    trashed = client.get(f"{API}/assets/{first['id']}", headers=owner).json()["data"]
    assert trashed["deletedAt"] is not None, "冲突后它应该还在回收站里"
