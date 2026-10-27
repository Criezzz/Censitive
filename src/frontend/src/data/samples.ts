export const samples = [
  {
    icon: "↗",
    title: "Soạn email riêng tư",
    subtitle: "Tên, email và dự án",
    text: "Viết email cho Lan tại lan@example.com về tiến độ dự án Maple.",
    terms: "Lan\nMaple",
  },
  {
    icon: "⌘",
    title: "Giữ kín mã truy cập",
    subtitle: "Che mã và không phục hồi",
    text: "Nhắc tôi thay mã truy cập DEMO_SECRET_ALPHA_001 trước buổi demo.",
    terms: "",
  },
  {
    icon: "◎",
    title: "Bảo vệ một cụm từ",
    subtitle: "Bạn xác định phạm vi cần che",
    text: "Soạn lời nhắn về kế hoạch giảm 30 vị trí cho nhóm quản lý.",
    terms: "kế hoạch giảm 30 vị trí",
  },
];

export type Sample = (typeof samples)[number];
