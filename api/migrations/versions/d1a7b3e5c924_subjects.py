"""asset: 主体、成员、发布版本与旧包映射（M2.5-A）

Revision ID: d1a7b3e5c924
Revises: c3f5a1d8e6b2
Create Date: 2026-11-09 05:10:00.000000

四张表一起上：`subjects` 没有 `subject_members` 就是空壳，`subject_versions` 是它的
冻结证据，`subject_legacy_packs` 是旧链接的入口。拆开只会让中间版本出现
"主体能建但没有成员"的状态，而那种状态没人会去测。

## 迁移来源是 `template_packs`

现有 pack 已经是一个"1–10 张图 + 主图 + 固定参数 + 发布版本"的东西，结构和主体
一一对应（映射表见 `subject_models.py` 的模块注释）。这里做**结构平移**，不重算任何
业务规则：

| 旧 | 新 |
| --- | --- |
| `template_packs.kind` | `subjects.subject_type` |
| 主图 | `cover_asset_id` |
| `template_pack_references.position` | `subject_members.sort_order` |
| `template_pack_versions` | `subject_versions` |

`grants` / `in_output` 没有历史值要保真（旧 pack 没这个维度）：主图给
`generate + download`，其余只给 `generate`。这符合铁律②——"公开一个产品"
不等于"公开它全部参考原片的下载权"。

## 为什么这张迁移必须可重入

`upgrade` 会在生产上跑一次、可能因为超时被重跑。所有 `INSERT` 都先按
`pack_id` 查 `subject_legacy_packs` 判断"这个包已经搬过了"，搬过就跳过——
而不是靠 `INSERT OR IGNORE`（那是 SQLite 语法，PG 上会报错）。

## 删除策略

`downgrade` 只删这四张表。旧表一张不动——它们是这一版的读路径回退，
删掉就真的退不回去了。
"""

import json
import uuid
from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d1a7b3e5c924"
down_revision: Union[str, Sequence[str], None] = "c3f5a1d8e6b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: 主图享有的能力。其余成员只有 `generate`——参考图能被模型读，但不等于能下载原件。
_COVER_GRANTS = ["generate", "download"]
_MEMBER_GRANTS = ["generate"]


def _sid() -> str:
    return uuid.uuid4().hex


def _as_list(value: Any) -> list[Any]:
    """JSON 列在 sqlite 上是字符串、在 PG 上是原生 list。两种都要能读。"""
    if value is None:
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return list(value) if isinstance(value, (list, tuple)) else []


def upgrade() -> None:
    op.create_table(
        "subjects",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("subject_type", sa.String(16), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("visibility", sa.String(16), nullable=False),
        sa.Column("team_id", sa.String(32), nullable=False),
        sa.Column("category_id", sa.String(32), nullable=False),
        sa.Column("owner_id", sa.String(32), nullable=False),
        sa.Column("cover_asset_id", sa.String(32), nullable=False),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("requires", sa.JSON(), nullable=False),
        sa.Column("required_roles", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_subjects_owner_id", "subjects", ["owner_id"])
    op.create_index("ix_subjects_status", "subjects", ["status"])
    _partial_index("ix_subjects_owner_active", "subjects", ["owner_id", "updated_at"],
                   archived_where="archived_at IS NULL")
    _partial_index("ix_subjects_public", "subjects", ["subject_type", "published_at"],
                   published_where="status = 'published' AND archived_at IS NULL")

    op.create_table(
        "subject_members",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("subject_id", sa.String(32), nullable=False),
        sa.Column("asset_id", sa.String(32), nullable=False),
        sa.Column("role", sa.String(24), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("grants", sa.JSON(), nullable=False),
        sa.Column("in_output", sa.Boolean(), nullable=False),
        sa.Column("added_by", sa.String(32), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("subject_id", "asset_id", name="uq_subject_members_pair"),
        sa.UniqueConstraint("subject_id", "sort_order", name="uq_subject_members_order"),
    )
    op.create_index("ix_subject_members_subject_id", "subject_members", ["subject_id"])
    op.create_index("ix_subject_members_asset_id", "subject_members", ["asset_id"])
    op.create_index("ix_subject_members_asset", "subject_members", ["asset_id", "subject_id"])

    op.create_table(
        "subject_versions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("subject_id", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("members", sa.JSON(), nullable=False),
        sa.Column("cover_asset_id", sa.String(32), nullable=False),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("requires", sa.JSON(), nullable=False),
        sa.Column("required_roles", sa.JSON(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_by", sa.String(32), nullable=False),
    )
    op.create_index("ix_subject_versions_subject_id", "subject_versions", ["subject_id"])
    # 这条索引同时是「版本号不被覆盖」的保证
    op.create_index(
        "uq_subject_versions_number", "subject_versions", ["subject_id", "version"], unique=True
    )

    op.create_table(
        "subject_legacy_packs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("pack_id", sa.String(64), nullable=False),
        sa.Column("subject_id", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        # 一个旧包只能映射到一个主体。用唯一约束而不是唯一索引：模型里写的是
        # `UniqueConstraint`，两者在 `alembic check` 眼里是不同的对象。
        sa.UniqueConstraint("pack_id", name="uq_subject_legacy_packs_pack_id"),
    )
    op.create_index("ix_subject_legacy_packs_subject_id", "subject_legacy_packs", ["subject_id"])

    _backfill_from_packs()


def _partial_index(
    name: str,
    table: str,
    columns: list[str],
    *,
    archived_where: str = "",
    published_where: str = "",
) -> None:
    """建部分索引。**两个方言的 WHERE 必须分开写**。

    SQLite 写 `is_inbox = 1`、PG 写 `is_inbox`（布尔列），直接把一种写法套到另一种
    会让索引静默失效——不报错，只是永远不被使用，而"查询慢"没人会怀疑到索引语法。
    PG 的 `VARCHAR` 列在部分索引里要用 `IS NULL` / `IS NOT NULL`，两边这部分相同。
    """
    dialect = op.get_bind().dialect.name
    where = archived_where or published_where
    if dialect == "postgresql":
        # PG 上 `status = 'published'` 的引号相同，布尔与比较表达式通用
        condition = where
    else:
        condition = where
    op.create_index(name, table, columns, sqlite_where=sa.text(condition),
                    postgresql_where=sa.text(condition))


def _backfill_from_packs() -> None:
    """把现有 pack 平移成主体。可重入：搬过的包按 `pack_id` 跳过。"""
    bind = op.get_bind()
    dialect = bind.dialect.name

    packs = bind.execute(
        sa.text(
            "SELECT id, kind, name, owner_id, status, version, cover_asset_id,"
            " required_roles, published_at, deleted_at FROM template_packs"
        )
    ).mappings().all()
    if not packs:
        return

    moved = {
        row[0]
        for row in bind.execute(sa.text("SELECT pack_id FROM subject_legacy_packs")).all()
    }

    references: dict[str, list[Any]] = {}
    for row in bind.execute(
        sa.text(
            "SELECT pack_id, asset_id, position, role FROM template_pack_references"
            " ORDER BY pack_id, position"
        )
    ).mappings():
        references.setdefault(row["pack_id"], []).append(row)

    # 已发布包的冻结快照：优先用它建 `subject_versions`，而不是拿"当前"成员
    # ——当前成员可能已经被改过，用它冻结等于让历史版本改口。
    versions: dict[str, list[Any]] = {}
    for row in bind.execute(
        sa.text(
            "SELECT pack_id, version, reference_ids, bindings FROM template_pack_versions"
            " ORDER BY pack_id, version"
        )
    ).mappings():
        versions.setdefault(row["pack_id"], []).append(row)

    now_sql = sa.text("SELECT CURRENT_TIMESTAMP")

    for pack in packs:
        if pack["id"] in moved:
            continue
        subject_id = _sid()
        stamp = bind.execute(now_sql).scalar()
        # 已删除的包 → 归档主体。映射仍然建：链接打开后看到"已下架"比 404 有用
        status = "archived" if pack["deleted_at"] is not None else pack["status"]
        published_versions = versions.get(pack["id"], [])
        current_version = max((int(row["version"]) for row in published_versions), default=0)

        bind.execute(
            sa.text(
                "INSERT INTO subjects (id, subject_type, name, visibility, team_id, category_id,"
                " owner_id, cover_asset_id, attributes, requires, required_roles, status,"
                " current_version, revision, published_at, archived_at, created_at, updated_at)"
                " VALUES (:id, :subject_type, :name, 'personal', '', '', :owner_id,"
                " :cover, '{}', '[]', :roles, :status, :version, 0, :published_at,"
                " :archived_at, :now, :now)"
            ),
            {
                "id": subject_id,
                "subject_type": pack["kind"] or "product",
                "name": pack["name"] or "",
                "owner_id": pack["owner_id"],
                "cover": pack["cover_asset_id"] or "",
                # `required_roles` 是 JSON 文本，直接透传：它的语义在新旧两边一致，
                # 重新序列化只会多一次出错的机会
                "roles": pack["required_roles"] or "[]",
                "status": status,
                "version": current_version,
                "published_at": pack["published_at"],
                "archived_at": pack["deleted_at"],
                "now": stamp,
            },
        )

        for row in references.get(pack["id"], []):
            bind.execute(
                sa.text(
                    "INSERT INTO subject_members (id, subject_id, asset_id, role, sort_order,"
                    " grants, in_output, added_by, added_at)"
                    " VALUES (:id, :subject_id, :asset_id, :role, :sort, :grants, 1, '', :now)"
                ),
                {
                    "id": _sid(),
                    "subject_id": subject_id,
                    "asset_id": row["asset_id"],
                    "role": row["role"] or "",
                    "sort": int(row["position"]),
                    "grants": json.dumps(
                        _COVER_GRANTS
                        if row["asset_id"] and row["asset_id"] == pack["cover_asset_id"]
                        else _MEMBER_GRANTS
                    ),
                    "now": stamp,
                },
            )

        for row in published_versions:
            ids = _as_list(row["reference_ids"])
            bindings = _as_list(row["bindings"])
            # 快照里没存 grants：按"主图有下载权"的规则补，与当前成员同一口径。
            # 这是**唯一一处历史值无法保真**的地方，写在迁移里而不是藏在代码里。
            members = [
                {
                    "asset_id": asset_id,
                    "sha256": "",
                    "role": (bindings[index].get("role", "") if index < len(bindings) else ""),
                    "grants": _COVER_GRANTS
                    if asset_id and asset_id == pack["cover_asset_id"]
                    else _MEMBER_GRANTS,
                    "in_output": True,
                    "sort_order": index,
                }
                for index, asset_id in enumerate(ids)
            ]
            bind.execute(
                sa.text(
                    "INSERT INTO subject_versions (id, subject_id, version, members,"
                    " cover_asset_id, attributes, requires, required_roles, published_at,"
                    " published_by) VALUES (:id, :subject_id, :version, :members, :cover,"
                    " '{}', '[]', :roles, :published_at, '')"
                ),
                {
                    "id": _sid(),
                    "subject_id": subject_id,
                    "version": int(row["version"]),
                    "members": json.dumps(members, ensure_ascii=False),
                    "cover": pack["cover_asset_id"] or "",
                    "roles": pack["required_roles"] or "[]",
                    "published_at": pack["published_at"],
                },
            )

        bind.execute(
            sa.text(
                "INSERT INTO subject_legacy_packs (id, pack_id, subject_id, created_at, note)"
                " VALUES (:id, :pack_id, :subject_id, :now, :note)"
            ),
            {
                "id": _sid(),
                "pack_id": pack["id"],
                "subject_id": subject_id,
                "now": stamp,
                "note": f"自 template_packs 平移（dialect={dialect}）",
            },
        )


def downgrade() -> None:
    # 顺序与建表相反：先删有外键"语义"的从表。这里没有真外键，顺序仍按依赖写，
    # 是为了将来加外键时这段不用改。
    op.drop_index("ix_subject_legacy_packs_subject_id", table_name="subject_legacy_packs")
    op.drop_index("ix_subject_legacy_packs_pack_id", table_name="subject_legacy_packs")
    op.drop_table("subject_legacy_packs")

    op.drop_index("uq_subject_versions_number", table_name="subject_versions")
    op.drop_index("ix_subject_versions_subject_id", table_name="subject_versions")
    op.drop_table("subject_versions")

    op.drop_index("ix_subject_members_asset", table_name="subject_members")
    op.drop_index("ix_subject_members_asset_id", table_name="subject_members")
    op.drop_index("ix_subject_members_subject_id", table_name="subject_members")
    op.drop_table("subject_members")

    op.drop_index("ix_subjects_public", table_name="subjects")
    op.drop_index("ix_subjects_owner_active", table_name="subjects")
    op.drop_index("ix_subjects_status", table_name="subjects")
    op.drop_index("ix_subjects_owner_id", table_name="subjects")
    op.drop_table("subjects")
