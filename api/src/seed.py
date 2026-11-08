"""演示数据装载（仅开发环境使用）：`app seed [--reset]`。

这个文件只做**编排**，不认识任何业务表：

    init_db → （可选）清表 → 按 `seed_order` 逐个调 `ModuleSpec.seed`

演示数据由各模块自己写（`modules/<name>/seed.py`）。这么分不是为了好看：
以前它们全堆在这个文件里，结果是 `conftest.py → seed.py → modules.image.models`，
**删掉任何一个业务模块，整套测试连收集都过不了**。想加一个新模块的演示数据，
也必须回来改框架文件——正好和「加模块不动框架」的目标相反。
"""

from __future__ import annotations

from sqlalchemy import delete

from api.discovery import active_modules
from api.module import SeedContext
from core.config import get_settings
from db.base import Base
from db.session import get_session_factory, init_db

# `--reset` 不清的表：框架记账（`app_meta` 里是权限目录快照、目录版本这类
# 「bootstrap 写过、本轮不会再写」的值）。清掉它的后果见 `reset` 分支的注释。
# 历史表必须跟着一起留：只留主表会得到「有一条 v1、却没有任何写入轨迹」的
# 不一致状态，`app meta <key>` 会打不出「最近写入」——这个坑是 test_cli 抓到的。
FRAMEWORK_TABLES = frozenset({"app_meta", "app_meta_history"})


async def seed(*, reset: bool = False) -> dict[str, int]:
    await init_db()

    settings = get_settings()
    context = SeedContext(
        demo_user_id=settings.demo_user_id,
        demo_user_name=settings.demo_user_name,
    )

    factory = get_session_factory()
    async with factory() as session:
        if reset:
            # 清表按 `Base.metadata` 反序，**不手写清单**：手写的那一版漏一个模块的
            # 结果不是报错，而是 `--reset` 之后旧行还在，演示数据和历史数据混在一起，
            # 测试之间互相污染，而且现象要等到下一次「明明 seed 过却查出两条」才出现。
            # 反向遍历是因为 sorted_tables 是拓扑序（父表在前），删数据要从子表开始。
            #
            # 框架记账表除外（`app_meta` 存权限目录快照等）。原因不是「舍不得删」，
            # 而是**时序**：`bootstrap` 在启动时就写过它，`seed --reset` 在之后执行，
            # 抹掉之后本轮没有任何代码会重写——`sync_new_permissions` 于是退化成
            # 「没有快照 → 全量补齐」，管理员手工去掉的旧权限会被悄悄加回来。
            # 这个坑是靠 `test_startup_writes_catalog_snapshot` 抓到的：换清单时它红了。
            for table in reversed(Base.metadata.sorted_tables):
                if table.name in FRAMEWORK_TABLES:
                    continue
                await session.execute(delete(table))
            await session.commit()

        # 顺序由模块自己声明（`seed_order`，小的先装）。identity 必须靠前：
        # 其它模块的演示行都指向 `context.demo_user_id`，用户还没种出来时
        # 那些行虽然插得进去（这些表之间没有外键），但列表页会显示一个不存在的人。
        counts: dict[str, int] = {}
        for spec in sorted(active_modules(), key=lambda s: (s.seed_order, s.name)):
            if spec.seed is None:
                continue
            counts[spec.name] = await spec.seed(session, context)
        return counts
