import type { useChatController } from "../controllers/useChatController";

export type ChatModel = ReturnType<typeof useChatController>["model"];
export type ChatActions = ReturnType<typeof useChatController>["actions"];
