import type { PanelProps } from "./PanelProps";
import { FactPanel } from "../FactPanel";
import { IdentityPanel } from "../IdentityPanel";

export function ProtectionPanel(props: PanelProps) {
  const {
    session,
    factState,
    busy,
    identityConfirmed,
    providerConfirmed,
    factsConfirmed,
    draftSelection,
    draftActions,
    draftFacts,
  } = props.model;
  const { correctFact, changeIdentity, stageIdentity, stageFact, clearDraft } =
    props.actions;
  return (
    <section
      id="protected-info"
      aria-label="Thông tin được bảo vệ"
      tabIndex={-1}
    >
      <h2 className="protected-heading">Thông tin được bảo vệ</h2>
      <FactPanel
        key={session}
        state={factState}
        busy={busy || !identityConfirmed || !providerConfirmed}
        uncertain={!factsConfirmed}
        onCorrect={correctFact}
      />
      <IdentityPanel
        key={`identity:${session}`}
        state={factState}
        busy={busy || !factsConfirmed || !providerConfirmed}
        uncertain={!identityConfirmed}
        draftSelection={draftSelection}
        actions={draftActions}
        factMarks={draftFacts}
        onCommand={changeIdentity}
        onStage={stageIdentity}
        onStageFact={stageFact}
        onClearDraft={clearDraft}
      />
    </section>
  );
}
