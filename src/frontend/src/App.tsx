import { useChatController } from "./controllers/useChatController";
import { api } from "./network/api";
import { ProvidersDialog } from "./ProvidersDialog";
import { SessionPanel } from "./views/SessionPanel";
import { ProviderHeader } from "./views/ProviderHeader";
import { ChatPanel } from "./views/ChatPanel";
import { PrivacyPanel } from "./views/PrivacyPanel";
import { PrivateWorkPanel } from "./views/PrivateWorkPanel";
import { WorkflowPanel } from "./views/WorkflowPanel";
import { getWorkflowStatus } from "./controllers/workspace";

export function App() {
  const chat = useChatController();
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Đến nội dung hội thoại
      </a>
      {chat.model.showProviders && (
        <ProvidersDialog
          current={chat.model.provider}
          request={api}
          onApply={chat.actions.selectProvider}
          onClose={chat.actions.closeProviders}
        />
      )}
      <SessionPanel {...chat} />
      <main className="workspace" id="main-content">
        <ProviderHeader {...chat} />
        <WorkflowPanel status={getWorkflowStatus(chat.model)} />
        <nav className="panel-links" aria-label="Các khu vực làm việc">
          <a href="#chat-panel">Hội thoại</a>
          <a href="#privacy-panel">Kiểm tra bản gửi</a>
          <a href="#protected-info">Thông tin được bảo vệ</a>
          <a href="#private-work-panel">Private work local</a>
          <a href="#provider-session">Provider và phiên</a>
          <a href="#session-audit" onClick={chat.actions.openAudit}>
            Nhật ký phiên
          </a>
        </nav>
        <div className="work-area">
          <ChatPanel {...chat} />
          <PrivacyPanel {...chat} />
        </div>
        <PrivateWorkPanel {...chat} />
      </main>
    </div>
  );
}
