import type { RefObject } from "react";
import type { ChatModel, ChatActions } from "../data/chatModel";

export interface PanelProps {
  model: ChatModel;
  actions: ChatActions;
  elements: {
    bottom: RefObject<HTMLDivElement | null>;
    input: RefObject<HTMLTextAreaElement | null>;
    providerButton: RefObject<HTMLButtonElement | null>;
  };
}
