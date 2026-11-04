export const ledgerFormFields = [
  ["surcharge_percent", "Phụ phí nhập tay (%)"],
  ["budget", "Ngân sách nhập tay"],
  ["recipient", "Người nhận nhập tay"],
  ["project", "Dự án nhập tay"],
  ["unit", "Đơn vị nhập tay"],
] as const;

export type LedgerFormField = (typeof ledgerFormFields)[number][0];
export type StructuredLedgerDraft = Record<LedgerFormField | "records", string>;
export type StructuredLedgerValues = {
  records: Array<{ label: string; value: string }>;
} & Partial<Record<LedgerFormField, string>>;

export const emptyLedgerDraft: StructuredLedgerDraft = {
  records: "",
  surcharge_percent: "",
  budget: "",
  recipient: "",
  project: "",
  unit: "",
};

export function structuredLedgerValues(
  draft: StructuredLedgerDraft,
): StructuredLedgerValues {
  const records = draft.records
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const separator = line.lastIndexOf(":");
      if (separator < 1 || !line.slice(separator + 1).trim())
        throw new Error("LEDGER_FORM_INVALID");
      return {
        label: line.slice(0, separator).trim(),
        value: line.slice(separator + 1).trim(),
      };
    });
  const result: StructuredLedgerValues = { records };
  for (const [field] of ledgerFormFields) {
    if (draft[field].trim()) result[field] = draft[field].trim();
  }
  return result;
}
