"""主体的 HTTP 契约。

出参刻意分两个粒度：

- **列表项**只带"够不够用、能不能点进去"（成员数、封面、可用性三态），
  不带成员明细。一页 24 个主体、每个 10 个成员，等于把 240 条素材塞进一次
  列表响应，而其中 236 条不会被看。
- **详情**才带成员条带，且每条都带 `grants` / 缺图标记——那是"我该不该发布"
  这个问题的答案，它必须逐条可读。

`availability` 是三态而不是布尔：`ready` / `broken`（成员被删） / `incomplete`
（缺角色或缺能力）。两态会把"还差什么"压成"不行"，而用户下一步要做的动作
完全不同：一个要补图，一个要改配置。
"""

from __future__ import annotations

from core.schema import CamelModel

__all__ = [
    "SubjectDetail",
    "SubjectList",
    "SubjectMemberOut",
    "SubjectOut",
    "SubjectPublishResult",
]


class SubjectMemberOut(CamelModel):
    asset_id: str
    #: 素材名与封面。没有它们，成员条带只剩一串 id——用户没法确认"缺的是哪张"
    name: str = ""
    cover_url: str = ""
    role: str = ""
    sort_order: int = 0
    #: 这张图允许的能力（`generate` / `download` / `derive`）
    grants: list[str] = []
    in_output: bool = True
    #: 素材已被软删/回收站。**不删行**：历史版本仍要能读出"当时有这张图"，
    #: 只是这个主体进只读态、不能发布也不能被新任务调用。
    missing: bool = False


class SubjectOut(CamelModel):
    id: str
    name: str
    subject_type: str
    status: str
    visibility: str
    team_id: str = ""
    category_id: str = ""
    cover_asset_id: str = ""
    #: 封面地址。卡片要显示堆叠缩略图，只给 id 的话前端还得逐个反查素材
    cover_url: str = ""
    member_count: int = 0
    current_version: int = 0
    #: `ready` / `broken` / `incomplete`，见模块注释
    availability: str = "incomplete"
    #: 给用户看的一句话，直接显示在卡片角标旁。空表示没问题
    availability_note: str = ""


class SubjectDetail(SubjectOut):
    members: list[SubjectMemberOut] = []
    attributes: dict[str, object] = {}
    requires: list[str] = []
    required_roles: list[str] = []
    #: 发布前置的完整判定结果（含逐成员缺口），前端据此高亮"是谁不行"
    missing_capabilities: list[str] = []
    missing_roles: list[str] = []
    member_gaps: dict[str, list[str]] = {}


class SubjectList(CamelModel):
    items: list[SubjectOut]
    total: int = 0
    page: int = 1
    page_size: int = 24


class SubjectPublishResult(CamelModel):
    subject_id: str
    version: int
    published_at: str
