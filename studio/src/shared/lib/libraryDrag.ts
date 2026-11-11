/**
 * 「素材库 → 生成表单」的拖拽契约。
 *
 * 为什么不用 `text/plain`：页面上任何可选中文本（提示词、素材名）都能以
 * `text/plain` 被拖进来，用文本类型兜底会把「拖了一段文字」误判成「拖了一张素材」，
 * 结果是参考图区莫名其妙多出一张空图。自定义 MIME 让接收方只认自己人。
 *
 * 为什么载荷里带名称和缩略图：接收方（生图参考图区）要立刻画出缩略图并显示名字，
 * 而它手里只有 id；不带就得在拖放路径上再查一次接口。缩略图地址只是展示用，
 * 真正提交给后端的是 id，字节由后端从素材库读。
 */
export const LIBRARY_ASSET_DRAG_TYPE = 'application/x-yufan-library-asset';

export interface LibraryAssetDrag {
  id: string;
  name: string;
  /** 缩略图地址（相对 API 前缀）；空串表示没有可用封面，由 UI 画占位 */
  url: string;
}

/** 拖拽开始：写入载荷。`effectAllowed` 由调用方按场景设置（复制，不是移动） */
export function writeLibraryDrag(dataTransfer: DataTransfer, asset: LibraryAssetDrag): void {
  dataTransfer.setData(LIBRARY_ASSET_DRAG_TYPE, JSON.stringify(asset));
}

/**
 * dragover 阶段只能读 `types`、读不到数据（浏览器安全限制），
 * 所以「能不能接」必须靠类型判断，不能靠解析载荷。
 *
 * 用 `Array.from` 而不是 `includes`：Safari 的 `types` 是 `DOMStringList`，没有 `includes`。
 */
export function hasLibraryDrag(types: readonly string[] | DOMStringList | undefined): boolean {
  return types ? Array.from(types).includes(LIBRARY_ASSET_DRAG_TYPE) : false;
}

/** drop 阶段解析载荷；结构不对就返回 null，不让脏数据污染参考图列表 */
export function readLibraryDrag(dataTransfer: DataTransfer): LibraryAssetDrag | null {
  const raw = dataTransfer.getData(LIBRARY_ASSET_DRAG_TYPE);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<LibraryAssetDrag>;
    if (!parsed || typeof parsed.id !== 'string' || !parsed.id) return null;
    return {
      id: parsed.id,
      name: typeof parsed.name === 'string' ? parsed.name : parsed.id,
      url: typeof parsed.url === 'string' ? parsed.url : '',
    };
  } catch {
    return null;
  }
}
