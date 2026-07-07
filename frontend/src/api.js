const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

export async function checkClaim(text) {
  // Abort after 45s so the UI doesn't spin forever if the backend is cold or
  // the AI model is throttled.
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 45000);
  let res;
  try {
    res = await fetch(`${API_URL}/check`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
      signal: controller.signal,
    });
  } catch (err) {
    if (err.name === "AbortError") {
      throw new Error(
        "This is taking too long — the AI model may be busy. Please try again in a moment.",
      );
    }
    throw new Error("Couldn't reach the server. Please try again.");
  } finally {
    clearTimeout(timer);
  }
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

// Fire-and-forget wake-up so the free-tier backend is warm by the time the
// user submits (avoids a cold-start wait on the first real request).
export function warmUp() {
  fetch(`${API_URL}/health`).catch(() => {});
}

export async function getHistory() {
  const res = await fetch(`${API_URL}/history`);
  if (!res.ok) throw new Error(`Failed to load history (${res.status})`);
  return res.json();
}
