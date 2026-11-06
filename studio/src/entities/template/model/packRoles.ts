/**
 * 参考图用途词表（前端副本）。
 *
 * 权威定义在 `api/src/core/pack_roles.py`，服务端会用同一份表校验用途、拼提示词；
 * 这里保留一份是为了封装编辑器在目录接口返回前就能渲染选项。两边不一致时
 * `api/tests/test_pack_roles.py` 会直接失败，改一处就必须同步另一处。
 */
export const PACK_ROLES: Record<string, Record<string, string>> = {
  product: {
    front: '产品主图',
    multiview: '多视结构图',
    detail: '产品细节图',
    back: '背面结构图',
    side: '侧面立体图',
  },
  character: {front: '正面身份图', side: '侧脸身份图', fullbody: '全身体态图'},
  scene: {main: '环境主参考', angle: '同空间补充视角', detail: '环境材质细节'},
};

export const BASE_ROLES: Record<string, string> = {product: 'front', character: 'front', scene: 'main'};

export const PACK_SCOPES: Record<string, string> = {
  worn: '人物上身穿着',
  held: '手持展示产品',
  boxed: '盒内可见产品',
  screen: '手机详情页产品图',
};

export function rolesFor(kind: string): Record<string, string> {
  return PACK_ROLES[kind] ?? PACK_ROLES.product;
}

export function baseRole(kind: string): string {
  return BASE_ROLES[kind] ?? 'front';
}

export function roleLabel(kind: string, role: string | undefined): string {
  return (role && rolesFor(kind)[role]) || '未指定用途';
}
