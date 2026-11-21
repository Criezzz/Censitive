import { useEffect, useRef, useState } from "react";
import {
  connectionMessages,
  type ProviderChoice,
  type Catalog,
  type Request,
} from "../data/providers";
export interface ProviderDialogProps {
  current: string;
  currentModel: string;
  request: Request;
  onApply: (provider: string, model: string) => Promise<void>;
  onClose: () => void;
}
export function useProviderConnections({
  current,
  currentModel,
  request,
  onApply,
  onClose,
}: ProviderDialogProps) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [providers, setProviders] = useState<ProviderChoice[]>([]);
  const [selected, setSelected] = useState(current);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  const chosen = providers.find((p) => p.id === selected);
  const [model, setModel] = useState(currentModel);
  const models = chosen?.models?.length
    ? chosen.models
    : chosen?.model
      ? [chosen.model]
      : [];
  const canSelect =
    models.includes(model) &&
    (chosen?.status === "ready" ||
      (chosen?.id === "openai-api" && chosen.status === "configured"));
  const signingIn = providers.some((p) => p.status === "signing_in");

  function applyCatalog(result: Catalog) {
    const visible = result.providers
      .filter((p) => p.id === "openai-api" || p.id === "codex-chatgpt")
      .sort(
        (a, b) => Number(b.id === "openai-api") - Number(a.id === "openai-api"),
      );
    const next = visible.find((p) => p.id === selected) ?? visible[0];
    const available = next?.models?.length
      ? next.models
      : next?.model
        ? [next.model]
        : [];
    setProviders(visible);
    setSelected(next?.id ?? "");
    setModel((currentValue) =>
      next?.id === selected && available.includes(currentValue)
        ? currentValue
        : (available[0] ?? ""),
    );
  }

  useEffect(() => {
    const element = dialog.current!;
    const opener = document.activeElement;
    element.showModal();
    let active = true;
    request<Catalog>("/providers")
      .then((result) => {
        if (active) {
          applyCatalog(result);
        }
      })
      .catch(() => {
        if (active) setError("Không thể tải danh sách bên nhận.");
      });
    return () => {
      active = false;
      element.close();
      if (opener instanceof HTMLElement && opener.isConnected) opener.focus();
    };
  }, [request]);

  useEffect(() => {
    if (!signingIn || working) return;
    let active = true;
    let pending = false;
    const timer = window.setInterval(() => {
      if (pending) return;
      pending = true;
      request<Catalog>("/providers")
        .then((result) => {
          if (active) applyCatalog(result);
        })
        .catch(() => {
          if (active) setError("Mất kết nối ứng dụng. Hãy kiểm tra lại.");
        })
        .finally(() => {
          pending = false;
        });
    }, 2000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [signingIn, working, request, selected]);

  async function action(path: string) {
    if (working) return;
    setWorking(true);
    setError("");
    try {
      const result = await request<Catalog>(path, "POST");
      applyCatalog(result);
    } catch (reason) {
      const code = reason instanceof Error ? reason.message : "";
      setError(
        connectionMessages[code] ??
          "Không thể kết nối bên nhận. Hãy thử kiểm tra lại.",
      );
    } finally {
      setWorking(false);
    }
  }

  async function apply() {
    if (!chosen || working || !canSelect) return;
    setWorking(true);
    setError("");
    try {
      await onApply(chosen.id, model);
      onClose();
    } catch (reason) {
      const code = reason instanceof Error ? reason.message : "";
      setError(
        connectionMessages[code] ??
          "Chưa xác nhận được kết quả đổi bên nhận. Hãy thử lại để xác nhận đích nhận trước khi gửi.",
      );
    } finally {
      setWorking(false);
    }
  }

  function selectChoice(id: string) {
    setSelected(id);
    const next = providers.find((p) => p.id === id);
    const available = next?.models?.length
      ? next.models
      : next?.model
        ? [next.model]
        : [];
    setModel(
      id === current && available.includes(currentModel)
        ? currentModel
        : (available[0] ?? ""),
    );
    setError("");
  }
  return {
    dialog,
    providers,
    selected,
    working,
    error,
    chosen,
    canSelect,
    signingIn,
    action,
    apply,
    selectChoice,
    model,
    setModel,
  };
}
