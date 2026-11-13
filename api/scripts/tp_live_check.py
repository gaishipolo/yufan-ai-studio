"""真服务 + 真 HTTP 的封装模板冒烟。

跑法（api 目录下）：
    .venv/Scripts/python.exe scripts/tp_live_check.py

前提：后端已起在 127.0.0.1:8080（`app.exe serve`）。

为什么放在 `scripts/` 而不是 `var/`：`var/` 是本地运行数据目录，会被
`scripts/clean_var.py` 当残留清掉——脚本本身是要长期留着复用的验证工具。
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import imageio_ffmpeg
from PIL import Image

BASE = "http://127.0.0.1:8080/api/v1"


def call(method, path, *, token=None, body=None, raw=None, content_type=None):
    url = BASE + path
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    elif raw is not None:
        data = raw
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode()
        try:
            return exc.code, json.loads(payload)
        except json.JSONDecodeError:
            return exc.code, {"raw": payload[:400]}


def check(label, ok, detail=""):
    mark = "OK " if ok else "FAIL"
    print(f"  [{mark}] {label}" + (f" -- {detail}" if detail else ""))
    if not ok:
        raise AssertionError(label)


def make_png(path: Path, color):
    Image.new("RGB", (320, 320), color).save(path)


def make_mp4(path: Path, color):
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run(
        [
            exe, "-y", "-f", "lavfi", "-i", f"color=c={color}:s=320x240:d=2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            str(path),
        ],
        check=True, capture_output=True,
    )


def upload(path: Path, filename, kind, token, endpoint="/library/assets/upload"):
    status, data = call(
        "POST", f"{endpoint}?filename={filename}&kind={kind}",
        token=token, raw=path.read_bytes(),
        content_type="application/octet-stream",
    )
    if status != 201:
        raise RuntimeError(f"upload failed {status}: {data}")
    return data["data"]


def main() -> int:
    print("== 0. 登录 ==")
    status, data = call(
        "POST", "/identity/login",
        body={"username": "admin", "password": "admin@12345"},
    )
    check("admin 登录", status == 200, f"status={status}")
    token = data["data"]["accessToken"]
    me = call("GET", "/identity/me", token=token)[1]["data"]
    print(f"     当前身份: {me.get('displayName') or me.get('username')} ({me.get('id')})")

    print("== 1. 造素材并上传 ==")
    with tempfile.TemporaryDirectory() as folder:
        base = Path(folder)
        good_png, bad_png = base / "good.png", base / "bad.png"
        make_png(good_png, (200, 30, 30))
        make_png(bad_png, (30, 30, 200))
        mp4 = base / "ref.mp4"
        make_mp4(mp4, "green")

        video = upload(mp4, "ref.mp4", "video", token, "/library/source-videos")
        good = upload(good_png, "good.png", "image", token)
        bad = upload(bad_png, "bad.png", "image", token)
        print(f"     参考视频 = {video['id']}")
        print(f"     good     = {good['id']}  tags={good.get('tagIds')}")
        print(f"     bad      = {bad['id']}  tags={bad.get('tagIds')}")
        check("good/bad 不是同一条（避免内容去重）", good["id"] != bad["id"])

    print("== 2. 建标签（给 good 打上「产品」一级 + 「白底」二级）==")
    status, data = call("GET", "/library/tags?state=active&library=personal", token=token)
    check("标签列表可读", status == 200, f"status={status}")
    tags = data["data"]
    parent = next((t for t in tags if not t.get("parentId")), None)
    child = next((t for t in tags if t.get("parentId")), None)
    if not parent:
        status, data = call(
            "POST", "/library/tags", token=token,
            body={"name": "产品", "parentId": ""},
        )
        check(
            "建一级标签",
            status in (200, 201),
            f"{status} {json.dumps(data, ensure_ascii=False)[:200]}",
        )
        parent = data["data"]
    if not child:
        status, data = call(
            "POST", "/library/tags", token=token,
            body={"name": "白底", "parentId": parent["id"]},
        )
        check(
            "建二级标签",
            status in (200, 201),
            f"{status} {json.dumps(data, ensure_ascii=False)[:200]}",
        )
        child = data["data"]
    print(f"     一级 = {parent['id']} {parent['name']}")
    print(f"     二级 = {child['id']} {child['name']}")

    status, data = call(
        "PATCH", f"/library/assets/{good['id']}", token=token,
        body={"tagIds": [child["id"]]},
    )
    check(
        "给 good 挂二级标签",
        status == 200,
        f"{status} {json.dumps(data, ensure_ascii=False)[:200]}",
    )
    attached = [t.get("id") for t in (data["data"].get("tags") or [])]
    check("标签已挂上", child["id"] in attached, f"tags={attached}")

    print("== 3. 保存草稿（第1槽=参考视频固定，第2槽=筛选槽位）==")
    status, data = call(
        "POST", "/template-packs", token=token,
        body={
            "name": "封装模板-实测",
            "description": "真服务冒烟",
            "prompt": "按 {{Mixed 1}} 的节奏，用 {{Mixed 2}} 做主图",
            "sourceVideoAssetId": video["id"],
            "sourceVideoIdSpace": "v2",
            "sourceDurationSec": 2.0,
            "aspectRatio": "9:16",
            "slots": [
                {
                    "token": "{{Mixed 1}}", "slotKind": "fixed", "label": "参考视频",
                    "assetId": video["id"], "assetIdSpace": "v2", "mediaType": "video",
                },
                {
                    "token": "{{Mixed 2}}", "slotKind": "filtered", "label": "产品主图",
                    "filterKind": "image", "filterTagIds": [parent["id"]], "required": True,
                },
            ],
        },
    )
    check(
        "保存草稿",
        status in (200, 201),
        f"{status} {json.dumps(data, ensure_ascii=False)[:300]}",
    )
    pack = data["data"]
    pack_id = pack["id"]
    print(f"     packId = {pack_id}  status = {pack['status']}  key = {pack['templateKey']}")

    print("== 4. 幂等：同 key 同配置再存一次 ==")
    status, data2 = call(
        "POST", "/template-packs", token=token,
        body={
            "name": "封装模板-实测",
            "description": "真服务冒烟",
            "prompt": "按 {{Mixed 1}} 的节奏，用 {{Mixed 2}} 做主图",
            "sourceVideoAssetId": video["id"],
            "sourceVideoIdSpace": "v2",
            "sourceDurationSec": 2.0,
            "aspectRatio": "9:16",
            "templateKey": pack["templateKey"],
            "slots": [
                {
                    "token": "{{Mixed 1}}", "slotKind": "fixed", "label": "参考视频",
                    "assetId": video["id"], "assetIdSpace": "v2", "mediaType": "video",
                },
                {
                    "token": "{{Mixed 2}}", "slotKind": "filtered", "label": "产品主图",
                    "filterKind": "image", "filterTagIds": [parent["id"]], "required": True,
                },
            ],
        },
    )
    check("同 key 同配置幂等", status in (200, 201) and data2["data"]["id"] == pack_id,
          f"status={status} id={data2.get('data', {}).get('id')}")

    print("== 5. 草稿不进模板库 ==")
    status, data = call("GET", "/template-packs", token=token)
    published_ids = [p["id"] for p in data["data"]["items"] if p["status"] == "published"]
    check("草稿不出现在已发布列表", pack_id not in published_ids)

    print("== 6. 草稿期改提示词 ==")
    status, data = call(
        "PATCH", f"/template-packs/{pack_id}/prompt", token=token,
        body={"prompt": "用 {{Mixed 2}} 作为封面，节奏跟随 {{Mixed 1}}"},
    )
    check(
        "草稿期可改提示词",
        status == 200,
        f"{status} {json.dumps(data, ensure_ascii=False)[:200]}",
    )

    print("== 7. 发布 ==")
    status, data = call("POST", f"/template-packs/{pack_id}/publish", token=token)
    check(
        "发布",
        status == 200,
        f"{status} {json.dumps(data, ensure_ascii=False)[:300]}",
    )
    print(f"     status = {data['data']['status']}")

    print("== 7. 候选池：只能选合规素材 ==")
    status, data = call(
        "GET",
        f"/template-packs/{pack_id}/candidates?token=%7B%7BMixed%202%7D%7D",
        token=token,
    )
    check(
        "候选池可读",
        status == 200,
        f"{status} {json.dumps(data, ensure_ascii=False)[:300]}",
    )
    items = data["data"]["items"]
    ids = [i["assetId"] for i in items]
    print(f"     候选池 = {[(i['name'], i['assetId']) for i in items]}")
    check("合规素材 good 在池内", good["id"] in ids)
    check("不合规素材 bad 不在池内", bad["id"] not in ids)

    print("== 8. 服务端复核：越权提交被拒 ==")
    status, data = call(
        "POST", f"/template-packs/{pack_id}/verify", token=token,
        body={"picks": {"{{Mixed 2}}": bad["id"]}},
    )
    check(
        "不合规选材被拒",
        status == 400,
        f"{status} {json.dumps(data, ensure_ascii=False)[:200]}",
    )

    print("== 9. 服务端复核：合规提交通过 ==")
    status, data = call(
        "POST", f"/template-packs/{pack_id}/verify", token=token,
        body={"picks": {"{{Mixed 2}}": good["id"]}},
    )
    check(
        "合规选材通过",
        status == 200,
        f"{status} {json.dumps(data, ensure_ascii=False)[:200]}",
    )
    print(f"     picks = {data['data']['picks']}")

    print("== 10. 已发布锁内容：改提示词被拒 ==")
    status, data = call(
        "PATCH", f"/template-packs/{pack_id}/prompt", token=token,
        body={"prompt": "偷偷改一下 {{Mixed 2}}"},
    )
    check(
        "已发布不可改提示词",
        status == 409,
        f"{status} {json.dumps(data, ensure_ascii=False)[:200]}",
    )

    print("== 11. 停用 ==")
    status, data = call("POST", f"/template-packs/{pack_id}/disable", token=token)
    check(
        "停用",
        status == 200,
        f"{status} {json.dumps(data, ensure_ascii=False)[:200]}",
    )
    check("停用后 status = disabled", data["data"]["status"] == "disabled")

    print("\n全部通过 ✓")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as exc:
        print(f"\n冒烟失败：{exc}")
        sys.exit(1)
