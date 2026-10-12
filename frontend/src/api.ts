export function token() {
  return sessionStorage.getItem("dreamer-token") || "";
}
export function headers() {
  return token() ? { Authorization: `Bearer ${token()}` } : undefined;
}
export async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: {
      ...headers(),
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const detail = data.detail;
    throw new Error(
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((d: { msg: string }) => d.msg).join("; ")
          : `Request failed (${response.status})`,
    );
  }
  return response.json();
}
export async function download(jobId: string, filename: string) {
  const response = await fetch(`/api/jobs/${jobId}/files/${filename}`, {
    headers: headers(),
  });
  if (!response.ok) throw new Error("Unable to download the artifact");
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
