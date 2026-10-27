import type {
  ChatMessage,
  Preview,
  PreviewDecision,
  SessionView,
  Phase,
  InspectTab,
  FactState,
  Correction,
  DraftAction,
  DraftFact,
  DraftOccurrence,
  ProtectionCommand,
} from "./contracts";
import type { Sample } from "./samples";

export interface ChatModel {
  provider: string;
  model: string;
  showProviders: boolean;
  detector: string;
  semantic: boolean;
  cloud: boolean;
  codex: boolean;
  localModel: boolean;
  session: string;
  providerConfirmed: boolean;
  factState: FactState | null;
  factsConfirmed: boolean;
  identityConfirmed: boolean;
  draftActions: DraftAction[];
  draftFacts: DraftFact[];
  draftSelection: DraftOccurrence | null;
  selectionIssue: string;
  selectionNotice: string;
  messages: ChatMessage[];
  provisional: string | null;
  draft: string;
  terms: string;
  phase: Phase;
  preview: Preview | null;
  pendingReview: PreviewDecision | null;
  error: string;
  inspect: InspectTab;
  audit: SessionView["audit"];
  showAudit: boolean;
  cancelPending: boolean;
  busy: boolean;
}

export interface ChatActions {
  selectProvider(provider: string, model: string): Promise<void>;
  prepare(): Promise<void>;
  reviewPreview(candidateId?: string): Promise<void>;
  send(): Promise<void>;
  cancel(): Promise<void>;
  cancelPrepare(): Promise<void>;
  reset(): Promise<void>;
  correctFact(command: Correction): Promise<void>;
  changeIdentity(command: ProtectionCommand): Promise<void>;
  stageIdentity(action: DraftAction): void;
  stageFact(mark: DraftFact): void;
  useSample(sample: Sample): void;
  semanticSample(): void;
  changeDraft(text: string): void;
  changeTerms(text: string): void;
  selectDraft(start: number, end: number): void;
  protectSelection(): void;
  setInspect(tab: InspectTab): void;
  clearDraft(): void;
  clearError(): void;
  openProviders(): void;
  closeProviders(): void;
  toggleAudit(): void;
  openAudit(): void;
}
