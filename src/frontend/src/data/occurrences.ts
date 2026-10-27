import type { FactState, DraftOccurrence } from "./contracts";

export function occurrenceContext(
  state: FactState,
  item: {
    value: string;
    source_id: string;
    source_revision?: number;
    start?: number;
    end?: number;
  },
) {
  const source = state.sources.find(
    (candidate) => candidate.id === item.source_id,
  );
  if (
    !source ||
    source.revision !== item.source_revision ||
    typeof item.start !== "number" ||
    typeof item.end !== "number" ||
    !Number.isInteger(item.start) ||
    !Number.isInteger(item.end)
  )
    return null;
  const chars = Array.from(source.text);
  if (
    item.start < 0 ||
    item.end <= item.start ||
    item.end > chars.length ||
    chars.slice(item.start, item.end).join("") !== item.value
  )
    return null;
  return {
    before: chars.slice(Math.max(0, item.start - 20), item.start).join(""),
    after: chars.slice(item.end, item.end + 20).join(""),
    sourceNumber: state.sources.indexOf(source) + 1,
    start: item.start + 1,
    end: item.end,
  };
}

export function selectedOccurrence(
  text: string,
  start: number,
  end: number,
): DraftOccurrence | null {
  if (start >= end || start < 0 || end > text.length) return null;
  const boundaries = [0];
  for (const character of text)
    boundaries.push(boundaries.at(-1)! + character.length);
  const a = boundaries.indexOf(start),
    b = boundaries.indexOf(end);
  const quote = text.slice(start, end);
  if (a < 0 || b < 0 || !quote.trim() || b - a > 800) return null;
  return { start: a, end: b, quote };
}
