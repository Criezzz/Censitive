let csrf = "";
export async function api<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method,
    credentials: "same-origin",
    headers:
      method === "GET"
        ? {}
        : { "Content-Type": "application/json", "X-CSRF-Token": csrf },
    body: method === "GET" ? undefined : JSON.stringify(body ?? {}),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.code ?? "REQUEST_FAILED");
  return result as T;
}

export function setCsrf(value: string) {
  csrf = value;
}
