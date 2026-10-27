export const errors: Record<string, string> = {
  OPENAI_API_KEY_REQUIRED:
    "Backend chưa có API key OpenAI. Cấu hình key trên máy rồi kiểm tra kết nối; không nhập key vào hội thoại.",
  OPENAI_API_KEY_INVALID:
    "Cấu hình API key chưa hợp lệ. Kiểm tra cấu hình backend trên máy.",
  OPENAI_MODEL_REQUIRED:
    "Backend chưa cấu hình model OpenAI API. Chọn model trong cấu hình trước khi gửi.",
  OPENAI_AUTH_FAILED:
    "OpenAI API từ chối xác thực. Kiểm tra key và quyền truy cập của project; lượt này chưa được lưu.",
  OPENAI_RATE_LIMIT:
    "OpenAI API đã đạt giới hạn hoặc quota. Kiểm tra tài khoản trước khi chuẩn bị lại; ứng dụng không tự gửi lại.",
  OPENAI_REQUEST_REJECTED:
    "OpenAI API không chấp nhận model hoặc nội dung yêu cầu này. Kiểm tra cấu hình; ứng dụng không tự chuyển provider.",
  OPENAI_INCOMPLETE:
    "OpenAI API chưa trả lời trọn vẹn. Phản hồi này không được lưu; ứng dụng không tự gửi lại.",
  PROVIDER_TIMEOUT:
    "Provider hết thời gian chờ. Lượt này chưa được lưu; không tự gửi lại.",
  STATE_REVIEW_REQUIRED:
    "Chưa xác định chắc chắn thông tin hoặc quan hệ đang được nói tới. Hãy viết rõ người/sự việc và phạm vi cần giữ riêng rồi chuẩn bị lại. Chưa gửi dữ liệu ra ngoài.",
  STATE_STALE:
    "Thông tin đã thay đổi. Kiểm tra bản hiện hành rồi lưu lại sửa đổi.",
  STATE_LIMIT:
    "Bộ nhớ bảo vệ đã đạt giới hạn của phiên. Hãy tạo phiên mới; ứng dụng không tự bỏ thông tin cũ.",
  STATE_COMMAND_CONFLICT:
    "Mã thao tác đã được dùng cho một thay đổi khác. Kiểm tra trạng thái trước khi sửa tiếp.",
  STATE_IDENTITY_REVIEW_REQUIRED:
    "Thay đổi danh tính cần xác định lại đúng người và phạm vi. Chưa áp dụng sửa đổi này.",
  STATE_EVIDENCE_MISMATCH:
    "Đoạn đã chọn không còn khớp nguồn. Hãy chọn lại đúng lần xuất hiện.",
  STATE_ENTITY_MISSING:
    "Định danh đã chọn không còn trong phiên. Hãy tải lại trạng thái và chọn lại.",
  STATE_AMBIGUOUS_LINK:
    "Các lựa chọn chồng lên nhau nhưng chỉ tới những định danh khác nhau. Hãy bỏ lựa chọn nháp rồi chọn lại.",
  STATE_IDENTITY_NOT_APPLIED:
    "Bản chuẩn bị chưa giữ đầy đủ lựa chọn bảo vệ danh tính. Chưa gửi; hãy chuẩn bị lại.",
  STATE_FACT_NOT_APPLIED:
    "Bản chuẩn bị chưa giữ đầy đủ thông tin bạn chọn bảo vệ. Chưa gửi; hãy chuẩn bị lại.",
  IDENTITY_OUTCOME_UNKNOWN:
    "Chưa xác nhận kết quả thao tác bảo vệ. Hãy kiểm tra lại đúng thao tác đang chờ trước khi tiếp tục.",
  PREVIEW_DECISION_LOCKED:
    "Đề xuất này liên quan đến quy tắc hoặc thông tin đang được bảo vệ, nên không thể bỏ che tại đây. Hãy chuẩn bị lại.",
  PREVIEW_DECISION_INVALID:
    "Lựa chọn không còn khớp bản chuẩn bị. Chưa gửi; hãy chuẩn bị lại.",
  PREVIEW_OUTCOME_UNKNOWN:
    "Chưa xác nhận thay đổi bản gửi. Kiểm tra lại đúng thao tác đang chờ; không tự gửi lại.",
  SEMANTIC_MODEL_MISMATCH:
    "Runtime đang trả lời bằng model khác cấu hình. Hãy khởi động lại đúng profile trước khi chuẩn bị bản gửi.",
  SEMANTIC_UNAVAILABLE:
    "Model local chưa sẵn sàng. Khởi động bộ phân tích Qwen rồi chuẩn bị lại. Chưa gửi dữ liệu ra ngoài.",
  SEMANTIC_TIMEOUT:
    "Phân tích local hết thời gian chờ. Hãy rút ngắn nội dung rồi thử lại.",
  SEMANTIC_CONTEXT_LIMIT:
    "Ngữ cảnh vượt 6.000 ký tự cho bộ phân tích local. Hãy tạo phiên mới hoặc rút ngắn nội dung; ứng dụng không tự cắt lịch sử.",
  SEMANTIC_BUSY:
    "Model local đang hoàn tất một lượt phân tích. Chờ một lúc rồi chuẩn bị lại.",
  SEMANTIC_INVALID_OUTPUT:
    "Kết quả phân tích local chưa hợp lệ. Chưa có bản được phép gửi; hãy thử chuẩn bị lại.",
  SEMANTIC_UNGROUNDED:
    "Model đề xuất một đoạn không khớp nguồn. Yêu cầu được giữ tại máy; hãy chuẩn bị lại.",
  SEMANTIC_INCOMPLETE:
    "Model chưa hoàn tất phân tích. Hãy rút ngắn nội dung rồi chuẩn bị lại.",
  RESERVED_TOKEN:
    "Nội dung có ký hiệu [[PG_ dành riêng cho hệ thống. Hãy bỏ ký hiệu đó trước khi tiếp tục.",
  OVERLAPPING_DETECTIONS:
    "Các vùng cần che chồng lấn. Hãy chỉnh danh sách cụm bảo vệ để mỗi vùng được xác định rõ.",
  STALE_PREVIEW:
    "Bản xem trước đã hết hạn hoặc thay đổi. Hãy chuẩn bị lại trước khi gửi.",
  CANCELLED: "Đã hủy. Phản hồi này không được thêm vào lịch sử.",
  SESSION_BUSY: "Phiên đang xử lý. Hãy chờ hoặc hủy lượt hiện tại.",
  HISTORY_LIMIT: "Phiên đã đạt giới hạn demo. Hãy tạo phiên mới để tiếp tục.",
  DETECTION_LIMIT:
    "Nội dung có quá nhiều vùng cần che. Hãy chia thành yêu cầu ngắn hơn.",
  VAULT_LIMIT: "Phiên đã đạt giới hạn giá trị bảo vệ. Hãy tạo phiên mới.",
  RESPONSE_LIMIT:
    "Phản hồi vượt giới hạn kích thước; lượt này chưa được lưu vào lịch sử.",
  INVALID_INPUT:
    "Nội dung không hợp lệ. Chỉ nhập text, tối đa 20.000 ký tự và 30 cụm bảo vệ.",
  INVALID_TERMS:
    "Mỗi cụm bảo vệ cần 1–200 ký tự, không chứa ký hiệu token dành riêng.",
  OUTBOUND_BLOCKED:
    "Bước kiểm tra cuối phát hiện nội dung chưa được che. Yêu cầu đã bị chặn.",
  UNKNOWN_TOKEN:
    "Phản hồi chứa token không được cấp cho lượt này. Không phục hồi hoặc lưu phản hồi.",
  MALFORMED_TOKEN:
    "Token trong phản hồi không hợp lệ. Không phục hồi hoặc lưu phản hồi.",
  AUTH_REQUIRED: "Phiên kết nối đã hết hiệu lực. Hãy tải lại trang.",
  SESSION_NOT_FOUND:
    "Phiên không còn tồn tại. Hãy tải lại trang hoặc tạo phiên mới.",
  PROVIDER_FAILED:
    "Provider không hoàn tất phản hồi. Bạn có thể chuẩn bị lại và thử lại.",
  PROVIDER_BUSY: "Codex đang xử lý một lượt khác. Hãy thử lại sau.",
  CODEX_VERSION_UNVERIFIED:
    "Phiên bản Codex đã thay đổi. Cần kiểm chứng lại trước khi kết nối.",
  CODEX_CHATGPT_LOGIN_REQUIRED:
    "Hãy đăng nhập Codex bằng ChatGPT rồi chuẩn bị lại lượt này.",
  CODEX_UNEXPECTED_TOOL:
    "Codex trả về thao tác công cụ ngoài phạm vi chat. Lượt này đã bị chặn.",
};

export const correctionErrors = new Set([
  "STATE_STALE",
  "STATE_IDENTITY_REVIEW_REQUIRED",
  "STATE_COMMAND_CONFLICT",
  "STATE_COMMAND_LIMIT",
  "STATE_LIMIT",
  "INVALID_INPUT",
  "SESSION_BUSY",
]);

export const identityErrors = new Set([
  "STATE_STALE",
  "STATE_ENTITY_MISSING",
  "STATE_EVIDENCE_MISMATCH",
  "STATE_AMBIGUOUS_LINK",
  "STATE_COMMAND_CONFLICT",
  "STATE_COMMAND_LIMIT",
  "STATE_LIMIT",
  "STATE_INVALID",
  "STATE_SOURCE_MISSING",
  "STATE_CATEGORY_INVALID",
  "INVALID_INPUT",
  "SESSION_BUSY",
]);

export const reviewErrors = new Set([
  "STALE_PREVIEW",
  "STATE_STALE",
  "SESSION_BUSY",
  "STATE_COMMAND_CONFLICT",
  "STATE_COMMAND_LIMIT",
  "PREVIEW_DECISION_LOCKED",
  "PREVIEW_DECISION_INVALID",
  "STATE_FACT_MISSING",
  "INVALID_INPUT",
  "STATE_INVALID",
  "STATE_LIMIT",
]);
