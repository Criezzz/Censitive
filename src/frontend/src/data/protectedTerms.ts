export function termIssue(text: string, terms: string[]): string {
  const term = text.trim();
  if (!term) return "Nhập một cụm cần bảo vệ.";
  if (/[\r\n]/.test(text))
    return "Chọn một cụm trên cùng một dòng để bảo vệ bằng khớp chính xác.";
  if (terms.includes(term)) return "Cụm này đã có trong danh sách bảo vệ.";
  return "";
}
