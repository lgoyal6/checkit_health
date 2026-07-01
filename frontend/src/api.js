const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

export async function checkClaim(text) {
  const res = await fetch(`${API_URL}/check`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (body.detail) detail = body.detail;
    } catch {
      // response had no JSON body; keep the generic message
    }
    throw new Error(detail);
  }
  return res.json();
}

export async function getHistory() {
  const res = await fetch(`${API_URL}/history`);
  if (!res.ok) throw new Error(`Failed to load history (${res.status})`);
  return res.json();
}
