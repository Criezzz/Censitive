import type { ChatModel } from "../data/chatModel";
import type { WorkflowStatus } from "../data/workspace";

export function getWorkflowStatus(model: ChatModel): WorkflowStatus {
  const state = (
    step: WorkflowStatus["step"],
    title: string,
    detail: string,
    attention = false,
  ): WorkflowStatus => ({ step, title, detail, attention });

  if (model.phase === "loading")
    return state(
      0,
      model.preview ? "Đang cập nhật bản gửi" : "Đang mở hội thoại",
      "Chờ xác nhận trạng thái từ máy của bạn.",
    );
  if (model.sessionMutation?.status === "unknown")
    return state(
      0,
      "Cần kiểm tra phiên",
      "Kết quả thao tác trước chưa rõ. Không tự gửi lại.",
      true,
    );
  if (model.cancelPending)
    return state(0, "Đang hủy lượt", "Chờ xác nhận trước khi thao tác tiếp.");
  if (!model.providerConfirmed)
    return state(
      0,
      "Cần xác nhận kết nối",
      "Chọn kết nối khả dụng trước khi chuẩn bị bản gửi.",
      true,
    );
  if (model.phase === "preparing")
    return state(
      1,
      "Đang chuẩn bị trên máy",
      "Chưa gửi hội thoại tới bên nhận. Bạn sẽ kiểm tra bản chuẩn bị gửi trước.",
    );
  if (model.phase === "sending")
    return state(
      3,
      "Đang nhận phản hồi",
      "Bản đã duyệt đã được chuyển sang bước gửi. Hủy không thu hồi dữ liệu đã gửi.",
    );
  if (model.error)
    return state(
      0,
      "Có thao tác cần kiểm tra",
      "Đọc thông báo ở ô soạn thảo trước khi tiếp tục.",
      true,
    );
  if (model.phase === "ready" && model.preview)
    return state(
      2,
      "Bản gửi đang chờ bạn duyệt",
      "Đọc toàn bộ bản chuẩn bị gửi và đích nhận. Chỉ gửi khi bạn bấm duyệt.",
    );
  if (model.messages.some((message) => message.role === "assistant"))
    return state(
      3,
      "Đã nhận phản hồi",
      "Chọn “Câu hỏi mới” để bắt đầu một phiên khác.",
    );
  return state(
    1,
    "Bắt đầu từ nội dung của bạn",
    "Nhập yêu cầu, bôi chọn đoạn cần bảo vệ nếu muốn, rồi chuẩn bị bản gửi.",
  );
}
