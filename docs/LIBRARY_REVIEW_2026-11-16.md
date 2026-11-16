# 资产库代码审查 · 2026-11-16

> 只做审查,未改动任何代码。范围:前端 `studio/src/features/library`、`studio/src/entities/library`、
> `pages/library`、`modules/library`(45 文件 / 约 7500 行);后端 `api/src/modules/library` 全部
> 20 个文件(约 8700 行) + `api/src/infra/media_probe.py` + `core/ports/library_*.py`。
>
> 每条都回代码核验过调用方与相邻模块的口径,并确认现有测试**没有**覆盖它(相关测试本身全绿)。
> 文件:行号以本文件生成时为准。

---

## 结论摘要

| 维度 | 判断 |
|---|---|
| 代码质量 | 明显高于一般水平。可见性判定集中在 `access.py`、不变式写在模型注释里、端口 fail-closed、24 个测试文件覆盖很密 |
| 问题类型 | ① 状态机与审批流的**跨状态复核缺失**(单步都对,串起来就出错)② 幂等键与上限的**口径分裂**(同一件事两处各写一遍)③ **异常路径的资源清理**(主路径很好,失败分支把字节留在存储里或留在内存里) |
| 风险最高的三处 | 生成入库端点可读取任意存储对象;共享审批通过时不复核资产状态;封装成员上限 100 可被绕过 |

---

# 一、前端(studio)

## P1

### F1. 详情里的改名会被任意一次详情刷新冲掉,输入静默丢失
- 位置:`studio/src/features/library/LibraryDetail.tsx:141-147`
- 触发:点铅笔改名(不点保存)→ 详情被重新拉取就会触发这个 effect。`asset.tags` 每次响应都是新数组引用,依赖里有它 → effect 必重跑。典型时机:**窗口重新聚焦**(`useLibraryDetail` 的 `staleTime` 只有 30s,`refetchOnWindowFocus` 默认开),以及任意一次资产写操作后的失效。
- 后果:`setName(displayName(asset.name))` 把刚输入的名字还原,`setRenaming(false)` 还把编辑态整个关掉 → 用户以为还在编辑,其实输入框已经没了;再点保存存的是旧名。注释写的是「换一条资产时重置」,实现却是「每次刷新都重置」。
- 修复方向:只在 `asset.id` 变化时重置(用 ref 记住上一个 id),别把每次刷新都会换引用的 `asset.tags` 放进依赖。

### F2. 上传成功后「打标签」失败,被整条误报成「上传失败」
- 位置:`studio/src/features/library/LibraryUploadModal.tsx:98-120`
- 触发:勾标签后上传,文件 POST 已成功,随后 `patch.mutateAsync({id, body:{tagIds}})` 失败(标签在上传期间被删、403、网络抖动)。
- 后果:文件其实已经在库里,但被同一个 `catch` 标成 `status:'failed'`,toast 说「N 个文件没上传成功」→ 用户重传 → **库里出现重复素材**;标签还实际没打上。
- 修复方向:上传与打标签分成两段处理,PATCH 失败只提示「标签未打上」,不要改上传状态。

### F3. 新建封装:预选成员无法移除,超过 100 条预选时提交必然 422 且无退路
- 位置:`studio/src/features/library/LibraryPackModal.tsx:40、76-84、173-207`
- 触发:在列表里跨页勾选素材 → 点「封装」。候选只请求 `pageSize:60` 一页;成员行 `<li>` 里只有「用途」下拉,**没有移除按钮**。
- 后果:不在当前候选页/搜索结果里的预选成员永远取消不掉(那一行只写「已选素材(不在当前列表里)」),只能被一起封进去;批量预选超过 100 条时,后端 `PackIn.items(max_length=100)` 拒为 422,而 UI 最多只能取消当前页的 60 个 → 死局。
- 修复方向:成员行加「移除」并同步从 `selected` 删除;把 100 上限的校验提到提交前。

### F4. 批量「移入回收站」既没有确认,对共享素材又必然失败
- 位置:`studio/src/features/library/LibraryWorkspace.tsx:467-492`(runBulk)、`studio/src/features/library/LibraryBulkBar.tsx:96-106`
- 触发:选中若干素材 → 点批量条「移入回收站」。
- 后果:两件事叠加。① **没有任何确认**——单条删除一律走 `LibraryConfirmModal`,容器自己的注释还写着「删除/驳回/强删统一走 `LibraryConfirmModal`」,批量删除这条却直接发请求。② **删除矩阵对不上**:卡片层刻意在小组/团队不给删除按钮(只给「取消共享」),批量条却照给;后端 `LifecycleService.delete` 对 `visibility=group` 抛 `SharedAssetNotDeletable`、对 `team` 抛 `TeamDeleteForbidden` → 选中共享素材时这一键必然报错或整批失败。
- 修复方向:批量删除也走 `LibraryConfirmModal`(理由字段复用同一套),并按入口收敛动作(小组/团队不给删除)。

## P2

### F5. 筛选/翻页后选中项不裁剪,批量动作会作用在看不见的条目上
`LibraryWorkspace.tsx:531-532、586`:`onKind` / `onKeyword` / 翻页都只 `update(...)`,不清 `selected`;只有 `changeView`(166-168)会清。批量条只显示「已选 N 项」,不列是哪几条 → 用户改完筛选后点批量删除,删掉的是已经不在当前视图里的素材;其中还可能有已被删掉的 id(打包下载会返回「N 项被跳过」)。
修复方向:列表数据变化时用当前结果的 id 集合裁剪 `selected`。

### F6. 批量条「清空」后,打标签面板与已选标签残留
`LibraryBulkBar.tsx:27-31、83`:`tagging`/`tagIds` 是组件级 state,`if (count <= 0) return null` 只是隐藏,组件没卸载。→ 点「清空」后再勾一批,打标签面板仍是展开的,里面还留着上一次的标签,点「应用到 N 项」就把旧标签打到新素材上。

### F7. 复制全文在正文没加载出来时复制到空串,却提示「已复制全文」
`LibraryDetail.tsx:216-222、236-243`:按钮没有 `disabled`,`text.data?.content ?? ''` 在 `isPending`/`isError` 时是空串,粘贴出来是空的。

### F8. 封装成员用途连续修改,后一次会用旧快照回滚前一次
`LibraryDetail.tsx:191-206`:`savePackRole` 每次都从 `asset.packItems`(上一次的服务端快照)重建整份 `items`,而服务端 `PATCH /library/packs/{id}` 是整体替换 → 详情还没刷新就改第二个成员,第一个成员的用途被静默改回。

### F9. 同一份封装详情有两套缓存键,封装写操作不失效其中一套
`entities/library/ui/PackChip.tsx:13-17` 与 `PromptRefs.tsx:259-267、357-365` 都用裸键 `['library','pack',id]`;而 `library.queries.ts` 的 `useInvalidateLibrary` 只失效 `list / detail / facets / tags` → 改了成员用途或删了封装之后,模板编辑页的封装 chip 仍显示旧的「N 张」和旧封面,直到 30s `staleTime` 到期。

### F10. 视频取流失败时永远显示「正在读取视频…」
`entities/library/ui/AssetPreview.tsx:44-61`:取流期间和取流失败都落到同一个 `!media.url` 分支(同文件族的 `PromptRefs.tsx:327-333` 用 `media.isPending` 区分了,这里漏了)→ 用户点了播放,画面永远停在「正在读取」。

### F11. 撤销共享的提示与后端「自审即通过」不一致
`LibraryShareModal.tsx:110、178`:toast 固定写「撤销申请已提交,审批通过后才会生效」,正文也写「撤销共享一律需要审批」。但后端 `sharing.py:_request_revoke` 在审核人就是自己时直接 `status="approved"` 立即生效 → 用户以为还没生效,实际已经撤了,还可能重复操作。

### F12. 版本恢复在「当前版本」还没读到时跳过乐观锁
`TextVersionHistory.tsx:51`:`expectedVersion: current.data?.version ?? undefined`。`current` 与版本列表是两条独立请求,正文读取更慢 → 此时点「恢复到此版」不带版本号,后端 `_check_version` 对缺省直接放行 → 别人刚改过的正文被静默覆盖。

### F13. 文本引用超过 8 份被静默截断
`TextRefPicker.tsx:40`:`next.slice(0, max)`,多出的直接扔掉,toast 只提示被丢弃的 `{{Text N}}` 占位符,不提示被丢弃的素材。

### F14. 新建文本资产没有 1MB 前端校验
`TextAssetModal.tsx:203`:底部写着「UTF-8 纯文本,最多 1 MB」,但没有任何校验 → 超过 1MB 要发到后端才被拒,错误框里是 pydantic 的英文。而上传弹窗对同一个 1MB 是前端先拦的(`LibraryUploadModal.tsx:74-76`)。

### F15. 上传成功后的自动关闭定时器没清理
`LibraryUploadModal.tsx:129`:`setTimeout(() => { reset(); onClose(); }, 600)` 没存也没清。600ms 内点「关闭」再重新打开并选好文件,旧定时器照样触发 → 刚选的文件无声消失、弹窗自己关掉。

### F16. 每张案例封面各发一次资产详情请求
`entities/library/ui/AssetPreview.tsx:31` 每个 `assetId` 一次 `GET /library/assets/{id}`,`CaseCover` 每个案例一张卡 → 模板库/封装列表一页 12–24 个并发详情请求,响应还带 packItems、generation、共享信息。根因是模板出参只给了 `demoAssetId`,没有 kind/coverUrl → 修复要么让模板契约带上封面信息,要么加一个轻量封面接口。

### F17. 上传类型判定与后端扩展名白名单不一致
`LibraryUploadModal.tsx:61-79` 与 `accept="image/*,audio/*"`:后端 `EXTENSIONS` 只认 `.mp4/.png/.jpg/.jpeg/.webp/.mp3/.wav/.txt/.md`(`.svg` 被显式排除),前端按 `file.type` 判 → `.gif`/`.svg`/`.heic` 能选中却在服务端被拒(`LibraryFileTypeNotAllowed`);反过来浏览器给出空 `file.type` 的合法 `.jpg` 会被前端先拒。另外前端上限 200MB、后端 `MAX_BYTES` 300MB,两处数字也不一样。

### F18. 提示词里的大图弹层:详情缺失时渲染无 `src` 的破图
`PromptRefs.tsx:347-353`:`detail.data` 为 undefined 时 `src` 是 undefined → `<img>` 没有 src,弹层里只剩浏览器默认破图,没有任何「素材不可用」的提示。

## 前端:守卫已经不存在,导出和注释还在

| 位置 | 问题 |
|---|---|
| `features/library/textRefs.ts:5-9` | `hasForeignMustache` 只被自己的测试引用,注释却写着「替换工作台用它拒绝用户手写别的占位符」——那条守卫根本没接上 |
| `entities/library/model/packItemRoles.ts:31-32` + `.test.ts:16-21` | `everyPackRoleKey()` 从 `PACK_ITEM_ROLE_LABELS` 自己派生,两条用例是自证式断言,永远不会红 |
| `entities/library/model/types.ts:216-225` | `SOURCE_VIDEO_ORIGINS` 全 studio 只有定义处一处命中(后端 `schema.py` 已有同义常量) |
| `entities/library/model/types.ts:107-121` | `KIND_OPTIONS` 上面压着两段互相矛盾的注释:旧注释说「刻意不含封装」,新注释说「封装也是这一档里的一项」 |
| `features/library/LibraryGrid.tsx:19、44` | `onToggleAll` 声明并传入但组件内部从不使用(全选在同文件的 `LibrarySelectAll` 里) |
| `entities/library/ui/PromptRefs.tsx:378-383` | 封装成员大图的说明显示原始 role key(`multiview`),而详情/封装弹窗显示的是「多视图」——同一真相两种说法 |

---

# 二、后端(api)

## P1

### B1. 生成入库端点是一个「任意对象读取」原语
- 位置:`api/src/modules/library/ingest.py:245-281`、`router.py:292-310`、`schema.py` 的 `FromGenerationIn`
- 触发:`POST /library/assets/from-generation` 的 body 里 `storageKey` / `archiveKey` 由调用方给,服务端**不校验这个 key 属于谁**,直接 `storage.get()` 取字节(`local_path()` 命中时连存储往返都省了),再建一条 `owner_id=actor.id, state=draft` 的资产行。
- 后果:调用者随后 `GET /library/assets/{新id}/file` 拿到这份字节 —— 等于把对象存储里的任意对象读成自己名下的素材。主存储里装的是**所有人**的私有原片;blob key 由内容 sha256 派生(`library/blobs/ab/<sha256>.png`),sha256 只在 `_archive` 出参里给本人/管理员,但 key 空间本身没有限制前缀,任何别的模块的 key 只要能拼出来同样能取。`archiveKey` 走归档盘的 `materialize()`,同样无归属校验。
- 缓解:该端点要 `library:generation:ingest`,内置角色模板不授这个码(可授给自定义角色)。
- 修复方向:入库只接受调用方自有命名空间的前缀,或把 `storageKey`/`archiveKey` 从 HTTP payload 拿掉、只留端口内部调用;至少在物化前按 key 反查资产行校验 owner。

### B2. 共享审批通过时不复核资产的状态与可见性
- 位置:`api/src/modules/library/sharing.py:218-270`(`decide`)、`442-461`(`_apply_share`/`_apply_revoke`);`lifecycle.py:562-598`(`_to_trash` 不处理 pending 申请单,只有 `_purge_row` 会把它们置 cancelled)
- 触发:两条路径都可达。① 素材发起「共享到团队」申请(pending)→ 审批前被移入回收站 → 审批人点通过 → `decide` 用 `load_asset` 取行、**不判 state**(前端 `ShareRequestsModal.tsx` 固定传 `expectedVersion: 0`,版本闸门也不生效)→ `_apply_share` 把 `visibility` 改成 `team`,组目标还会插一行组行 → 回收站里出现 `state=trash ∧ visibility=team` 的行,破坏不变式 A/B(模型注释里写明这两条不变式由测试钉住,实际是服务层约定、没有 DB 约束,所以不会报错,只会静默写坏)。② 素材共享到团队后(团队共享会清空组行),一条还挂着的组级 pending 单被通过 → `_apply_share(GROUP)` 无条件写 `visibility="group"` 并补回组行 → 素材从团队库整体消失,**且这次降级没有任何撤销申请单**;走 revoke 分支更糟:`_apply_revoke(GROUP)` 删不到组行 → `remaining=None` → `visibility="personal"`,团队共享被无声抹掉。
- 修复方向:`decide()` 按申请目标重跑一次目标有效性校验(组目标:组行仍在且 `visibility != "team"`;团队目标:`visibility != "team"`;revoke:组行仍在),并要求 `asset.state == "active"`;不满足返回 409 让审批人重看。顺带把申请单上写了却从没用过的 `expected_version` 用起来。

### B3. 封装成员上限 100 被两个独立上限绕过,合并后最多 200
- 位置:`api/src/modules/library/schema.py`(`PackIn`:`asset_ids` 与 `items` 各自 `max_length=100`)、`packs.py`(`_ordered_items` 合并两组、`_validate` 只逐条校验不查总数)
- 触发:`POST /library/packs` body `{"name":"x","items":[100 项],"assetIds":[100 个互不重复的 id]}` → 两个字段各自 ≤100 都过 → `_ordered_items` 把两组拼成 200 个成员 → 201 落库。
- 后果:突破设计的 100 上限;更直接的后果是这个封装**永远下不下来** —— 下载闸门是「展开后的条目数」(`library_download_max_items=100`),200 个成员必然 413,封装成了拿不到内容的空壳。
- 修复方向:在 `_ordered_items` 之后对合并后的总数做一次 ≤100 校验,超限 400。

## P2

### B4. `access.py` 的「本人」口径在 SQL 分支与 `can_read` 之间漂移
`access.py:94-98`(`active_visible_clause` 的 owner 分支要求 `visibility == "personal"`)与 `114-118`(`tab_clause` 的 PERSONAL 分支)对得上,但 `254-256`(`can_read` 的 owner 分支)**无条件放行**,注释还写着「共享出去之后仍是『我的』,被移出小组也不该看不见自己的东西」。触发:A 把素材共享到小组 G 后被移出 G → 列表四个入口全都看不见它(`personal` 要求 visibility=personal 不成立、`group` 要求 A∈G 不成立),角标也不计数,但直链能打开、读判定放行 → 用户自己的素材在界面上彻底消失,也没法自救(撤销共享不再是自审)。这个文件的头注释写着「同一条规则不能写第二遍…SQL 分支与 Python 分支都从这里取」,这里正是漂掉的那一支。

### B5. 入库幂等键没按人隔离,跨用户撞键会静默吞掉入库
`ingest.py:157-161` 的 `_by_source_key` 不带 owner 过滤,落在 `uq_library_assets_source_key` 这个**全局**唯一索引上。触发:B 用 `sourceKey="upload:A:a1"` 调 from-generation → 命中 A 的行 → 返回 200 `{assetId: A 的资产 id, created:false}`,B 这次的字节**从未入库**。上传侧早就踩过这个坑并修过(`service.py` 给键加 `upload:<actorId>:` 前缀,注释写明了原因),入库侧没跟上。

### B6. 封装上「用户手选标签」绕过了「只有叶子能打标」
`tags.py:157-199`(`sync_pack_tags` 里 `set_asset_tags(..., reject_groups=False)`)把**用户手选的 base** 与**继承来的成员标签**混在一起免检 → `POST /library/packs {"tagIds":[一级标签 id]}` 会把一级分组标签绑到封装上;同一个标签打到普通素材上则被 400 拦下。两条路径口径不一致。

### B7. 文本并发编辑的乐观锁退化成通用 409,后写的内容静默丢失
`text_assets.py:210-247`:`update` 是「读 → 判 → 写」,两个会话同时打开同一资产(都读到 `textVersion=3`)几乎同时保存 → 两边 `_check_version` 都放行 → 都算出 `number = 4` → 都插 `(asset_id, version=4)` → 后一个撞 `uq_library_text_versions_number` → 落到全局 IntegrityError 处理器,返回「数据冲突,请检查唯一性或关联引用」,而不是接口自己约定的「正文已被修改,请刷新后重试」(也不带 `details.textVersion`)→ 后写的那份正文直接没了。同文件里「内容与库里另一条重复」的情况是被显式处理并给了指路的,唯独并发这一条没有。

### B8. 下载 GC 先清空 `storage_key` 再删字节,删失败被吞 → zip 永久泄漏
`downloads.py` 的 expired 分支:`row.storage_key = ""` 在 `storage.delete(key)` **之前**,删除异常被 `contextlib.suppress(Exception)` 吞掉 → 事务照常提交 → 下一轮 GC 只选 `status in (succeeded, failed)`,这行已是 expired 不会再被选中,`ancient` 分支只删行不删字节 → 那个最大 2GB 的 zip 永久留在对象存储里,没有任何记录还知道它存在。

### B9. `build_download` 的 queued→running 不是原子状态转移
`downloads.py:445-449`:读行 → 判 `status != QUEUED` → 置 RUNNING → commit。模块自己的注释写明「db 驱动在租约丢失后会重投」,而队列的原子领取只保证「谁领到 job 行」,不保证旧持有者停止写业务表 → 两个 worker 同时读到 queued 会各自压包(同一个 key 互相覆盖、CPU 与磁盘翻倍);若其中一个在另一个提交 succeeded 之后失败,失败分支 `row.status = FAILED` 会把整行覆盖(且把 `storage_key` 清空)→ 用户看到「打包失败」,而 GC 之后按 failed 把那个其实可用的 zip 删掉。

### B10. `create_preview` 不捕获 ffmpeg 超时/异常
`api/src/infra/media_probe.py`:视频分支 `subprocess.run(..., timeout=30)` 没有 try/except(同文件的 `create_browser_preview` 捕获了 `(OSError, subprocess.TimeoutExpired)` 并返回 False),图片分支的 `Image.open` 同样裸奔 → 一条首帧抽取超时的视频会让上传/入库返回 500 而不是「文件损坏,请换一个」;而且 `service._ingest` 是**先写 blob 再生成封面**,于是字节已经进了主存储、库里却没有对应行 → 每次失败都留下一份无主字节(v2 的 blob 前缀也不会被旧 `prune-blobs` 扫到)。

### B11. ingest 的 storageKey 分支把整份对象读进内存
`ingest.py`:`payload = await self.storage.get(...)` → 远端驱动(MinIO/S3)下 300MB 全驻留进程,体积上限的判断发生在整份读进内存**之后**,拦不住峰值;同文件的 `sourceUrl` 分支已经用 `fetch_to_file` 流式落盘,两条路径口径不一致。

### B12. 媒体探测没有像素上限
`media_probe.inspect` 只按字节上限(300MB)放行,PIL 只在超过 2×`MAX_IMAGE_PIXELS`(约 1.79 亿像素)时才抛 `DecompressionBombError` → 一张 12000×9000 的 PNG(约 1 亿像素)会通过告警区间,`thumbnail` 触发完整解码,约 1 亿像素 × 4 字节 ≈ 400MB 一次性进 worker 线程。

### B13. 运维命令默认无 LIMIT,一次把全表读进内存
`maintenance.py`:`if batch: query = query.limit(batch)`,而 CLI 的 `--batch` 默认 0 → `args.batch or None` → 无分页 → `app library repreview` 在十万条资产的库上会把十万个 ORM 实体读进进程。`sync-pack-tags` 同样是这个写法。

### B14. 批量撤销共享传的 `expected_version` 全链路没人消费
`bulk.py` 的 REQUEST_REVOKE 分支把版本传给 `ShareService.request`,但后者只把它原样存进 `library_share_requests.expected_version`;全仓唯一的比较在 `sharing.py:230` 的 `decide` 里,用的是**审批人自己带的那份**(前端固定传 0)→ 乐观锁在共享申请上形同虚设,基于旧页面发出的撤销申请会作用在用户没见过的资产状态上(撤销方向风险最大)。同一批里的 rename / approveDraft / rejectDraft 都做了 `state_version` 校验,只有这一条链是空的。

## 后端:次要

| 位置 | 问题 |
|---|---|
| `lifecycle.py` `_purge_row` | 彻底删除成员时把 `library_pack_items` 行删掉 → 已解散的封装恢复后成员静默丢失、`size_bytes`/`duration` 仍按旧成员累加;`lookup.py` 明明约定了「成员被彻底删掉仍要出现,用 `readable=False` 标出」 |
| `router.py` `/assets/upload` | 端点接受 `kind=video`(UploadKind 里有 VIDEO),而它的 docstring 说「视频走 /source-videos,来源由服务端写死」→ 来源语义又变成前端传参,前端已经特意避开这条路,服务端没强制 |
| `router.py` 的 `LibraryDeleteDep` | 路由层 `require("library:delete")` 与服务层的 `delete:any` 不对称:只有 `library:delete:any` 没有 `library:delete` 的角色会看到按钮却 403(当前只有超管可达,属埋雷) |

---

# 三、查过但不成立的(免得重复排查)

- **「重传一个回收站里的同内容文件会命中秒传、返回那条回收站记录」** —— 不成立:`_to_trash` 会写 `deleted_at`,而 `_find_duplicate` 按 `deleted_at IS NULL` 过滤,回收站里的行不参与秒传。
- **「offsetTop 对 sticky 元素返回自然位置」** —— 前端吸顶面板的问题,不在后端范围内(见前端 F1 与 `useStickyFitHeight`)。
- **「批量审批不传 expected_versions 会导致 409」** —— 不成立:后端 `_check_version` 的约定是 `expected=0` 表示不校验。

---

# 四、建议的修复顺序

1. **P1 三条**(B1/B2/B3 + 前端 F1–F4)—— 都是数据可见性与一致性问题,越晚修越可能已经写坏数据。
2. **口径漂移那组**(B4/B5/B6/F9/F14)—— 都是「同一件事两处各写一份」,改法要按「一处定义、处处引用」,别再写第二份。
3. **异常路径的资源清理**(B8/B10/B11/B12/F15)—— 失败时不要把字节留在存储里,也不要把 300MB 读进内存。
4. **并发与幂等**(B7/B9/B14)—— 需要用条件更新/原子抢号,改动比前面几组大。
5. **死代码与注释对不上**那组 —— 顺手清掉,别再留「曾经有个守卫」的假象。

每一次改动单独一笔提交(本仓库规矩:一次提交只做一件事;推送前先问用户)。
