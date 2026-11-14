"""默认只读预览；--apply --snapshot 保存迁移前后配置，--rollback 校验后恢复 editor。"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from modules.identity.asset_roles import affected_users, apply, preview

from db.session import get_session_factory


async def run() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--apply", action="store_true")
    action.add_argument("--rollback", action="store_true")
    parser.add_argument("--snapshot", type=Path)
    args = parser.parse_args()
    if (args.apply or args.rollback) and args.snapshot is None:
        parser.error("变更必须指定 --snapshot 路径")
    async with get_session_factory()() as session:
        if args.rollback:
            report = json.loads(args.snapshot.read_text(encoding="utf-8"))
            await apply(session, report, rollback=True)
        else:
            report = await preview(session)
            report["affectedUsers"] = await affected_users(session, report)
            if args.apply and any(c["before"] != c["after"] for c in report["changes"]):
                # 独占创建防止丢失旧备份；角色已匹配目标时保持只读。
                with args.snapshot.open("x", encoding="utf-8") as stream:
                    json.dump(report, stream, ensure_ascii=False, indent=2)
                await apply(session, report)
        print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    asyncio.run(run())
