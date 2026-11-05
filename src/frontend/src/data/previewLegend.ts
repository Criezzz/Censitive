import type { Preview } from "./contracts";

export function previewLegend(
  sanitized: string,
  detections: Preview["detections"],
  draft: string,
) {
  const tokens = sanitized.match(/\[\[PG_v1_[0-9a-f]{32}\]\]/g) ?? [];
  const sorted = detections
    .filter((d) => !d.unmasked_once)
    .sort((a, b) => a.start - b.start);
  if (tokens.length !== sorted.length) return null;
  let index = 0;
  const restored = sanitized.replace(
    /\[\[PG_v1_[0-9a-f]{32}\]\]/g,
    () => sorted[index++].value,
  );
  if (restored !== draft) return null;
  const entries = tokens.map((token, i) => {
    const { value, category, restore_allowed } = sorted[i];
    return { token, value, category, restore_allowed, count: 1 };
  });
  const distinct = new Map<string, (typeof entries)[number]>();
  for (const entry of entries) {
    const previous = distinct.get(entry.token);
    if (previous) {
      if (
        previous.value !== entry.value ||
        previous.category !== entry.category ||
        previous.restore_allowed !== entry.restore_allowed
      )
        return null;
      previous.count++;
    } else distinct.set(entry.token, entry);
  }
  return [...distinct.values()];
}
