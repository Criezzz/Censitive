export type Phase = "loading" | "idle" | "preparing" | "ready" | "sending";
export type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  provider?: string;
  metadata?: { public_plan: string; numeric_warning: boolean };
};
export type SessionSummary = {
  id: string;
  title: string;
  provider: string;
  model: string;
  message_count: number;
  provider_confirmed: boolean;
};
export type SessionIdentity = { id: string; provider: string; model: string };
export type SessionView = SessionIdentity & {
  title: string;
  messages: ChatMessage[];
  terms: string[];
  provider_confirmed: boolean;
};
export type SessionList = { sessions: SessionSummary[] };
export type Bootstrap = {
  csrf: string;
  provider: string;
  model: string;
  local_available: boolean;
};
export type SessionMutation = {
  kind: "new" | "switch" | "rename" | "delete";
  session_id?: string;
  status: "busy" | "unknown";
};
export type Preview = {
  id: string;
  sanitized: string;
  body: string;
  method_changes: {
    original: string;
    sent: string;
    kind: string;
    releasable: boolean;
    released: boolean;
  }[];
  method_adjust: { released: string[]; masked: string[] };
};
