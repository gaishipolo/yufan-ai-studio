// Per-task billing switch for LibTV (www.liblib.tv / api.liblib.tv).
//
// Reverse-engineered from the logged-in canvas page via CDP (2026-11-09):
//   * The "0 积分畅用" queue is selected by `params.infiniteSwitch`:
//       0 = use the zero-power benefit (free serial queue)
//       1 = opt out of it for THIS request (normal credit queue)
//   * Opting out must also name the account's active zero-power benefit tags in
//     `disableBenefitTags`, BOTH at the top level and inside `params`, and the
//     frontend regenerates `requestId` when it builds that request.
//   * Active tags come from
//       GET https://api2.liblib.art/api/www/commerce/activity/benefit?productTypes=3
//     with the same `token` header the Canvas adapter uses. A usable entry has
//     benefitType "powerZero" (or a tag like rateLimit_powerZero_*),
//     benefitStatus missing/1, and an endTime that has not passed.
//   * The account-level "优先使用 0 积分畅用" setting is deliberately NOT
//     touched. Only this request opts out; other tasks keep the free queue.
//
// Read-only quote check on a real archived request (2026-11-09):
//   free  -> {power: 0,   originalPrice: 300}
//   credit-> {power: 240, originalPrice: 300}
// Quoting never creates a task; only /api/task/generation/create does.

import {randomUUID} from 'node:crypto';
import {loginHeaders} from '../../tools/js-reverse-mcp/canvas-transport.mjs';

const BENEFIT_URL = 'https://api2.liblib.art/api/www/commerce/activity/benefit?productTypes=3';

// Mirrors the frontend helper isZeroPowerBenefitTag (chunk 21lnjlkbnjaho.js).
export function isZeroPowerBenefitTag(tag) {
  return typeof tag === 'string' && (tag === 'rateLimit' || tag.startsWith('rateLimit_powerZero'));
}

function benefitIsActive(entry, now) {
  if (!entry || typeof entry !== 'object') return false;
  if (entry.benefitStatus != null && entry.benefitStatus !== 1) return false;
  if (entry.endTime != null) {
    const end = new Date(entry.endTime).getTime();
    if (!Number.isFinite(end) || end <= now) return false;
  }
  if (entry.benefitType === 'powerZero') return true;
  return Array.isArray(entry.tags) && entry.tags.some(isZeroPowerBenefitTag);
}

// Extract the deduplicated set of zero-power tags that are still in force.
export function activeZeroPowerTags(entries, now = Date.now()) {
  const tags = [];
  for (const entry of Array.isArray(entries) ? entries : []) {
    if (!benefitIsActive(entry, now)) continue;
    for (const tag of Array.isArray(entry.tags) ? entry.tags : []) {
      if (isZeroPowerBenefitTag(tag) && !tags.includes(tag)) tags.push(tag);
    }
  }
  return tags;
}

/** Query the account's currently active zero-power benefit tags.
 *  `api` is the CanvasApi instance from the bridge; its headers carry the
 *  browser-derived login token. Fall back to reading the browser again only if
 *  a caller passes something that is not a CanvasApi.
 */
export async function accountCreditTags(api) {
  let headers = api?.headers;
  if (!headers?.token) headers = await loginHeaders(process.env.JS_REVERSE_BROWSER_URL);
  const response = await fetch(BENEFIT_URL, {headers, signal: AbortSignal.timeout(15000)});
  if (!response.ok) throw Error(`Benefit query HTTP ${response.status}`);
  const result = await response.json();
  if (result?.code !== 0) throw Error(`Benefit query code ${result?.code}`);
  const tags = activeZeroPowerTags(result.data?.list);
  if (!tags.length) throw Error('No active zero-power benefit tag; credit queue needs manual review');
  return tags;
}

/** Build the per-task credit-queue request. Pure: never mutates `request`. */
export function applyBilling(request, mode, tags) {
  if (mode !== 'credits') return request;
  const list = Array.isArray(tags) ? tags : [];
  const valid = list.filter(isZeroPowerBenefitTag);
  if (!valid.length) throw Error('Credit queue requires active zero-power benefit tags');
  const merge = (existing) => {
    const merged = Array.isArray(existing) ? [...existing] : [];
    for (const tag of valid) if (!merged.includes(tag)) merged.push(tag);
    return merged;
  };
  return {
    ...request,
    requestId: randomUUID().replaceAll('-', ''),
    disableBenefitTags: merge(request.disableBenefitTags),
    params: {
      ...request.params,
      infiniteSwitch: 1,
      disableBenefitTags: merge(request.params?.disableBenefitTags),
    },
  };
}
