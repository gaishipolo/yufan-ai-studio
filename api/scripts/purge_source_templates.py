#!/usr/bin/env python3
"""清空「模板库 / 原片模板库」中的全部 source 原片（一次性运维脚本）。

模板库页面显示的「模板」并不是 `template_packs`，而是 `assets` 表里
`category = 'source'` 的归档原片。本脚本把这些原片**彻底删除**。

删除不直接改数据库：走应用自带的两步接口
    PUT    /api/v1/assets/{id}/deleted   {"expectedVersion": n, "deleted": true}
    DELETE /api/v1/assets/{id}
第二步会由 `modules/asset/archive_purge.py::purge_trash_asset` 统一完成
引用检查、DB 删行、JSON 镜像清理、S3 字节清理和审计留痕。绕过它直接 DELETE
会留下孤儿字节，且周期对账会把镜像写回来。

前提：后端服务已启动（建议 `WORKER_ENABLED=false` 启动，避免恢复历史任务）。

用法（工作目录 api/；`--keep 0507` 表示保留名称以 0507 开头的原片）：
    .venv/Scripts/python.exe scripts/purge_source_templates.py --inventory --keep 0507
    .venv/Scripts/python.exe scripts/purge_source_templates.py --backup --keep 0507
    .venv/Scripts/python.exe scripts/purge_source_templates.py --execute \\
        --keep 0507 --backup-dir var/backup/<目录>
    .venv/Scripts/python.exe scripts/purge_source_templates.py --verify \\
        --backup-dir var/backup/<目录>

默认（不带参数）是 --inventory 只读盘点，不会修改任何数据。
备份与执行会强制校验同一套 --keep 规则，且规则必须至少命中一条，防止拼错导致全删。
管理员口令从环境变量 PURGE_ADMIN_USER / PURGE_ADMIN_PASSWORD 读取。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
VAR = ROOT / "var"
CATEGORY = "source"
MIRROR_PREFIX = "catalog/assets"


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
def load_env() -> dict[str, str]:
    text = (ROOT / ".env").read_text(encoding="utf-8")
    return dict(re.findall(r"^([A-Z_0-9]+)=(.*)$", text, re.M))


def pg_dsn(env: dict[str, str]) -> str:
    return env["DB_URL"].replace("postgresql+asyncpg://", "postgresql://")


def s3(env: dict[str, str]):
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=env["S3_ENDPOINT"],
        aws_access_key_id=env["S3_ACCESS_KEY"],
        aws_secret_access_key=env["S3_SECRET_KEY"],
        region_name=env.get("S3_REGION", "us-east-1"),
    )


def archive_bucket(env: dict[str, str]) -> str:
    return env["S3_ARCHIVE_BUCKET"]


# --------------------------------------------------------------------------- #
# 数据盘点
# --------------------------------------------------------------------------- #
async def fetch_targets(env: dict[str, str], keep: list[str] | None = None) -> list[dict]:
    """待删除清单 = 全部未删除的 source 原片 − 命中 keep 规则的行。

    keep 规则可以是 asset id，也可以是名称前缀（例如 `--keep 0507`）。
    """
    import asyncpg

    keep = [k.strip() for k in (keep or []) if k.strip()]
    conn = await asyncpg.connect(pg_dsn(env), timeout=15)
    try:
        rows = await conn.fetch(
            "select * from assets where category = $1 and deleted_at is null order by name",
            CATEGORY,
        )
        all_rows = [json.loads(json.dumps(dict(r), default=str)) for r in rows]
        targets = [
            row for row in all_rows
            if not any(row["id"] == k or row["name"].startswith(k) for k in keep)
        ]
        for row in targets:
            row["run_inputs"] = [
                dict(r)
                for r in await conn.fetch(
                    """select run_id, asset_id, role, position, usage, storage_key
                       from replacement_run_inputs where asset_id = $1 order by run_id""",
                    row["id"],
                )
            ]
        return targets
    finally:
        await conn.close()


async def kept_rows(env: dict[str, str], keep: list[str]) -> list[dict]:
    """按 keep 规则找出被保留的原片；规则写错（命中 0 条）时直接拒绝，防止误删全部。"""
    import asyncpg

    rules = [k.strip() for k in keep if k.strip()]
    if not rules:
        return []
    conn = await asyncpg.connect(pg_dsn(env), timeout=15)
    try:
        rows = await conn.fetch(
            "select id, name from assets where category = $1 and deleted_at is null", CATEGORY
        )
    finally:
        await conn.close()
    found: dict[str, dict] = {}
    for rule in rules:
        hits = [dict(r) for r in rows if r["id"] == rule or r["name"].startswith(rule)]
        if not hits:
            raise SystemExit(f"拒绝执行：保留规则 {rule!r} 没有匹配到任何原片，请检查拼写")
        for hit in hits:
            found[hit["id"]] = hit
    return list(found.values())


def object_keys(row: dict) -> list[str]:
    """与 archive_purge.candidate_keys 同口径：原件 + 缩略图 + 转码流 + 镜像。"""
    keys = [row.get("storage_key"), row.get("cover_key")]
    cover = row.get("cover_key") or ""
    if cover.lower().endswith(".jpg"):
        keys.append(cover[:-4] + ".mp4")  # clip_key：与原图同版本号的派生流
    keys.append(f"{MIRROR_PREFIX}/{row['id']}.json")
    return list(dict.fromkeys(k for k in keys if k))


def probe_objects(client, bucket: str, keys: list[str]) -> dict[str, int | None]:
    """None = 对象不存在；int = 对象字节数。"""
    found: dict[str, int | None] = {}
    for key in keys:
        try:
            found[key] = client.head_object(Bucket=bucket, Key=key)["ContentLength"]
        except Exception:  # noqa: BLE001 - 404/NoSuchKey 都只是「没有这个派生对象」
            found[key] = None
    return found


# --------------------------------------------------------------------------- #
# 动作
# --------------------------------------------------------------------------- #
def cmd_inventory(env: dict[str, str], keep: list[str]) -> None:
    kept = asyncio.run(kept_rows(env, keep))
    targets = asyncio.run(fetch_targets(env, keep))
    client = s3(env)
    bucket = archive_bucket(env)
    print(f"保留 {len(kept)} 条：")
    for row in kept:
        print(f"  ✓ {row['name']} (id={row['id']})")
    print(f"\n待删除模板（category={CATEGORY}、未删除）：{len(targets)} 条")
    print(f"归档桶：{bucket}\n")
    total = 0
    present = 0
    refs = 0
    for i, row in enumerate(targets, 1):
        found = probe_objects(client, bucket, object_keys(row))
        alive = {k: v for k, v in found.items() if v is not None}
        total += sum(v or 0 for v in alive.values())
        present += len(alive)
        refs += len(row["run_inputs"])
        print(f"{i:2}. {row['name']}")
        print(f"    id={row['id']}  owner={row['owner_id']}  stateVersion={row['state_version']}")
        print(f"    S3 对象 {len(alive)}/{len(found)} 个，共 {sum(alive.values()):,} 字节")
        for key, size in alive.items():
            print(f"      - {key} ({size:,} B)")
        if row["run_inputs"]:
            print(f"    ⚠ 被 {len(row['run_inputs'])} 条历史替换任务档案引用")
    print(f"\n合计：S3 对象 {present} 个 / {total:,} 字节（{total / 1024 / 1024:.1f} MiB）")
    print(f"历史替换任务引用：{refs} 条（删除后这些档案仍在，但原片回显会失效）")
    print("\n这是只读盘点，未做任何修改。")


def cmd_backup(env: dict[str, str], explicit: str | None, keep: list[str]) -> Path:
    kept = asyncio.run(kept_rows(env, keep))
    targets = asyncio.run(fetch_targets(env, keep))
    if not targets:
        raise SystemExit("没有待删除的模板，无需备份")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = Path(explicit) if explicit else VAR / "backup" / f"purge-source-templates-{stamp}"
    out = out.resolve()
    (out / "objects").mkdir(parents=True, exist_ok=True)

    client = s3(env)
    bucket = archive_bucket(env)
    manifest: dict = {
        "createdAt": datetime.now(UTC).isoformat(),
        "category": CATEGORY,
        "bucket": bucket,
        "endpoint": env["S3_ENDPOINT"],
        "keep": sorted(k.strip() for k in keep if k.strip()),
        "kept": kept,
        "count": len(targets),
        "assets": targets,
        "objects": [],
    }

    for row in targets:
        for key in object_keys(row):
            size = probe_objects(client, bucket, [key])[key]
            if size is None:
                continue
            dest = out / "objects" / key
            dest.parent.mkdir(parents=True, exist_ok=True)
            client.download_file(bucket, key, str(dest))
            digest = hashlib.sha256(dest.read_bytes()).hexdigest()
            if dest.stat().st_size != size:
                raise RuntimeError(f"备份字节数不符：{key}")
            manifest["objects"].append(
                {"assetId": row["id"], "key": key, "size": size, "sha256": digest}
            )
            print(f"  ✓ {key} ({size:,} B)")

    (out / "assets.json").write_text(
        json.dumps(targets, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n备份完成：{out}")
    print(f"  {len(targets)} 条资产行 + {len(manifest['objects'])} 个对象"
          f"（{sum(o['size'] for o in manifest['objects']) / 1024 / 1024:.1f} MiB）")
    return out


def cmd_execute(
    env: dict[str, str], backup_dir: str | None, base_url: str, keep: list[str]
) -> None:
    import httpx

    if not backup_dir:
        raise SystemExit("拒绝执行：请先用 --backup 生成备份，再用 --backup-dir 指定它")
    backup = Path(backup_dir).resolve()
    manifest_path = backup / "manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"拒绝执行：备份清单不存在 {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    backed_up = {a["id"] for a in manifest["assets"]}

    # 备份与执行必须用同一套保留规则：备份时留了 0507，执行时漏传 --keep 就会多删一条。
    rules = sorted(k.strip() for k in keep if k.strip())
    if sorted(manifest.get("keep") or []) != rules:
        raise SystemExit(
            f"拒绝执行：保留规则与备份不一致（备份={manifest.get('keep')}，本次={rules}）。"
            "请用与 --backup 相同的 --keep 参数，或重新备份"
        )
    kept_now = asyncio.run(kept_rows(env, keep))
    kept_ids = {r["id"] for r in kept_now}
    if sorted(kept_ids) != sorted(r.get("id") for r in manifest.get("kept") or []):
        raise SystemExit("拒绝执行：当前保留的原片与备份时不一致，请重新盘点并备份")

    targets = asyncio.run(fetch_targets(env, keep))
    current = {row["id"] for row in targets}
    if not current:
        raise SystemExit("当前已无待删除模板，无需执行")
    if current & kept_ids:
        raise SystemExit("拒绝执行：保留规则失效，待删清单里出现了要保留的原片")
    if current - backed_up:
        raise SystemExit(f"拒绝执行：以下 id 未备份，先重做备份 → {sorted(current - backed_up)}")
    if manifest.get("count") != len(backed_up):
        raise SystemExit("拒绝执行：备份清单 count 与资产条数不一致")

    username = os.environ.get("PURGE_ADMIN_USER", "admin")
    password = os.environ.get("PURGE_ADMIN_PASSWORD")
    if not password:
        raise SystemExit("请设置环境变量 PURGE_ADMIN_PASSWORD（资产 owner 对应的账号口令）")

    with httpx.Client(base_url=base_url, timeout=60.0) as http:
        login = http.post("/identity/login", json={"username": username, "password": password})
        login.raise_for_status()
        payload = login.json().get("data", login.json())
        token = payload.get("accessToken") or payload.get("access_token")
        if not token:
            raise SystemExit(f"登录响应里没有令牌：{str(payload)[:300]}")
        headers = {"Authorization": f"Bearer {token}"}
        print(f"登录成功：{username}\n")

        done = 0
        for row in targets:
            aid = row["id"]
            print(f"[{done + 1}/{len(targets)}] {row['name']}")
            # 第一步：移入回收站。乐观锁要求 expectedVersion 与当前 state_version 一致。
            soft = http.put(
                f"/assets/{aid}/deleted",
                json={"expectedVersion": row["state_version"], "deleted": True},
                headers=headers,
            )
            if soft.status_code != 200:
                raise RuntimeError(f"移入回收站失败 {aid}: {soft.status_code} {soft.text[:400]}")
            # 第二步：彻底删除（DB 行 + 镜像 + S3 字节 + 审计）。
            hard = http.delete(f"/assets/{aid}", headers=headers)
            if hard.status_code != 204:
                raise RuntimeError(
                    f"彻底删除失败 {aid}: {hard.status_code} {hard.text[:400]}；"
                    "该条已移入回收站，可先在页面恢复后重试"
                )
            print("    ✓ 已彻底删除")
            done += 1

    print(f"\n完成：{done}/{len(targets)} 条模板已彻底删除，保留 {len(kept_ids)} 条")
    cmd_verify(env, manifest)


def cmd_verify(env: dict[str, str], manifest: dict | None = None) -> None:
    import asyncpg

    kept_ids = [r["id"] for r in (manifest or {}).get("kept") or []]

    async def check():
        conn = await asyncpg.connect(pg_dsn(env), timeout=15)
        try:
            left = await conn.fetchval(
                "select count(*) from assets where category = $1 and deleted_at is null", CATEGORY
            )
            total = await conn.fetchval("select count(*) from assets")
            trash = await conn.fetchval(
                "select count(*) from assets where category = $1 and deleted_at is not null",
                CATEGORY,
            )
            kept_alive = await conn.fetchval(
                "select count(*) from assets"
                " where category = $1 and deleted_at is null and id = any($2::text[])",
                CATEGORY,
                kept_ids,
            )
            return left, total, trash, kept_alive
        finally:
            await conn.close()

    left, total, trash, kept_alive = asyncio.run(check())
    print(f"\n[验证] 未删除的 source 模板：{left} 条（期望 {len(kept_ids)} = 保留数）")
    print(f"[验证] 保留的 {len(kept_ids)} 条仍在：{kept_alive} 条")
    print(f"[验证] assets 表总行数：{total}；回收站里的 source：{trash}")

    alive: list[str] = []
    if manifest:
        client = s3(env)
        bucket = archive_bucket(env)
        alive = [
            obj["key"]
            for obj in manifest["objects"]
            if probe_objects(client, bucket, [obj["key"]])[obj["key"]] is not None
        ]
        print(
            f"[验证] 备份中 {len(manifest['objects'])} 个 S3 对象，"
            f"仍存在 {len(alive)} 个（期望 0）"
        )
        for key in alive[:10]:
            print(f"    ! {key}")
    intact = left == len(kept_ids) and kept_alive == len(kept_ids) and not alive
    if intact:
        count = manifest["count"] if manifest else "目标"
        print(
            f"\n✅ 删除完成：{count} 条模板的数据库行、JSON 镜像、S3 字节全部移除；保留项完好"
        )
    else:
        print("\n⚠ 仍有残留或保留项异常，见上方明细")


def main() -> int:
    parser = argparse.ArgumentParser(description="清空模板库中的 source 原片模板")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--inventory", action="store_true", help="只读盘点（默认）")
    group.add_argument("--backup", action="store_true", help="备份 DB 行与 S3 对象")
    group.add_argument("--execute", action="store_true", help="执行彻底删除")
    group.add_argument("--verify", action="store_true", help="验证删除结果")
    parser.add_argument("--backup-dir", help="备份目录（--backup 可指定输出；--execute 必填）")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080/api/v1", help="后端 API 前缀")
    parser.add_argument(
        "--keep", action="append", default=[], metavar="ID_OR_PREFIX",
        help="保留不删：asset id 或名称前缀，可重复。备份与执行必须使用相同的 --keep",
    )
    args = parser.parse_args()

    env = load_env()
    if args.backup:
        cmd_backup(env, args.backup_dir, args.keep)
    elif args.execute:
        cmd_execute(env, args.backup_dir, args.base_url, args.keep)
    elif args.verify:
        manifest = None
        if args.backup_dir:
            p = Path(args.backup_dir) / "manifest.json"
            if p.is_file():
                manifest = json.loads(p.read_text(encoding="utf-8"))
        cmd_verify(env, manifest)
    else:
        cmd_inventory(env, args.keep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
