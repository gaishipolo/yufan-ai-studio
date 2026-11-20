"""封装的建、列、取，以及「成员不再单列」。

四个断言方向：

1. **继承共同标签、不继承各自独有的**：`6036` 对整包有意义，`正面图/三视图`
   是成员各自的 —— 后者正是取图时区分成员的依据，打进包里会让整包看起来
   「什么角色都有」，取图就无从匹配。
2. **成员进了包就不再单列**：列表与角标同口径（否则翻页翻到空页、角标对不上）。
3. **包列表按槽位候选标签过滤**：这就是「产品包 / 服装包 / 人物包各自归位」
   的机制，不需要给包再加类型字段。
4. **成员被删仍然列出并标记**：包静默少一张，比露出来更糟。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from library_support import LIB_RW, admin_headers, make_user, upload_image

API = "/api/v1"


def _headers(client: TestClient) -> dict[str, str]:
    admin = admin_headers(client)
    _, headers = make_user(client, admin, LIB_RW)
    return headers


def _tag(client: TestClient, headers: dict[str, str], name: str) -> dict:
    response = client.post(f"{API}/library/tags", json={"name": name}, headers=headers)
    assert response.status_code in (200, 201), response.text
    return response.json()["data"]


def _tag_asset(
    client: TestClient, headers: dict[str, str], asset_id: str, tag_ids: list[str]
) -> None:
    response = client.patch(
        f"{API}/library/assets/{asset_id}", json={"tagIds": tag_ids}, headers=headers
    )
    assert response.status_code == 200, response.text


def _pack(client: TestClient, headers: dict[str, str], name: str, ids: list[str]):
    return client.post(
        f"{API}/library/packs", json={"name": name, "assetIds": ids}, headers=headers
    )


def _shared(
    client: TestClient, headers: dict[str, str], ids: list[str], name: str = "共同产品"
) -> str:
    """给这组素材打上同一个标签 —— 建封装的前提（成员至少共有一个标签）。

    **合并而不是覆盖**已有标签：用例自己打的「正面图 / 三视图」不能丢，
    那正是「只继承共同标签」那条用例的观察对象。

    专门验证「没有共同标签被拒」的用例**不要**用它，直接 post。
    """
    tag = _tag(client, headers, name)
    for asset_id in ids:
        detail = client.get(f"{API}/library/assets/{asset_id}", headers=headers).json()["data"]
        existing = [item["id"] for item in (detail.get("tags") or [])]
        if tag["id"] in existing:
            continue
        client.patch(
            f"{API}/library/assets/{asset_id}",
            json={"tagIds": [*existing, tag["id"]]},
            headers=headers,
        )
    return tag["id"]


def _asset_names(client: TestClient, headers: dict[str, str]) -> list[str]:
    body = client.get(f"{API}/library/assets", params={"pageSize": 100}, headers=headers)
    assert body.status_code == 200, body.text
    return [item["name"] for item in body.json()["data"]["items"]]


def test_pack_inherits_only_common_tags(client: TestClient) -> None:
    headers = _headers(client)
    product = _tag(client, headers, "6036")
    front = _tag(client, headers, "正面图")
    views = _tag(client, headers, "三视图")

    first = upload_image(client, API, headers, name="6036-黑-正面.png", color="red")
    second = upload_image(client, API, headers, name="6036-黑-三视图.png", color="blue")
    _tag_asset(client, headers, first["id"], [product["id"], front["id"]])
    _tag_asset(client, headers, second["id"], [product["id"], views["id"]])

    body = _pack(client, headers, "6036 黑", [first["id"], second["id"]])
    assert body.status_code == 201, body.text
    pack = body.json()["data"]
    assert pack["memberCount"] == 2
    # 并集：包的 tagIds 汇总了成员的标签，选择器靠它判断"能不能填这一格"
    assert set(pack["tagIds"]) == {product["id"], front["id"], views["id"]}

    detail = client.get(f"{API}/library/assets/{pack['id']}", headers=headers).json()["data"]
    assert detail["kind"] == "pack"
    # **包身上**只留共同标签：正面图/三视图不继承
    assert {item["name"] for item in detail["tags"]} == {"6036"}


def test_pack_rejects_unknown_or_nested_members(client: TestClient) -> None:
    headers = _headers(client)
    image = upload_image(client, API, headers, name="a.png", color="green")
    _shared(client, headers, [image["id"]])

    missing = _pack(client, headers, "空包", ["nonexistent-id"])
    assert missing.status_code == 400
    assert "不存在或无权访问" in missing.json()["message"]

    empty = client.post(
        f"{API}/library/packs", json={"name": "空包", "assetIds": []}, headers=headers
    )
    assert empty.status_code == 422

    pack = _pack(client, headers, "第一层", [image["id"]])
    assert pack.status_code == 201, pack.text
    nested = _pack(client, headers, "第二层", [pack.json()["data"]["id"]])
    assert nested.status_code == 400
    assert "不能嵌套" in nested.json()["message"]


def test_pack_members_stop_listing_separately(client: TestClient) -> None:
    headers = _headers(client)
    first = upload_image(client, API, headers, name="6036-黑-正面.png", color="yellow")
    second = upload_image(client, API, headers, name="6036-黑-三视图.png", color="cyan")
    _shared(client, headers, [first["id"], second["id"]])

    before = _asset_names(client, headers)
    assert "6036-黑-正面.png" in before and "6036-黑-三视图.png" in before

    body = _pack(client, headers, "6036 黑", [first["id"], second["id"]])
    assert body.status_code == 201, body.text

    after = _asset_names(client, headers)
    # 成员不单列，包本身要出现
    assert "6036-黑-正面.png" not in after
    assert "6036-黑-三视图.png" not in after
    assert "6036 黑" in after


def test_pack_list_filters_by_slot_candidate_tags(client: TestClient) -> None:
    """槽位传自己的候选标签过来，只有含该标签成员的包才会出现。

    这就是「产品包 / 服装包 各自归位」的判据 —— 不给包加类型字段也能分工。
    """
    headers = _headers(client)
    front = _tag(client, headers, "正面图")
    dress = _tag(client, headers, "服装")

    with_front = upload_image(client, API, headers, name="f.png", color="orange")
    _tag_asset(client, headers, with_front["id"], [front["id"]])
    only_dress = upload_image(client, API, headers, name="d.png", color="white")
    _tag_asset(client, headers, only_dress["id"], [dress["id"]])

    assert _pack(client, headers, "产品包", [with_front["id"]]).status_code == 201
    assert _pack(client, headers, "服装包", [only_dress["id"]]).status_code == 201

    for_front = client.get(
        f"{API}/library/packs", params={"tagIds": [front["id"]]}, headers=headers
    ).json()["data"]
    assert [item["name"] for item in for_front] == ["产品包"]

    everything = client.get(f"{API}/library/packs", headers=headers).json()["data"]
    assert {item["name"] for item in everything} == {"产品包", "服装包"}


def test_trashed_member_stops_pulling_the_pack_into_a_slot(client: TestClient) -> None:
    """成员进了回收站，包就不该再出现在**那一格**的候选里。

    列表的判据与分发是同一条：「这个包能不能填这一格」。分发跳过了已删成员，
    列表却把它的标签算进并集的话，包会出现在一个一格都填不上的网格里 ——
    用户点下去只会得到「没有能对上这些槽位的素材」。
    """
    headers = _headers(client)
    detail_tag = _tag(client, headers, "细节图")

    front = upload_image(client, API, headers, name="f.png", color="orange")
    detail = upload_image(client, API, headers, name="d.png", color="white")
    shared_tag = _shared(client, headers, [front["id"], detail["id"]], name="6036")
    _tag_asset(client, headers, detail["id"], [detail_tag["id"], shared_tag])

    body = _pack(client, headers, "6036 一套", [front["id"], detail["id"]])
    assert body.status_code == 201, body.text

    def listed(tag_id: str) -> list[str]:
        rows = client.get(
            f"{API}/library/packs", params={"tagIds": [tag_id]}, headers=headers
        ).json()["data"]
        return [item["name"] for item in rows]

    assert listed(detail_tag["id"]) == ["6036 一套"]

    removed = client.delete(f"{API}/library/assets/{detail['id']}", headers=headers)
    assert removed.status_code in (200, 204), removed.text

    # 那一格填不上了，包就不再出现在那里；但「6036」这一维仍然看得到它。
    assert listed(detail_tag["id"]) == []
    assert listed(shared_tag) == ["6036 一套"]


def test_pack_detail_marks_deleted_member(client: TestClient) -> None:
    """成员进了回收站仍然列出并标记 —— 包静默少一张，取图时才炸更糟。"""
    headers = _headers(client)
    keep = upload_image(client, API, headers, name="keep.png", color="purple")
    drop = upload_image(client, API, headers, name="drop.png", color="black")
    _shared(client, headers, [keep["id"], drop["id"]])
    body = _pack(client, headers, "6036 黑", [keep["id"], drop["id"]])
    assert body.status_code == 201, body.text
    pack_id = body.json()["data"]["id"]

    removed = client.delete(f"{API}/library/assets/{drop['id']}", headers=headers)
    assert removed.status_code in (200, 204), removed.text

    detail = client.get(f"{API}/library/packs/{pack_id}", headers=headers).json()["data"]
    by_name = {item["name"]: item for item in detail["members"]}
    assert by_name["drop.png"]["deleted"] is True
    assert by_name["keep.png"]["deleted"] is False
    assert detail["memberCount"] == 2


def test_pack_member_count_and_items_are_exposed(client: TestClient) -> None:
    """「装了几张」要能从列表读出来，「装的是哪几张」要能从详情读出来。

    这条守的是**出参映射**：字段在 schema 里定义了、序列化不报错，漏映射的
    表现是永远输出默认值（0 / 空数组）—— 2026-11-19 的 `randomPrompt` 就是
    这么漏的：库里对、接口返回空，前端据此判成「没配」。
    """
    headers = _headers(client)
    # 颜色必须不同：同色会被「按内容秒传」去重成同一条资产，包就只剩一个成员
    first = upload_image(client, API, headers, name="6036-黑-正面平铺.png", color="red")["id"]
    second = upload_image(client, API, headers, name="6036-黑-立体图.png", color="blue")["id"]
    _shared(client, headers, [first, second])
    response = client.post(
        f"{API}/library/packs",
        json={"name": "6036 黑", "assetIds": [first, second]},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    pack_id = response.json()["data"]["id"]

    # 列表：这张卡就是「成员不再单列」之后唯一能看到那几张图的地方，
    # 只给封面不给张数，等于让人点进去才知道装了什么。
    listing = client.get(f"{API}/library/assets?kind=pack&view=all", headers=headers)
    assert listing.status_code == 200, listing.text
    cards = {item["id"]: item for item in listing.json()["data"]["items"]}
    assert cards[pack_id]["memberCount"] == 2
    assert cards[pack_id]["kind"] == "pack"

    # 详情：包没有自己的字节，媒体区只能靠成员列表
    detail = client.get(f"{API}/library/assets/{pack_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    data = detail.json()["data"]
    assert data["memberCount"] == 2
    assert [item["name"] for item in data["packItems"]] == [
        "6036-黑-正面平铺.png",
        "6036-黑-立体图.png",
    ]
    # 成员顺序 = 建包时的选择顺序，不是按 id 或名字排的
    assert all(item["coverUrl"] for item in data["packItems"])


def test_pack_members_are_hidden_from_the_list_but_not_the_picker(
    client: TestClient,
) -> None:
    """「成员不单列」只作用于资产库列表，**不作用于选择器**。

    两个场景的结论相反：
    - 列表是「看我的库」：同一张图既在包里又单列，等于出现两次；
    - 选择器是「挑素材去用」：藏掉就等于那几张图选不到了。模板里现有的
      槽位默认还是按素材挑（`pickMode=asset`），藏了会立刻表现为
      「我上传的图不见了」—— 那是比「看到两次」严重得多的问题。
    """
    headers = _headers(client)
    first = upload_image(client, API, headers, name="6036-黑-正面.png", color="silver")
    _shared(client, headers, [first["id"]])
    pack = client.post(
        f"{API}/library/packs",
        json={"name": "6036 黑", "assetIds": [first["id"]]},
        headers=headers,
    )
    assert pack.status_code == 201, pack.text

    listing = client.get(f"{API}/library/assets?view=all&pageSize=100", headers=headers)
    listed = {item["id"] for item in listing.json()["data"]["items"]}
    assert first["id"] not in listed, "进了包的成员不该再单列"
    assert any(
        item["id"] == pack.json()["data"]["id"] for item in listing.json()["data"]["items"]
    ), "包本身要在列表里"

    selection = client.get(f"{API}/library/selection?pageSize=100", headers=headers)
    picked = {item["id"] for item in selection.json()["data"]["items"]}
    assert first["id"] in picked, "选择器要能选到成员，否则模板里挑不到这张图"


def test_asset_belongs_to_at_most_one_pack(client: TestClient) -> None:
    """一张素材只能进一个封装。

    不拦的话，「6036 黑」和「6 月新品」都含同一张正面图，改一个包的成员会
    连带影响另一个包的取图 —— 而使用者以为两个包互不相干。

    选择器会把已进包的标灰（`packId` / `packName`），这里是最后一道闸：
    界面没拦住时接口也不能默默放行。
    """
    headers = _headers(client)
    first = upload_image(client, API, headers, name="6036-黑-正面.png", color="coral")
    second = upload_image(client, API, headers, name="6036-黑-立体.png", color="khaki")
    _shared(client, headers, [first["id"], second["id"]])

    created = client.post(
        f"{API}/library/packs",
        json={"name": "6036 黑", "assetIds": [first["id"], second["id"]]},
        headers=headers,
    )
    assert created.status_code == 201, created.text

    # 同一张图再进另一个包：拒，并说清已经在哪个包里
    again = client.post(
        f"{API}/library/packs",
        json={"name": "6 月新品", "assetIds": [first["id"]]},
        headers=headers,
    )
    assert again.status_code == 409, again.text
    assert "6036 黑" in again.json()["message"]


def test_selection_tells_which_pack_an_asset_is_in(client: TestClient) -> None:
    """选择器要能显示「已在封装「X」里」，否则建包时只能选完才被拒。"""
    headers = _headers(client)
    member = upload_image(client, API, headers, name="6036-白-正面.png", color="plum")
    _shared(client, headers, [member["id"]])
    loose = upload_image(client, API, headers, name="散图.png", color="lime")
    pack = client.post(
        f"{API}/library/packs",
        json={"name": "6036 白", "assetIds": [member["id"]]},
        headers=headers,
    )
    pack_id = pack.json()["data"]["id"]

    selection = client.get(f"{API}/library/selection?pageSize=100", headers=headers)
    picked = {item["id"]: item for item in selection.json()["data"]["items"]}
    assert picked[member["id"]]["packId"] == pack_id
    assert picked[member["id"]]["packName"] == "6036 白"
    # 没进包的素材这两个字段是空 —— 前端据此决定要不要标灰
    assert picked[loose["id"]]["packId"] is None
    assert picked[loose["id"]]["packName"] is None


def test_pack_allows_an_empty_name_and_falls_back(client: TestClient) -> None:
    """名字不是必填：留空记成「未命名封装」。

    界面上逼人现起名字的结果是「新建封装 (1) (2) (3)」—— 比一个统一的
    「未命名封装」更没信息量，而且用户还得先想个名字才能建。
    """
    headers = _headers(client)
    member = upload_image(client, API, headers, name="6036-黑-正面.png", color="peru")
    _shared(client, headers, [member["id"]])
    created = client.post(
        f"{API}/library/packs",
        json={"name": "   ", "assetIds": [member["id"]]},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    assert created.json()["data"]["name"] == "未命名封装"


def test_pack_requires_at_least_one_shared_tag(client: TestClient) -> None:
    """成员必须有至少一个共同标签，否则不成「一套」。

    封装的意思是「同一件东西的几个视角」，而这件事在系统里唯一能判的信号就是
    标签：`6036` 这类产品编号是成员共同带的，`正面图 / 三视图` 是各自独有的。
    一组没有共同标签的图，要么根本不是一回事，要么还没打标 —— 打出来的包在
    模板库选择器里也没有依据归到任何槽位（那里靠「成员标签 ∩ 槽位候选标签」判）。

    而且要在**建行之前**拦：先落一行包再报错，会留下一条没有成员的空壳。
    """
    headers = _headers(client)
    front = _tag(client, headers, "正面图")
    views = _tag(client, headers, "三视图")

    first = upload_image(client, API, headers, name="a-正面.png", color="aqua")
    second = upload_image(client, API, headers, name="b-三视图.png", color="beige")
    _tag_asset(client, headers, first["id"], [front["id"]])
    _tag_asset(client, headers, second["id"], [views["id"]])

    before = client.get(f"{API}/library/assets?kind=pack&view=all", headers=headers)
    assert before.json()["data"]["total"] == 0

    response = _pack(client, headers, "混在一起的", [first["id"], second["id"]])
    assert response.status_code == 400, response.text
    assert "共同的标签" in response.json()["message"]

    # 没有留下空壳
    after = client.get(f"{API}/library/assets?kind=pack&view=all", headers=headers)
    assert after.json()["data"]["total"] == 0


def test_pack_accepts_members_sharing_just_one_tag(client: TestClient) -> None:
    """只共有一个标签也算「一套」——判据是「有没有」，不是「有几个」。"""
    headers = _headers(client)
    product = _tag(client, headers, "6036")
    front = _tag(client, headers, "正面图")

    first = upload_image(client, API, headers, name="c-正面.png", color="cyan")
    second = upload_image(client, API, headers, name="d-黑白.png", color="brown")
    _tag_asset(client, headers, first["id"], [product["id"], front["id"]])
    _tag_asset(client, headers, second["id"], [product["id"]])

    response = _pack(client, headers, "只共有一个标签", [first["id"], second["id"]])
    assert response.status_code == 201, response.text
    # `PackOut` 给的是 tagIds（成员标签的并集），不是 tags —— 包身上只有共同标签
    detail = client.get(
        f"{API}/library/assets/{response.json()['data']['id']}", headers=headers
    ).json()["data"]
    assert {item["name"] for item in detail["tags"]} == {"6036"}


def test_members_come_back_when_their_pack_goes_to_trash(client: TestClient) -> None:
    """包进了回收站，成员要回到个人库。

    不这么做的话：包自己只在回收站里，成员又被「不单列」排除在个人库之外 ——
    那几张图会凭空消失，用户得进回收站翻才找得到。
    """
    headers = _headers(client)
    first = upload_image(client, API, headers, name="6036-黑-正面.png", color="orchid")
    second = upload_image(client, API, headers, name="6036-黑-三视图.png", color="salmon")
    _shared(client, headers, [first["id"], second["id"]])
    pack = _pack(client, headers, "6036 黑", [first["id"], second["id"]]).json()["data"]

    names = _asset_names(client, headers)
    assert "6036-黑-正面.png" not in names

    removed = client.delete(f"{API}/library/assets/{pack['id']}", headers=headers)
    assert removed.status_code in (200, 204), removed.text

    # 包在回收站里
    trashed = client.get(f"{API}/library/assets?state=trash&pageSize=100", headers=headers)
    assert any(item["id"] == pack["id"] for item in trashed.json()["data"]["items"])
    # 成员回到个人库
    names = _asset_names(client, headers)
    assert "6036-黑-正面.png" in names
    assert "6036-黑-三视图.png" in names


def test_asset_in_a_trashed_pack_is_still_taken(client: TestClient) -> None:
    """回收站里的包**仍然占着**成员，且报错要说清这一点。

    那些包还能恢复，恢复之后同一张图就属于两个包了。只说「已经属于封装「X」」
    而那个包在回收站里，用户翻遍资产库也找不到它。
    """
    headers = _headers(client)
    alive = upload_image(client, API, headers, name="6036-白-正面.png", color="tan")
    _shared(client, headers, [alive["id"]])
    pack = _pack(client, headers, "6036 白", [alive["id"]]).json()["data"]

    client.delete(f"{API}/library/assets/{pack['id']}", headers=headers)

    again = _pack(client, headers, "另一个包", [alive["id"]])
    assert again.status_code == 409, again.text
    assert "回收站" in again.json()["message"]


def test_pack_can_be_renamed_and_remembers_members(client: TestClient) -> None:
    """改名不动成员。两个字段独立，传哪个改哪个。"""
    headers = _headers(client)
    first = upload_image(client, API, headers, name="6036-黑-正面.png", color="violet")
    second = upload_image(client, API, headers, name="6036-黑-三视图.png", color="wheat")
    _shared(client, headers, [first["id"], second["id"]])
    pack = _pack(client, headers, "临时名", [first["id"], second["id"]]).json()["data"]

    renamed = client.patch(
        f"{API}/library/packs/{pack['id']}", json={"name": "6036 黑"}, headers=headers
    )
    assert renamed.status_code == 200, renamed.text
    body = renamed.json()["data"]
    assert body["name"] == "6036 黑"
    # 成员没被顺手清掉
    assert [member["id"] for member in body["members"]] == [first["id"], second["id"]]


def test_editing_members_keeps_existing_ones_and_recomputes_tags(
    client: TestClient,
) -> None:
    """换成员：留下来的不该被唯一性检查拦住，标签也要重算。

    `exclude_pack_id` 就是为这条存在的 —— 不排除自己，改一次成员就会被
    「这些素材已经属于封装「自己」」拦住。
    """
    headers = _headers(client)
    product = _tag(client, headers, "6036")
    front = _tag(client, headers, "正面图")
    views = _tag(client, headers, "三视图")
    detail = _tag(client, headers, "细节图")

    first = upload_image(client, API, headers, name="a.png", color="snow")
    second = upload_image(client, API, headers, name="b.png", color="azure")
    third = upload_image(client, API, headers, name="c.png", color="beige")
    _tag_asset(client, headers, first["id"], [product["id"], front["id"]])
    _tag_asset(client, headers, second["id"], [product["id"], views["id"]])
    _tag_asset(client, headers, third["id"], [product["id"], detail["id"]])

    pack = _pack(client, headers, "6036", [first["id"], second["id"]]).json()["data"]
    # `PackOut.tagIds` 是**成员标签的并集**（选择器靠它判断能不能填）；
    # 包**身上**的标签要走资产详情 —— 那里只有共同标签。
    def pack_own_tags() -> set[str]:
        row = client.get(f"{API}/library/assets/{pack['id']}", headers=headers).json()["data"]
        return {item["name"] for item in row["tags"]}

    assert pack_own_tags() == {"6036"}

    # 保留 first，用 third 换掉 second：first 属于自己，不该触发冲突
    updated = client.patch(
        f"{API}/library/packs/{pack['id']}",
        json={"assetIds": [first["id"], third["id"]]},
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()["data"]
    assert [member["id"] for member in body["members"]] == [first["id"], third["id"]]
    # 共同标签仍是 6036（三视图走了、细节图来了，两个都不是共同的）
    assert pack_own_tags() == {"6036"}


def test_editing_members_rejects_empty_and_foreign_assets(client: TestClient) -> None:
    headers = _headers(client)
    mine = upload_image(client, API, headers, name="我的.png", color="olive")
    other = upload_image(client, API, headers, name="别人的.png", color="navy")
    _shared(client, headers, [mine["id"], other["id"]])
    pack = _pack(client, headers, "6036", [mine["id"]]).json()["data"]

    empty = client.patch(
        f"{API}/library/packs/{pack['id']}", json={"assetIds": []}, headers=headers
    )
    assert empty.status_code in (400, 422), empty.text

    # 另一张已经在别的包里 → 409；跨包复用会连带影响两边的取图
    other_pack = _pack(client, headers, "另一个", [other["id"]]).json()["data"]
    assert other_pack["memberCount"] == 1
    stolen = client.patch(
        f"{API}/library/packs/{pack['id']}",
        json={"assetIds": [mine["id"], other["id"]]},
        headers=headers,
    )
    assert stolen.status_code == 409, stolen.text


def test_only_owner_can_edit_a_pack(client: TestClient) -> None:
    """别人的包一律 404：既不说「存在但没权限」，也不泄露别人的库结构。"""
    admin = admin_headers(client)
    _, owner = make_user(client, admin, LIB_RW)
    _, stranger = make_user(client, admin, LIB_RW)

    member = upload_image(client, API, owner, name="某人的.png", color="crimson")
    _shared(client, owner, [member["id"]])
    pack = _pack(client, owner, "某人的包", [member["id"]]).json()["data"]

    response = client.patch(
        f"{API}/library/packs/{pack['id']}", json={"name": "改个名"}, headers=stranger
    )
    assert response.status_code == 404, response.text


def test_replacing_every_member_swaps_the_inherited_tag(client: TestClient) -> None:
    """整批换成员时，旧标签必须**掉下去**。

    包身上的标签是「成员共同标签的投影」，是派生值。增量维护（只加不减）会留下
    「上一次的共同标签」，而它已经不共同了 —— 模板库正是靠这个投影判断
    「这个包能不能填那一格」，留着的旧标签会把包放进填不上的槽位里。
    """
    headers = _headers(client)
    old_a = upload_image(client, API, headers, name="6036-a.png", color="gold")
    old_b = upload_image(client, API, headers, name="6036-b.png", color="honeydew")
    new_a = upload_image(client, API, headers, name="9999-a.png", color="indigo")
    new_b = upload_image(client, API, headers, name="9999-b.png", color="ivory")
    _shared(client, headers, [old_a["id"], old_b["id"]], name="6036")
    _shared(client, headers, [new_a["id"], new_b["id"]], name="9999")

    pack = _pack(client, headers, "先叫 6036", [old_a["id"], old_b["id"]]).json()["data"]
    row = client.get(f"{API}/library/assets/{pack['id']}", headers=headers).json()["data"]
    assert {item["name"] for item in row["tags"]} == {"6036"}

    updated = client.patch(
        f"{API}/library/packs/{pack['id']}",
        json={"assetIds": [new_a["id"], new_b["id"]]},
        headers=headers,
    )
    assert updated.status_code == 200, updated.text

    row = client.get(f"{API}/library/assets/{pack['id']}", headers=headers).json()["data"]
    # 6036 必须走了，9999 必须在了
    assert {item["name"] for item in row["tags"]} == {"9999"}


def test_pack_list_can_require_every_tag(client: TestClient) -> None:
    """`mode=all`：成员标签的并集要覆盖**全部** tagIds。

    封装容器（一次选包填多格）用它：容器里有「正面图 + 三视图」两格，
    包里就必须两张都对得上 —— 只对得上一张的包候选里不该出现，
    点下去只填一半这件事没法在界面上解释。
    """
    headers = _headers(client)
    front = _tag(client, headers, "正面图")
    views = _tag(client, headers, "三视图")
    shared = _tag(client, headers, "6036")

    both_a = upload_image(client, API, headers, name="both-a.png", color="orange")
    both_b = upload_image(client, API, headers, name="both-b.png", color="white")
    only_front = upload_image(client, API, headers, name="front.png", color="purple")

    _tag_asset(client, headers, both_a["id"], [shared["id"], front["id"]])
    _tag_asset(client, headers, both_b["id"], [shared["id"], views["id"]])
    _tag_asset(client, headers, only_front["id"], [shared["id"], front["id"]])

    assert _pack(client, headers, "一套（正+三）", [both_a["id"], both_b["id"]]).status_code == 201
    assert _pack(client, headers, "只有正面", [only_front["id"]]).status_code == 201

    wanted = [shared["id"], front["id"], views["id"]]

    def names(mode: str) -> list[str]:
        rows = client.get(
            f"{API}/library/packs",
            params={"tagIds": wanted, "mode": mode},
            headers=headers,
        ).json()["data"]
        return sorted(item["name"] for item in rows)

    # any：能填上其中一格就算候选
    assert names("any") == ["一套（正+三）", "只有正面"]
    # all：必须把这几格都填得上
    assert names("all") == ["一套（正+三）"]
