import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  decideResponse,
  draftResponse,
  getNarrative,
  getResponseText,
  narrativeReportUrl,
  setNarrativeStatus,
  updateReview,
} from "../api.js";
import {
  EvidenceBadge,
  LifecycleBadge,
  ReviewButtons,
  Sparkline,
  formatNumber,
} from "../components/Signals.jsx";

const NARRATIVE_STATUSES = [
  "watching",
  "reviewing",
  "responded",
  "archived",
  "resolved",
];

// Raw ISO strings with microseconds are unreadable in a header line.
function formatWhen(iso) {
  if (!iso) return "unknown";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

function Stat({ label, value, sub }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-3">
      <div className="text-xl font-semibold tabular-nums text-slate-900">
        {value}
      </div>
      <div className="mt-0.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
        {label}
      </div>
      {sub && <div className="mt-1 text-xs text-slate-400">{sub}</div>}
    </div>
  );
}

function Section({ title, children, aside }) {
  return (
    <section className="mt-8">
      <div className="flex items-center justify-between gap-4 border-b border-slate-200 pb-2">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          {title}
        </h2>
        {aside}
      </div>
      {children}
    </section>
  );
}

// The whole draft, slot by slot. Showing the structure rather than a blob of
// text is the point: the analyst can see it followed the Debunking Handbook
// order, and can tell at a glance if a slot came back thin.
const SLOTS = [
  ["fact", "Fact (lead with this)"],
  ["warning", "Warning"],
  ["myth", "Myth (stated once)"],
  ["fallacy_name", "Fallacy"],
  ["explanation", "Why it is wrong"],
  ["replacement", "What is actually true"],
  ["reinforcement", "Fact, restated"],
];

function ResponseCard({ response, analystKey, onDecided }) {
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const draft = response.draft || {};
  const decided = response.status !== "draft";

  async function decide(status) {
    setBusy(status);
    setError("");
    try {
      onDecided(
        await decideResponse(response.response_id, { status, analystKey }),
      );
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy("");
    }
  }

  async function copy() {
    setError("");
    try {
      const text = await getResponseText(response.response_id, analystKey);
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    } catch (err) {
      setError(err.message);
    }
  }

  const statusCls =
    response.status === "approved"
      ? "bg-green-100 text-green-900 ring-green-200"
      : response.status === "rejected"
        ? "bg-slate-100 text-slate-500 ring-slate-200"
        : "bg-amber-100 text-amber-900 ring-amber-200";

  return (
    <article className="mt-4 rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span
          className={`rounded-full px-2 py-0.5 text-xs font-semibold ring-1 ${statusCls}`}
        >
          {response.status}
        </span>
        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">
          {response.mode === "prebunk" ? "Pre-bunk" : "Debunk"}
        </span>
        <code className="text-xs text-slate-400">{response.protocol}</code>
      </div>

      {response.mode === "prebunk" && (
        <p className="mt-3 rounded-md bg-blue-50 px-3 py-2 text-xs text-blue-900">
          This narrative is still early, so the draft inoculates against the
          manipulation technique without restating the rumor. Repeating a claim
          to people who have not seen it spreads it.
        </p>
      )}

      <dl className="mt-4 space-y-3">
        {SLOTS.filter(([key]) => draft[key]).map(([key, label]) => (
          <div key={key}>
            <dt className="text-xs font-semibold uppercase tracking-wide text-slate-400">
              {label}
            </dt>
            <dd className="mt-0.5 text-sm text-slate-800">{draft[key]}</dd>
          </div>
        ))}
      </dl>

      {draft.suggested_post && (
        <div className="mt-4 rounded-md border border-slate-200 bg-slate-50 p-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
            Suggested post
          </p>
          <p className="mt-1 text-sm text-slate-800">{draft.suggested_post}</p>
        </div>
      )}

      <p className="mt-3 text-xs text-slate-500">
        Sources:{" "}
        {(response.citations || []).map((id) => (
          <code key={id} className="mr-1 rounded bg-slate-100 px-1">
            {id}
          </code>
        ))}
        {draft.dropped_citations?.length > 0 && (
          <span className="ml-1 text-amber-700">
            ({draft.dropped_citations.length} invented citation
            {draft.dropped_citations.length === 1 ? "" : "s"} dropped)
          </span>
        )}
      </p>

      {draft.tone_notes?.length > 0 && (
        <details className="mt-3 text-xs text-slate-500">
          <summary className="cursor-pointer font-medium text-slate-600">
            Tone guidance this draft follows
          </summary>
          <ul className="mt-2 list-disc space-y-1 pl-5">
            {draft.tone_notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </details>
      )}

      {error && <p className="mt-3 text-sm text-red-700">{error}</p>}

      <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3">
        {!decided && (
          <>
            <button
              type="button"
              disabled={Boolean(busy)}
              onClick={() => decide("approved")}
              className="rounded-md bg-slate-900 px-3 py-1.5 text-xs font-semibold text-white hover:bg-slate-700 disabled:opacity-50"
            >
              {busy === "approved" ? "Approving..." : "Approve"}
            </button>
            <button
              type="button"
              disabled={Boolean(busy)}
              onClick={() => decide("rejected")}
              className="rounded-md bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 ring-1 ring-slate-300 hover:bg-slate-100 disabled:opacity-50"
            >
              Reject
            </button>
          </>
        )}
        {response.status === "approved" && (
          <button
            type="button"
            onClick={copy}
            className="rounded-md bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 ring-1 ring-slate-300 hover:bg-slate-100"
          >
            {copied ? "Copied" : "Copy for sending"}
          </button>
        )}
        <span className="text-xs text-slate-400">
          {response.approved_by
            ? `${response.status} by ${response.approved_by}`
            : "Checkit never posts. An approved draft is copied out and sent by a person."}
        </span>
      </div>
    </article>
  );
}

export default function NarrativePage() {
  const { narrativeId } = useParams();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [drafting, setDrafting] = useState(false);
  const [refusal, setRefusal] = useState(null);
  const [savingPost, setSavingPost] = useState("");
  const [analystKey, setAnalystKey] = useState("");

  const load = useCallback(() => {
    setLoading(true);
    getNarrative(narrativeId)
      .then(setData)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [narrativeId]);

  useEffect(load, [load]);

  async function requestDraft() {
    setDrafting(true);
    setRefusal(null);
    setError("");
    try {
      const created = await draftResponse(narrativeId, { analystKey });
      setData((current) => ({
        ...current,
        responses: [created, ...(current.responses || [])],
      }));
    } catch (err) {
      // A refusal is the system working, not failing, so it reads as guidance.
      if (err.reason) setRefusal({ reason: err.reason, message: err.message });
      else setError(err.message);
    } finally {
      setDrafting(false);
    }
  }

  async function review(postId, status) {
    setSavingPost(postId);
    setError("");
    try {
      const updated = await updateReview(postId, {
        status,
        note: "",
        actor: "analyst",
        analystKey,
      });
      setData((current) => ({
        ...current,
        members: current.members.map((m) =>
          m.post_id === postId ? { ...m, ...updated } : m,
        ),
      }));
    } catch (err) {
      setError(err.message);
    } finally {
      setSavingPost("");
    }
  }

  async function changeStatus(status) {
    try {
      await setNarrativeStatus(narrativeId, status, analystKey);
      setData((current) => ({
        ...current,
        narrative: { ...current.narrative, status },
      }));
    } catch (err) {
      setError(err.message);
    }
  }

  if (loading) return <p className="text-slate-500">Loading narrative...</p>;
  if (error && !data)
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
        {error}
      </div>
    );
  if (!data) return null;

  const { narrative, members, evidence, responses, trajectory } = data;
  const reach = members.reduce(
    (sum, m) => sum + Number(m.like_count || 0) + Number(m.retweet_count || 0),
    0,
  );
  const platforms = [...new Set(members.map((m) => m.source || "unknown"))];
  const reviewed = members.filter(
    (m) => m.review_status && m.review_status !== "unreviewed",
  ).length;
  const evidenceState =
    ["contradicted", "mixed", "supported", "retrieved"].find((state) =>
      members.some((m) => m.evidence_state === state),
    ) || "insufficient";

  return (
    <div>
      <Link
        to="/"
        className="text-sm font-medium text-blue-600 hover:underline"
      >
        &larr; Back to narratives
      </Link>

      <div className="mt-3 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold">{narrative.label}</h1>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <LifecycleBadge state={narrative.lifecycle_state} />
            <EvidenceBadge state={evidenceState} />
            <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">
              {narrative.topic || "untopiced"}
            </span>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={narrative.status || "watching"}
            onChange={(e) => changeStatus(e.target.value)}
            aria-label="Narrative status"
            className="rounded-md border border-slate-300 px-2 py-1.5 text-sm"
          >
            {NARRATIVE_STATUSES.map((status) => (
              <option key={status} value={status}>
                {status}
              </option>
            ))}
          </select>
          <a
            href={narrativeReportUrl(narrativeId, "html")}
            target="_blank"
            rel="noopener noreferrer"
            className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-semibold text-white hover:bg-slate-700"
          >
            Situation report
          </a>
        </div>
      </div>

      {error && (
        <div className="mt-4 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          {error}
        </div>
      )}

      <section className="mt-6 grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <Stat label="Reach" value={formatNumber(reach)} />
        <Stat label="Posts" value={members.length} />
        <Stat
          label="Platforms"
          value={platforms.length}
          sub={platforms.join(", ")}
        />
        <Stat
          label="Reach / hr"
          value={formatNumber(Math.round(trajectory.growth_per_hour || 0))}
        />
        <Stat label="Sources" value={evidence.length} />
        <Stat label="Reviewed" value={`${reviewed}/${members.length}`} />
      </section>

      <Section
        title="Reach over time"
        aside={
          <span className="text-xs text-slate-400">
            {trajectory.observations} observation
            {trajectory.observations === 1 ? "" : "s"} over{" "}
            {Number(trajectory.span_hours || 0).toFixed(1)}h
          </span>
        }
      >
        <div className="mt-3 text-red-600">
          <Sparkline points={trajectory.points} width={640} height={72} />
        </div>
        <p className="mt-2 text-xs text-slate-500">
          First seen {formatWhen(narrative.first_seen_at)} &middot; last seen{" "}
          {formatWhen(narrative.last_seen_at)}
        </p>
      </Section>

      <Section title={`Posts carrying this narrative (${members.length})`}>
        <ul className="mt-3 space-y-3">
          {members.map((m) => (
            <li
              key={m.post_id}
              className="rounded-lg border border-slate-200 bg-white p-3"
            >
              <p className="text-sm text-slate-800">{m.claim}</p>
              <p className="mt-1 text-xs text-slate-500">
                @{m.username || "unknown"} on {m.source || "unknown"} &middot;{" "}
                {formatNumber(
                  Number(m.like_count || 0) + Number(m.retweet_count || 0),
                )}{" "}
                reach
              </p>
              <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
                <ReviewButtons
                  value={m.review_status}
                  busy={savingPost === m.post_id}
                  onChoose={(status) => review(m.post_id, status)}
                />
                {m.fact_check_url && (
                  <a
                    href={m.fact_check_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-xs font-medium text-blue-600 hover:underline"
                  >
                    Published fact-check
                  </a>
                )}
              </div>
            </li>
          ))}
        </ul>
      </Section>

      <Section title={`Evidence (${evidence.length})`}>
        {evidence.length === 0 ? (
          <p className="mt-3 text-sm text-slate-500">
            No evidence retrieved for this narrative yet. That is not evidence
            the claim is true or false.
          </p>
        ) : (
          <ul className="mt-3 space-y-3">
            {evidence.map((e) => (
              <li
                key={e.id}
                className="rounded-lg border border-slate-200 bg-white p-3"
              >
                <a
                  href={e.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-sm font-medium text-blue-600 hover:underline"
                >
                  {e.title || e.id}
                </a>
                <p className="mt-1 text-xs text-slate-500">
                  {e.publisher} &middot; {e.published_at || "date unknown"}{" "}
                  &middot; relevance {Number(e.relevance_score || 0).toFixed(2)}
                </p>
                {e.passage && (
                  <p className="mt-2 text-xs text-slate-600">{e.passage}</p>
                )}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section
        title="Response"
        aside={
          <button
            type="button"
            onClick={requestDraft}
            disabled={drafting}
            className="rounded-md bg-slate-900 px-3 py-1.5 text-xs font-semibold text-white hover:bg-slate-700 disabled:opacity-50"
          >
            {drafting ? "Drafting..." : "Draft a response"}
          </button>
        }
      >
        {refusal && (
          <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
            <span className="font-semibold">No draft was generated.</span>{" "}
            {refusal.message}
          </div>
        )}
        {(responses || []).length === 0 && !refusal && (
          <p className="mt-3 text-sm text-slate-500">
            No drafts yet. A draft can only be generated when retrieved evidence
            actually contradicts the claim, and Checkit never posts it: an
            approved draft is copied out and sent by a person.
          </p>
        )}
        {(responses || []).map((r) => (
          <ResponseCard
            key={r.response_id}
            response={r}
            analystKey={analystKey}
            onDecided={(updated) =>
              setData((current) => ({
                ...current,
                responses: current.responses.map((item) =>
                  item.response_id === updated.response_id ? updated : item,
                ),
              }))
            }
          />
        ))}
      </Section>

      <details className="mt-8 text-xs text-slate-500">
        <summary className="cursor-pointer font-medium text-slate-600">
          Analyst key
        </summary>
        <p className="mt-2">
          Required only when the deployment sets <code>ANALYST_API_KEY</code>.
          Stored in this tab only.
        </p>
        <input
          type="password"
          value={analystKey}
          onChange={(e) => setAnalystKey(e.target.value)}
          placeholder="Analyst key"
          className="mt-2 rounded-md border border-slate-300 px-2 py-1 text-sm"
        />
      </details>
    </div>
  );
}
