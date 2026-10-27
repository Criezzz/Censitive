export type FactState = {
  enabled: boolean;
  epoch: number;
  facts: {
    id: string;
    revision: number;
    value: string;
    category: string;
    scope: string;
    superseded: boolean;
    source_id: string;
  }[];
  sources: { id: string; revision: number; text: string; digest?: string }[];
  entities?: {
    id: string;
    value: string;
    source_id: string;
    source_revision?: number;
    start?: number;
    end?: number;
    protected: boolean;
    aliases: {
      value: string;
      source_id: string;
      source_revision?: number;
      start?: number;
      end?: number;
    }[];
  }[];
};
export type Correction = {
  operation_id: string;
  fact_id: string;
  expected_revision: number;
  expected_epoch: number;
  text: string;
};
export type DraftOccurrence = { start: number; end: number; quote: string };
export type Occurrence = DraftOccurrence & {
  source_id: string;
  source_revision: number;
  source_digest: string;
};
export type DraftAction =
  | { kind: "create"; occurrence: DraftOccurrence; protect: boolean }
  | { kind: "alias"; occurrence: DraftOccurrence; entity_id: string };
export type FactCategory =
  "confidential" | "personal" | "health" | "financial" | "credential";
export type DraftFact = { occurrence: DraftOccurrence; category: FactCategory };
export type CreateFactCommand = {
  operation_id: string;
  command: {
    kind: "create_fact";
    expected_epoch: number;
    evidence: Occurrence;
    category: FactCategory;
  };
};
export type ProtectionCommand = {
  operation_id: string;
  command: IdentityCommand["command"] | CreateFactCommand["command"];
};
export type FactResult = {
  receipt: {
    operation_id: string;
    committed_epoch: number;
    fact_id: string;
    fact_revision: number;
  };
  state: FactState;
};
export type IdentityCommand = {
  operation_id: string;
  command:
    | {
        kind: "create";
        expected_epoch: number;
        evidence: Occurrence;
        protect: boolean;
      }
    | { kind: "protect"; expected_epoch: number; entity_id: string }
    | {
        kind: "alias";
        expected_epoch: number;
        entity_id: string;
        mention: Occurrence;
      };
};
export type IdentityResult = {
  receipt: {
    operation_id: string;
    committed_epoch: number;
    entity_id: string;
    policy_fact_id: string | null;
  };
  state: FactState;
};
export type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  provider?: string;
};
export type Preview = {
  streaming: boolean;
  state?: FactState | null;
  id: string;
  sanitized: string;
  body: string;
  expires_in: number;
  history_count: number;
  semantic: null | {
    model: string;
    elapsed_ms: number;
    context_messages: number;
    findings: {
      quote: string;
      category: string;
      source: number;
      evidence_source: number;
      evidence_quote: string;
    }[];
  };
  detections: {
    start: number;
    end: number;
    category: string;
    value: string;
    restore_allowed: boolean;
  }[];
  review?: {
    expected_epoch: number;
    items: {
      candidate_id: string;
      start: number;
      end: number;
      quote: string;
      can_reject: boolean;
      locked_reason: string;
      rejected: boolean;
    }[];
  } | null;
};
export type PreviewDecision = {
  operation_id: string;
  prepared_id: string;
  expected_epoch: number;
  command: { kind: "reject_new_mask"; candidate_id: string };
};
export type PreviewDecisionResult = {
  receipt: {
    operation_id: string;
    old_prepared_id: string;
    new_prepared_id: string;
    body_sha256: string;
  };
  preview_available: boolean;
  preview: Preview | null;
};
export type SessionView = {
  id: string;
  provider: string;
  model: string;
  messages: ChatMessage[];
  audit: { event: string; status: string; version: number }[];
  state?: FactState;
};
export type Phase = "loading" | "idle" | "preparing" | "ready" | "sending";
export type Bootstrap = {
  csrf: string;
  provider: string;
  model: string;
  detector: string;
  state_enabled?: boolean;
};
export type SessionIdentity = { id: string; provider: string; model: string };

export type InspectTab = "preview" | "json" | "evidence";
