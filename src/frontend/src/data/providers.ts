export function providerName(id: string): string {
  if (id === "codex-chatgpt") return "Codex";
  if (id === "antigravity-google") return "Antigravity";
  if (id === "openai-api") return "OpenAI API";
  if (id === "local-qwen") return "Qwen local";
  return "Demo local";
}

export function providerBadge(id: string): string {
  return id === "codex-chatgpt" ? "Codex · ChatGPT" : providerName(id);
}

export type QuotaBucket = {
  group: string;
  window: string;
  remaining_fraction: number;
  reset_time: string;
};

export type ProviderChoice = {
  id: string;
  name: string;
  model: string;
  auth: string;
  status: string;
  code: string;
  authenticated?: boolean;
  quota?: QuotaBucket[];
};
export type Catalog = { providers: ProviderChoice[] };
export type Request = <T>(
  path: string,
  method?: string,
  body?: unknown,
) => Promise<T>;

export const connectionMessages: Record<string, string> = {
  OPENAI_API_KEY_REQUIRED:
    "Thiếu OPENAI_API_KEY. Đặt key trong cấu hình backend trên máy, khởi động lại rồi mở lại hộp này.",
  OPENAI_API_KEY_INVALID:
    "OPENAI_API_KEY trong backend chưa hợp lệ. Kiểm tra cấu hình trên máy rồi khởi động lại.",
  OPENAI_MODEL_REQUIRED:
    "Thiếu CENSITIVE_OPENAI_MODEL. Đặt model OpenAI API trong cấu hình backend rồi khởi động lại.",
  CODEX_NOT_FOUND:
    "Chưa tìm thấy Codex CLI trên máy. Cài Codex rồi kiểm tra lại.",
  CODEX_VERSION_UNVERIFIED:
    "Bản Codex này chưa được kiểm chứng với gateway. Kết nối đang bị khóa.",
  CODEX_CHATGPT_LOGIN_REQUIRED: "Chưa có phiên đăng nhập ChatGPT trong Codex.",
  CODEX_LOGIN_FAILED:
    "Đăng nhập chưa hoàn tất. Thử lại hoặc dùng lệnh bên dưới.",
  CODEX_LOGIN_TIMEOUT:
    "Đã hết thời gian chờ đăng nhập. Bạn có thể bắt đầu lại.",
  CODEX_LOGIN_CANCELLED:
    "Đã dừng chờ đăng nhập. Tài khoản đã đăng nhập trước đó không bị đăng xuất.",
  LOGIN_BUSY: "Một cửa sổ khác đang đăng nhập Codex. Hãy chờ rồi kiểm tra lại.",
  SESSION_BUSY: "Hãy chờ lượt chat kết thúc trước khi đổi provider.",
  SESSION_CHANGED:
    "Phiên đã thay đổi trong lúc kiểm tra kết nối. Hãy đóng hộp này rồi kiểm tra lại.",
  AUTH_REQUIRED:
    "Kết nối ứng dụng đã hết hiệu lực. Đóng hộp này và tạo phiên mới.",
  ANTIGRAVITY_NOT_FOUND:
    "Chưa tìm thấy Antigravity CLI trên máy. Cài CLI chính thức rồi kiểm tra lại.",
  NOT_FOUND:
    "Chưa tìm thấy Antigravity CLI trên máy. Cài CLI chính thức rồi kiểm tra lại.",
  ANTIGRAVITY_VERSION_UNVERIFIED:
    "Phiên bản Antigravity CLI này chưa được kiểm chứng với gateway. Kết nối chat đang bị khóa.",
  VERSION_UNVERIFIED:
    "Phiên bản Antigravity CLI này chưa được kiểm chứng với gateway. Kết nối chat đang bị khóa.",
  ANTIGRAVITY_BOUNDARY_UNVERIFIED:
    "Tài khoản Google đã được nhận diện, nhưng cách ly context/tools của Antigravity chưa được kiểm chứng.",
  ANTIGRAVITY_CONNECTION_FAILED:
    "Không thể kiểm tra kết nối Antigravity trên máy.",
  ANTIGRAVITY_METADATA_FAILED: "Không thể đọc metadata Antigravity từ CLI.",
  ANTIGRAVITY_METADATA_TIMEOUT:
    "Antigravity CLI hết thời gian trả về metadata.",
  ANTIGRAVITY_METADATA_OUTPUT_LIMIT:
    "Metadata Antigravity vượt giới hạn cho phép.",
  ANTIGRAVITY_METADATA_INVALID:
    "Metadata Antigravity không đúng định dạng được kiểm chứng.",
};
