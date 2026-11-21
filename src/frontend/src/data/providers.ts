export function providerName(id: string): string {
  if (id === "codex-chatgpt") return "Codex";
  if (id === "openai-api") return "OpenAI API";
  return "OpenAI API";
}

export function providerBadge(id: string): string {
  return id === "codex-chatgpt" ? "Codex · ChatGPT" : providerName(id);
}

export type ProviderChoice = {
  id: string;
  name: string;
  model: string;
  auth: string;
  status: string;
  code: string;
  models?: string[];
};
export type Catalog = { providers: ProviderChoice[] };
export type Request = <T>(
  path: string,
  method?: string,
  body?: unknown,
) => Promise<T>;

export const connectionMessages: Record<string, string> = {
  OPENAI_API_KEY_REQUIRED:
    "Thiếu OPENAI_API_KEY. Đặt khóa trong biến môi trường, khởi động lại rồi mở lại hộp này.",
  OPENAI_API_KEY_INVALID:
    "OPENAI_API_KEY chưa hợp lệ. Kiểm tra cấu hình trên máy rồi khởi động lại.",
  OPENAI_MODEL_REQUIRED: "Tên mô hình OpenAI không hợp lệ.",
  CODEX_NOT_FOUND:
    "Chưa tìm thấy Codex CLI trên máy. Cài Codex rồi kiểm tra lại.",
  CODEX_CHATGPT_LOGIN_REQUIRED: "Chưa có phiên đăng nhập ChatGPT trong Codex.",
  CODEX_LOGIN_FAILED:
    "Đăng nhập chưa hoàn tất. Thử lại hoặc dùng lệnh bên dưới.",
  CODEX_LOGIN_TIMEOUT:
    "Đã hết thời gian chờ đăng nhập. Bạn có thể bắt đầu lại.",
  CODEX_LOGIN_CANCELLED:
    "Đã dừng chờ đăng nhập. Tài khoản đã đăng nhập trước đó không bị đăng xuất.",
  LOGIN_BUSY: "Một cửa sổ khác đang đăng nhập Codex. Hãy chờ rồi kiểm tra lại.",
  SESSION_BUSY: "Hãy chờ lượt chat kết thúc trước khi đổi bên nhận.",
  SESSION_CHANGED:
    "Phiên đã thay đổi trong lúc kiểm tra kết nối. Hãy đóng hộp này rồi kiểm tra lại.",
  AUTH_REQUIRED:
    "Kết nối ứng dụng đã hết hiệu lực. Đóng hộp này và tạo phiên mới.",
  UNKNOWN_PROVIDER_MODEL: "Mô hình này không có trong danh sách được hỗ trợ.",
};
