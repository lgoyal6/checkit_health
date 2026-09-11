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

export async function updateReview(
  postId,
  { status, note, actor, analystKey },
) {
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

// --- narrative ledger ------------------------------------------------------

async function getJson(path, label) {
  const res = await fetch(`${API_URL}${path}`);
  if (!res.ok) throw new Error(`Failed to load ${label} (${res.status})`);
  return res.json();
}

// Capability disclosure. The monitor uses this to say plainly when grouping is
// running on the offline embedding, which only merges near-identical wording,
// rather than implying semantic narrative detection it isn't doing.
export async function getMeta() {
  return getJson("/meta", "settings");
}

export async function getNarratives({
  limit = 100,
  topic = "",
  lifecycleState = "",
} = {}) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (topic) params.set("topic", topic);
  if (lifecycleState) params.set("lifecycle_state", lifecycleState);
  return getJson(`/narratives?${params.toString()}`, "narratives");
}

export async function getNarrative(narrativeId) {
  return getJson(`/narratives/${encodeURIComponent(narrativeId)}`, "narrative");
}

export function narrativeReportUrl(narrativeId, format = "html") {
  return `${API_URL}/narratives/${encodeURIComponent(narrativeId)}/report?format=${format}`;
}

export function exportUrl(
  kind,
  { window = "7d", topic = "", source = "" } = {},
) {
  if (kind === "narratives") return `${API_URL}/export/narratives.csv`;
  const params = new URLSearchParams({ window, limit: "2000" });
  if (topic) params.set("topic", topic);
  if (source) params.set("source", source);
  return `${API_URL}/export/claims.csv?${params.toString()}`;
}

function analystHeaders(analystKey) {
  return {
    "Content-Type": "application/json",
    ...(analystKey ? { "X-Analyst-Key": analystKey } : {}),
  };
}

async function readError(res, fallback) {
  const body = await res.json().catch(() => ({}));
  const detail = body.detail;
  if (detail && typeof detail === "object") {
    const error = new Error(detail.message || fallback);
    error.reason = detail.reason;
    throw error;
  }
  throw new Error(detail || fallback);
}

export async function setNarrativeStatus(narrativeId, status, analystKey) {
  const res = await fetch(
    `${API_URL}/narratives/${encodeURIComponent(narrativeId)}/status`,
    {
      method: "PATCH",
      headers: analystHeaders(analystKey),
      body: JSON.stringify({ status }),
    },
  );
  if (!res.ok) await readError(res, `Status update failed (${res.status})`);
  return res.json();
}

// Ask for a counter-message draft. The mode (pre-bunk vs debunk) is decided by
// the backend from where the narrative sits on its curve, not chosen here: a
// rumor most people haven't seen yet gets inoculation against the tactic, not
// a restatement of the claim.
export async function draftResponse(narrativeId, { author, analystKey } = {}) {
  const res = await fetch(
    `${API_URL}/narratives/${encodeURIComponent(narrativeId)}/responses`,
    {
      method: "POST",
      headers: analystHeaders(analystKey),
      body: JSON.stringify({ author: author || "analyst" }),
    },
  );
  if (!res.ok)
    await readError(res, `Could not draft a response (${res.status})`);
  return res.json();
}

export async function decideResponse(
  responseId,
  { status, approvedBy, note, analystKey } = {},
) {
  const res = await fetch(
    `${API_URL}/responses/${encodeURIComponent(responseId)}`,
    {
      method: "PATCH",
      headers: analystHeaders(analystKey),
      body: JSON.stringify({
        status,
        approved_by: approvedBy || "analyst",
        note: note || "",
      }),
    },
  );
  if (!res.ok) await readError(res, `Decision failed (${res.status})`);
  return res.json();
}

// Approved drafts only. There is no publish path anywhere in this client: the
// export is text a person copies and sends themselves.
export async function getResponseText(responseId, analystKey) {
  const res = await fetch(
    `${API_URL}/responses/${encodeURIComponent(responseId)}/text`,
    { headers: analystKey ? { "X-Analyst-Key": analystKey } : {} },
  );
  if (!res.ok) await readError(res, `Export failed (${res.status})`);
  return res.text();
}

export async function getTuning(k = 20) {
  return getJson(`/tuning/weights?k=${k}`, "tuning report");
}
