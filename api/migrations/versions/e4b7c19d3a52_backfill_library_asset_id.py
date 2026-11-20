"""把历史上已入库的视频任务回填 `library_asset_id`。

没有这一步，去字幕的「原地替换」只能对新任务生效：老任务的 `library_asset_id`
是空的，派生任务找不到「原片资产」，就会在资产库里多出一条 —— 而产品口径是
**只保留去字幕版**。幂等：只填 NULL，可重复执行。
"""

from alembic import op

revision = "e4b7c19d3a52"
down_revision = "d2a6f4c81b90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE video_tasks AS t
        SET library_asset_id = g.asset_id
        FROM (
            SELECT DISTINCT ON (source_task_id) source_task_id, asset_id
            FROM library_asset_generation
            WHERE source_module = 'video'
            ORDER BY source_task_id, asset_id
        ) AS g
        WHERE t.id = g.source_task_id
          AND t.library_asset_id IS NULL
        """
    )


def downgrade() -> None:
    # 回填是数据修正，撤不回去（也不该撤：撤了会让已替换的资产失去指针）
    pass
