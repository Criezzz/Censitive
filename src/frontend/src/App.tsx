import { useChatController } from "./controllers/useChatController";
import { api } from "./network/api";
import { ProvidersDialog } from "./ProvidersDialog";
import { SessionPanel } from "./views/SessionPanel";
import { ProviderHeader } from "./views/ProviderHeader";
import { ChatPanel } from "./views/ChatPanel";
import { PrivacyPanel } from "./views/PrivacyPanel";
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
          currentModel={chat.model.providerConfirmed ? chat.model.model : ""}
          request={api}
          onApply={chat.actions.selectProvider}
          onClose={chat.actions.closeProviders}
        />
      )}
      <SessionPanel {...chat} />
      <main className="workspace" id="main-content">
        <ProviderHeader {...chat} />
        <WorkflowPanel status={getWorkflowStatus(chat.model)} />
        <div className="work-area">
          <ChatPanel {...chat} />
          <PrivacyPanel {...chat} />
        </div>
      </main>
    </div>
  );
}
