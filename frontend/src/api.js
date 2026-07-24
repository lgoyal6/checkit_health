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

// Full evidence report (Rumor / Confidence Level / Summary / Key Facts /
// Analysis / Conclusion) for a single claim. Heavier than /check, so it's
// only called once a claim has already passed triage.
export async function getReport({
  claim,
  topic,
  factCheckVerdict,
  factCheckSource,
  evidence,
}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 45000);
  let res;
  try {
    res = await fetch(`${API_URL}/report`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        claim,
        topic: topic || null,
        fact_check_verdict: factCheckVerdict || null,
        fact_check_source: factCheckSource || null,
        evidence: evidence || [],
      }),
      signal: controller.signal,
    });
  } catch (err) {
    if (err.name === "AbortError") {
      throw new Error(
        "The report is taking too long — the AI model may be busy. Please try again in a moment.",
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

export async function getHistory() {
  const res = await fetch(`${API_URL}/history`);
  if (!res.ok) throw new Error(`Failed to load history (${res.status})`);
  return res.json();
}

export async function getTrending({
  window = "7d",
  topic = "",
  source = "",
  limit = 50,
} = {}) {
  const params = new URLSearchParams({ window, limit: String(limit) });
  if (topic) params.set("topic", topic);
  if (source) params.set("source", source);
  const res = await fetch(`${API_URL}/monitor?${params.toString()}`);
  if (!res.ok) throw new Error(`Failed to load monitor (${res.status})`);
  return res.json();
}

export async function getStats(window = "7d") {
  const params = new URLSearchParams({ window });
  const res = await fetch(`${API_URL}/stats?${params.toString()}`);
  if (!res.ok) throw new Error(`Failed to load stats (${res.status})`);
  return res.json();
}

export async function updateReview(postId, { status, note, actor, analystKey }) {
  const res = await fetch(
    `${API_URL}/claims/${encodeURIComponent(postId)}/review`,
    {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        ...(analystKey ? { "X-Analyst-Key": analystKey } : {}),
      },
      body: JSON.stringify({ status, note, actor }),
    },
  );
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Review update failed (${res.status})`);
  }
  return res.json();
}
