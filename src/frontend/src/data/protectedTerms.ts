export function termIssue(text: string, terms: string[]): string {
  const term = text.trim();
  if (!term) return "Nhập một cụm cần bảo vệ.";
  if (/[\r\n]/.test(text))
    return "Chọn một cụm trên cùng một dòng để bảo vệ bằng khớp chính xác.";
  if (Array.from(term).length > 200) return "Chọn cụm không quá 200 ký tự.";
  if (term.includes("[[PG_"))
    return "Cụm chứa ký hiệu dành riêng của ứng dụng.";
  if (terms.includes(term)) return "Cụm này đã có trong danh sách bảo vệ.";
  if (terms.length >= 30)
    return "Đã đủ 30 cụm bảo vệ. Hãy kiểm tra danh sách trước khi thêm.";
  return "";
}
