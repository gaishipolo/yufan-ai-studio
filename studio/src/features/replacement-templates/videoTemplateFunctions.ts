import type { VideoTemplateOut } from '@/shared/api/schema';
import { TYPES } from './replacementOptions';

export function videoTemplateLabel(item: VideoTemplateOut) {
  const label = item.rules?.workbenchLabel;
  return typeof label === 'string' && label.trim()
    ? label.trim() : TYPES.find(type => type.kind === item.defaultKind)?.label ?? item.name;
}

export function videoTemplateScopes(item: VideoTemplateOut): string[] {
  const options = item.rules?.scopeOptions;
  return Array.isArray(options)
    ? [...new Set(options.filter((option): option is string => typeof option === 'string' && !!option.trim()).map(option => option.trim()))]
    : [];
}

export function videoTemplatesForSource(items: VideoTemplateOut[], asset?: {id: string; name: string}) {
  if (!asset) return [];
  return items.filter(item => item.sourceVideoAssetId === asset.id);
}
