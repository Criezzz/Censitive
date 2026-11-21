export const errors: Record<string, string> = {
  AUTH_REQUIRED: "Phiên kết nối đã hết hiệu lực. Hãy tải lại trang.",
  CANCELLED: "Đã hủy lượt này.",
  CODEX_CHATGPT_LOGIN_REQUIRED:
    "Hãy đăng nhập Codex bằng ChatGPT rồi chuẩn bị lại lượt này.",
  CODEX_UNEXPECTED_TOOL:
    "Codex trả về thao tác công cụ ngoài phạm vi chat. Lượt này đã bị chặn.",
  FINAL_ANSWER_FORBIDDEN:
    "Đáp án trên máy chứa nội dung không được phép phục hồi. Phản hồi bị chặn và chưa được lưu.",
  INVALID_INPUT: "Dữ liệu chưa hợp lệ. Kiểm tra lại nội dung.",
  MASK_NOT_FOUND: "Không tìm thấy cụm cần che trong bản gửi.",
  METHOD_INVALID_OUTPUT:
    "Qwen chưa trả kết quả hợp lệ. Chưa lưu đáp án; ứng dụng không tự gửi lại hoặc đổi phương pháp.",
  METHOD_ONE_SHOT: "Chọn “Câu hỏi mới” để bắt đầu phiên khác.",
  METHOD_UNAVAILABLE:
    "Qwen trên máy chưa sẵn sàng. Kiểm tra kết nối rồi chuẩn bị lại. Chưa gửi dữ liệu ra ngoài.",
  OPENAI_API_KEY_INVALID:
    "OPENAI_API_KEY chưa hợp lệ. Kiểm tra biến môi trường rồi khởi động lại.",
  OPENAI_API_KEY_REQUIRED:
    "Đặt OPENAI_API_KEY trong biến môi trường rồi khởi động lại.",
  OPENAI_AUTH_FAILED:
    "OpenAI API từ chối xác thực. Kiểm tra khóa và quyền truy cập của dự án; lượt này chưa được lưu.",
  OPENAI_INCOMPLETE:
    "OpenAI API chưa trả lời trọn vẹn. Phản hồi này không được lưu; ứng dụng không tự gửi lại.",
  OPENAI_RATE_LIMIT:
    "OpenAI API đã đạt giới hạn hoặc hạn mức. Kiểm tra tài khoản trước khi chuẩn bị lại; ứng dụng không tự gửi lại.",
  OPENAI_REFUSED:
    "OpenAI từ chối xử lý yêu cầu này. Chưa có kết quả được lưu; ứng dụng không tự gửi lại hoặc đổi bên nhận.",
  OPENAI_REQUEST_REJECTED:
    "OpenAI API không chấp nhận mô hình hoặc nội dung yêu cầu này. Kiểm tra cấu hình; ứng dụng không tự chuyển bên nhận.",
  OUTBOUND_BLOCKED:
    "Bước kiểm tra cuối phát hiện nội dung chưa được che. Yêu cầu đã bị chặn.",
  PROVIDER_FAILED:
    "Bên nhận không hoàn tất phản hồi. Bạn có thể chuẩn bị lại và thử lại.",
  PROVIDER_TIMEOUT:
    "Bên nhận hết thời gian chờ. Lượt này chưa được lưu; không tự gửi lại.",
  RELEASE_FORBIDDEN:
    "Cụm này luôn được che: thông tin truy cập hoặc cụm bạn tự thêm vào bảo vệ.",
  REQUEST_FAILED: "Chưa kết nối được ứng dụng trên máy.",
  SEMANTIC_INVALID_OUTPUT:
    "Kết quả phân tích trên máy chưa hợp lệ. Chưa có bản được phép gửi; hãy thử chuẩn bị lại.",
  SEMANTIC_TIMEOUT:
    "Phân tích trên máy hết thời gian chờ. Hãy rút ngắn nội dung rồi thử lại.",
  SEMANTIC_UNAVAILABLE:
    "Mô hình trên máy chưa sẵn sàng. Khởi động bộ phân tích Qwen rồi chuẩn bị lại. Chưa gửi dữ liệu ra ngoài.",
  SEMANTIC_UNGROUNDED:
    "Mô hình đề xuất một đoạn không khớp nguồn. Yêu cầu được giữ tại máy; hãy chuẩn bị lại.",
  SESSION_BUSY: "Phiên đang xử lý. Hãy chờ hoặc hủy lượt hiện tại.",
  SESSION_NOT_FOUND:
    "Phiên không còn tồn tại. Hãy tải lại trang hoặc tạo phiên mới.",
  STALE_PREVIEW:
    "Bản xem trước đã thay đổi. Hãy chuẩn bị lại trước khi gửi.",
  UNKNOWN_PROVIDER_MODEL: "Chọn mô hình trong Kết nối AI.",
};
