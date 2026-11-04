import { useEffect, useRef, useState } from "react";
import {
  connectionMessages,
  type ProviderChoice,
  type Catalog,
  type Request,
} from "../data/providers";
export interface ProviderDialogProps {
  current: string;
  request: Request;
  onApply: (provider: string, model: string) => Promise<void>;
  onClose: () => void;
}
export function useProviderConnections({
  current,
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
  const canSelect =
    chosen?.id !== "antigravity-google" &&
    (chosen?.status === "ready" ||
      (chosen?.id === "openai-api" && chosen.status === "configured"));
  const signingIn = providers.some((p) => p.status === "signing_in");

  useEffect(() => {
    const element = dialog.current!;
    const opener = document.activeElement;
    element.showModal();
    let active = true;
    request<Catalog>("/providers")
      .then((result) => {
        if (active) setProviders(result.providers);
      })
      .catch(() => {
        if (active) setError("Không thể tải danh sách provider.");
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
          if (active) setProviders(result.providers);
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
  }, [signingIn, working, request]);

  async function action(path: string) {
    if (working) return;
    setWorking(true);
    setError("");
    try {
      const result = await request<Catalog>(path, "POST");
      setProviders(result.providers);
    } catch (reason) {
      const code = reason instanceof Error ? reason.message : "";
      setError(
        connectionMessages[code] ??
          "Không thể kết nối provider. Hãy thử kiểm tra lại.",
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
      await onApply(chosen.id, chosen.model);
      onClose();
    } catch (reason) {
      const code = reason instanceof Error ? reason.message : "";
      setError(
        connectionMessages[code] ??
          "Chưa xác nhận được kết quả đổi provider. Hãy thử lại để xác nhận đích nhận trước khi gửi.",
      );
    } finally {
      setWorking(false);
    }
  }

  function selectChoice(id: string) {
    setSelected(id);
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
  };
}
