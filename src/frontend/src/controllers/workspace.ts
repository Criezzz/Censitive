import type { ChatModel } from "../data/chatModel";
import type { WorkflowStatus } from "../data/workspace";

export function getWorkflowStatus(model: ChatModel): WorkflowStatus {
  const state = (
    step: WorkflowStatus["step"],
    title: string,
    detail: string,
    target = "chat-panel",
    action = "Đến hội thoại",
    attention = false,
  ): WorkflowStatus => ({ step, title, detail, target, action, attention });

  if (model.phase === "loading")
    return state(
      0,
      "Đang mở hội thoại",
      "Chờ xác nhận trạng thái từ máy của bạn.",
    );
  if (
    model.recoveryStatus === "unknown_send" ||
    model.recoveryStatus === "storage_failed" ||
    model.sessionMutation?.status === "unknown"
  )
    return state(
      0,
      "Cần kiểm tra phiên",
      "Kết quả thao tác trước chưa rõ. Không tự gửi lại.",
      "provider-session",
      "Kiểm tra phiên",
      true,
    );
  if (
    model.privatePhase !== "idle" ||
    model.privateLedgerPhase !== "idle" ||
    model.privateLedgerCandidate ||
    model.privateCancelPending ||
    model.privateLedgerCancelPending ||
    model.privateLedgerRerunPending
  )
    return state(
      0,
      "Đang có việc riêng",
      "Theo dõi hoặc hoàn tất thao tác trong khu vực xử lý local.",
      "private-work-panel",
      "Đến việc riêng",
    );
  if (model.cancelPending)
    return state(0, "Đang hủy lượt", "Chờ xác nhận trước khi thao tác tiếp.");
  if (!model.providerConfirmed)
    return state(
      0,
      "Cần xác nhận provider",
      "Chọn kết nối khả dụng trước khi chuẩn bị bản gửi.",
      "provider-session",
      "Kiểm tra kết nối",
      true,
    );
  if (!model.factsConfirmed || !model.identityConfirmed || model.pendingReview)
    return state(
      0,
      "Cần kiểm tra thay đổi",
      "Thông tin bảo vệ hoặc bản gửi chưa được xác nhận.",
      "privacy-panel",
      "Kiểm tra bản gửi",
      true,
    );
  if (model.phase === "preparing")
    return state(
      1,
      "Đang chuẩn bị trên máy",
      "Chưa gửi hội thoại tới provider. Bạn sẽ kiểm tra bản chuẩn bị gửi trước.",
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
      "chat-panel",
      "Xem thông báo",
      true,
    );
  if (model.phase === "ready" && model.preview)
    return state(
      2,
      "Bản gửi đang chờ bạn duyệt",
      "Đọc toàn bộ bản chuẩn bị gửi và đích nhận. Chỉ gửi khi bạn bấm duyệt.",
      "privacy-panel",
      "Kiểm tra bản gửi",
    );
  if (!model.draft && model.messages.at(-1)?.role === "assistant")
    return state(
      3,
      "Đã nhận phản hồi",
      "Bạn có thể tiếp tục hội thoại; lượt mới vẫn cần xem lại và duyệt.",
    );
  return state(
    1,
    "Bắt đầu từ nội dung của bạn",
    "Nhập yêu cầu, bôi chọn đoạn cần bảo vệ nếu muốn, rồi chuẩn bị bản gửi.",
    "chat-panel",
    "Soạn nội dung",
  );
}
