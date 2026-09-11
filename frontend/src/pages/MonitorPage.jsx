import { Fragment, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { exportUrl, getStats, getTrending, updateReview } from "../api.js";
import { PriorityMeter, ReviewButtons } from "../components/Signals.jsx";
import { truthVerdict } from "../verdict.js";

const WINDOWS = [
  ["24h", "Last 24h"],
  ["7d", "Last 7d"],
  ["30d", "Last 30d"],
  ["all", "All time"],
];

// Tiers are computed client-side from whatever rows the current filters
// return, so they stay meaningful whether it's a quiet day or something is
// actually spiking — no backend change needed.
const TIER_DEFS = [
  {
    key: "viral",
    label: "Viral",
    pctFloor: 0.97,
    dot: "bg-red-500",
    pill: "bg-red-600",
  },
  {
    key: "high",
    label: "High",
    pctFloor: 0.85,
    dot: "bg-amber-500",
    pill: "bg-amber-500",
  },
  {
    key: "medium",
    label: "Medium",
    pctFloor: 0.5,
    dot: "bg-blue-500",
    pill: "bg-blue-500",
  },
  {
    key: "low",
    label: "Low",
    pctFloor: 0,
    dot: "bg-slate-400",
    pill: "bg-slate-500",
  },
];

function formatDate(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString();
}

function formatNumber(n) {
  return new Intl.NumberFormat().format(Number(n || 0));
}

function reach(row) {
  return (
    Number(row.reach ?? 0) ||
    Number(row.like_count || 0) + Number(row.retweet_count || 0)
  );
}

// Sort options for ordering rows within a tier. "reach" (likes + reposts) is
// the default and also what tiers are bucketed by; the others just reorder
// the same rows by a different engagement signal.
const SORT_OPTIONS = [
  ["priority", "Review priority"],
  ["reach", "Reach (likes + reposts)"],
  ["likes", "Likes"],
  ["reposts", "Reposts"],
  ["newest", "Newest"],
  ["confidence", "Confidence"],
];

function metricValue(row, sortBy) {
  switch (sortBy) {
    case "priority":
      return Number(row.priority?.score || 0);
    case "likes":
      return Number(row.like_count || 0);
    case "reposts":
      return Number(row.retweet_count || 0);
    case "newest":
      return (
        new Date(row.timestamp_processed || row.timestamp || 0).getTime() || 0
      );
    case "confidence":
      return Number(row.confidence || 0);
    case "reach":
    default:
      return reach(row);
  }
}

// Buckets `rows` into { viral, high, medium, low } by percentile of reach
// within the current result set. Each bucket is sorted high-to-low.
function tierRows(rows) {
  const buckets = { viral: [], high: [], medium: [], low: [] };
  if (rows.length === 0) return buckets;

  const sorted = [...rows].sort((a, b) => reach(a) - reach(b));
  const n = sorted.length;
  const thresholdAt = (pct) =>
    reach(sorted[Math.max(0, Math.min(n - 1, Math.ceil(pct * n) - 1))]);
  const thresholds = {
    low: thresholdAt(0.5),
    medium: thresholdAt(0.85),
    high: thresholdAt(0.97),
  };

  for (const row of rows) {
    const r = reach(row);
    let tier;
    if (r <= thresholds.low) tier = "low";
    else if (r <= thresholds.medium) tier = "medium";
    else if (r <= thresholds.high) tier = "high";
    else tier = "viral";
    buckets[tier].push(row);
  }
  for (const key of Object.keys(buckets)) {
    buckets[key].sort((a, b) => reach(b) - reach(a));
  }
  return buckets;
}

function verdictBadge(row) {
  if (row.fact_check_verdict) {
    const verdict = truthVerdict(row.fact_check_verdict);
    return (
      <span
        className={`rounded-full px-2 py-0.5 text-xs font-semibold ring-1 ${verdict.cls}`}
      >
        {verdict.label}
      </span>
    );
  }
  return (
    <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600 ring-1 ring-slate-200">
      Needs review
    </span>
  );
}

function StatCard({ label, value, sub }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <div className="text-2xl font-semibold tabular-nums text-slate-900">
        {value}
      </div>
      <div className="mt-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
        {label}
      </div>
      {sub && <div className="mt-2 text-xs text-slate-500">{sub}</div>}
    </div>
  );
}

function ClaimTable({
  rows,
  sortBy,
  expanded,
  setExpanded,
  onCheckClaim,
  onReview,
  savingPost,
}) {
  if (rows.length === 0) {
    return (
      <p className="px-4 py-6 text-sm text-slate-500">
        No claims in this tier for the current filters.
      </p>
    );
  }
  const sortedRows = [...rows].sort(
    (a, b) => metricValue(b, sortBy) - metricValue(a, sortBy),
  );
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[880px] text-left text-sm">
        <thead className="bg-slate-100 text-xs uppercase tracking-wide text-slate-500">
          <tr>
            <th className="px-4 py-3">Claim</th>
            <th className="px-4 py-3">Priority</th>
            <th className="px-4 py-3">Topic</th>
            <th className="px-4 py-3">Reach</th>
            <th className="px-4 py-3">Verdict</th>
            <th className="px-4 py-3">Source</th>
            <th className="px-4 py-3">Date</th>
            <th className="px-4 py-3 text-right">Actions</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {sortedRows.map((row) => {
            const isOpen = expanded === row.post_id;
            return (
              <Fragment key={row.post_id}>
                <tr className="hover:bg-slate-50">
                  <td className="max-w-md px-4 py-3">
                    <div className="truncate font-medium text-slate-800">
                      {row.claim}
                    </div>
                    <div className="mt-1 truncate text-xs text-slate-500">
                      @{row.username || "unknown"}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <PriorityMeter priority={row.priority} compact />
                  </td>
                  <td className="px-4 py-3 text-slate-600">
                    {row.topic || "-"}
                  </td>
                  <td className="px-4 py-3 font-semibold tabular-nums text-slate-800">
                    {formatNumber(reach(row))}
                  </td>
                  <td className="px-4 py-3">{verdictBadge(row)}</td>
                  <td className="px-4 py-3 text-slate-600">
                    {row.source || "-"}
                  </td>
                  <td className="px-4 py-3 text-slate-500">
                    {formatDate(row.timestamp_processed || row.timestamp)}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <div className="flex justify-end gap-2">
                      <button
                        type="button"
                        onClick={() => setExpanded(isOpen ? null : row.post_id)}
                        aria-expanded={isOpen}
                        aria-controls={`claim-details-${row.post_id}`}
                        className="whitespace-nowrap rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 transition-colors hover:bg-slate-100 focus:outline-none focus:ring-2 focus:ring-slate-400 focus:ring-offset-2"
                      >
                        {isOpen ? "Hide details" : "View details"}
                      </button>
                      <button
                        type="button"
                        onClick={() => onCheckClaim(row)}
                        className="whitespace-nowrap rounded-md bg-slate-900 px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-slate-700 focus:outline-none focus:ring-2 focus:ring-slate-400 focus:ring-offset-2"
                      >
                        Check claim
                      </button>
                    </div>
                  </td>
                </tr>
                {isOpen && (
                  <tr
                    id={`claim-details-${row.post_id}`}
                    className="bg-slate-50"
                  >
                    <td colSpan={8} className="px-4 py-4">
                      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_280px]">
                        <div>
                          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                            Full text
                          </p>
                          <p className="mt-1 whitespace-pre-wrap text-slate-800">
                            {row.text}
                          </p>
                          {row.classification_reasoning && (
                            <>
                              <p className="mt-4 text-xs font-semibold uppercase tracking-wide text-slate-500">
                                Reasoning
                              </p>
                              <p className="mt-1 text-sm text-slate-700">
                                {row.classification_reasoning}
                              </p>
                            </>
                          )}
                          {row.fact_check_verdict && (
                            <p className="mt-4 text-sm text-slate-700">
                              <span className="font-semibold">
                                {row.fact_check_source || "Fact check"}:
                              </span>{" "}
                              {row.fact_check_verdict}
                            </p>
                          )}
                          {row.fact_check_url && (
                            <a
                              href={row.fact_check_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="mt-1 inline-block text-sm font-medium text-blue-600 hover:underline"
                            >
                              Read the full fact check
                            </a>
                          )}
                        </div>
                        <div className="rounded-md border border-slate-200 bg-white p-3 text-sm text-slate-600">
                          <PriorityMeter priority={row.priority} />
                          <div className="mt-3 border-t border-slate-100 pt-3">
                            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                              Your call
                            </p>
                            <div className="mt-2">
                              <ReviewButtons
                                value={row.review_status}
                                busy={savingPost === row.post_id}
                                onChoose={(status) => onReview(row, status)}
                              />
                            </div>
                            <p className="mt-2 text-xs text-slate-400">
                              Recorded against this claim&apos;s stored triage
                              score, which is what lets the ranking be retuned
                              against real decisions.
                            </p>
                          </div>
                          {row.narrative_id && (
                            <Link
                              to={`/narratives/${encodeURIComponent(row.narrative_id)}`}
                              className="mt-3 inline-block border-t border-slate-100 pt-3 text-xs font-medium text-blue-600 hover:underline"
                            >
                              Open the narrative this belongs to
                            </Link>
                          )}
                          <div className="mt-3 flex justify-between gap-4 border-t border-slate-100 pt-3">
                            <span>Likes</span>
                            <span className="font-semibold tabular-nums text-slate-900">
                              {formatNumber(row.like_count)}
                            </span>
                          </div>
                          <div className="mt-2 flex justify-between gap-4">
                            <span>Reposts</span>
                            <span className="font-semibold tabular-nums text-slate-900">
                              {formatNumber(row.retweet_count)}
                            </span>
                          </div>
                          <div className="mt-2 flex justify-between gap-4">
                            <span>Confidence</span>
                            <span className="font-semibold tabular-nums text-slate-900">
                              {row.confidence != null
                                ? `${Math.round(row.confidence * 100)}%`
                                : "-"}
                            </span>
                          </div>
                        </div>
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function MonitorPage() {
  const navigate = useNavigate();
  const [window, setWindow] = useState("7d");
  const [topic, setTopic] = useState("");
  const [source, setSource] = useState("");
  const [query, setQuery] = useState("");
  const [rows, setRows] = useState([]);
  const [stats, setStats] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState(null);
  const [activeTier, setActiveTier] = useState("viral");
  const [sortBy, setSortBy] = useState("priority");
  const [savingPost, setSavingPost] = useState("");
  const [analystKey, setAnalystKey] = useState("");

  // Triage from the page that ranks claims. Review used to live only on
  // History, which reads the last 50 rows, so decisions were never recorded
  // where the volume actually is.
  async function review(row, status) {
    setSavingPost(row.post_id);
    setError("");
    try {
      const updated = await updateReview(row.post_id, {
        status,
        note: "",
        actor: "analyst",
        analystKey,
      });
      setRows((current) =>
        current.map((item) =>
          item.post_id === row.post_id ? { ...item, ...updated } : item,
        ),
      );
    } catch (err) {
      setError(err.message);
    } finally {
      setSavingPost("");
    }
  }

  function checkMonitoredClaim(row) {
    navigate("/check", {
      state: { claim: row.claim || row.text || "" },
    });
  }

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    Promise.all([
      getStats(window),
      getTrending({ window, topic, source, limit: 200 }),
    ])
      .then(([statsData, trendingData]) => {
        if (!active) return;
        setStats(statsData || {});
        setRows(Array.isArray(trendingData) ? trendingData : []);
        setExpanded(null);
      })
      .catch((err) => {
        if (active) setError(err.message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [window, topic, source]);

  const topicOptions = stats.by_topic || [];
  const sourceOptions = stats.by_source || [];
  const topTopic = topicOptions[0]?.name || "-";

  const visible = useMemo(() => {
    const term = query.trim().toLowerCase();
    if (!term) return rows;
    return rows.filter((row) =>
      [row.claim, row.text, row.topic, row.username]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(term)),
    );
  }, [rows, query]);

  const tiers = useMemo(() => tierRows(visible), [visible]);
  const activeRows = activeTier === "all" ? visible : tiers[activeTier];

  // If a filter/search wipes out the active tier, hop to one that has rows.
  useEffect(() => {
    if (activeTier === "all") return;
    if (tiers[activeTier]?.length > 0) return;
    const fallback = TIER_DEFS.find((t) => tiers[t.key].length > 0);
    if (fallback) setActiveTier(fallback.key);
  }, [tiers, activeTier]);

  return (
    <div>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold">
            Viral health misinformation monitor
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-slate-600">
            Individual posts from monitored sources, ranked by review priority.
            To work by rumor instead of by post, use the{" "}
            <Link to="/" className="font-medium text-blue-600 hover:underline">
              narrative queue
            </Link>
            .
          </p>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-sm text-slate-500" aria-live="polite">
            {visible.length} claims shown
          </span>
          <a
            href={exportUrl("claims", { window, topic, source })}
            className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-100"
          >
            Export CSV
          </a>
        </div>
      </div>

      {error && (
        <div className="mt-6 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          {error}
        </div>
      )}

      <section className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Claims monitored"
          value={formatNumber(stats.flagged_claims)}
          sub={
            loading
              ? "Loading"
              : `${WINDOWS.find(([k]) => k === window)?.[1] || window}`
          }
        />
        <StatCard
          label="Total reach"
          value={formatNumber(stats.total_reach)}
          sub="Likes plus reposts"
        />
        <StatCard
          label="Likely false"
          value={formatNumber(stats.likely_false_count)}
          sub={`${formatNumber(stats.verified_count)} verified by fact-checks`}
        />
        <StatCard
          label="Top topic"
          value={topTopic}
          sub="By reach in this window"
        />
      </section>

      <section className="mt-6 flex flex-col gap-3 rounded-lg border border-slate-200 bg-white p-4 shadow-sm lg:flex-row lg:items-center">
        <select
          value={window}
          onChange={(e) => setWindow(e.target.value)}
          aria-label="Monitor time window"
          className="rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:outline-none"
        >
          {WINDOWS.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        <select
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          aria-label="Filter by topic"
          className="rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:outline-none"
        >
          <option value="">All topics</option>
          {topicOptions.map((item) => (
            <option key={item.name || "unknown"} value={item.name || ""}>
              {item.name || "unknown"} ({formatNumber(item.count)})
            </option>
          ))}
        </select>
        <select
          value={source}
          onChange={(e) => setSource(e.target.value)}
          aria-label="Filter by source"
          className="rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:outline-none"
        >
          <option value="">All sources</option>
          {sourceOptions.map((item) => (
            <option key={item.name || "unknown"} value={item.name || ""}>
              {item.name || "unknown"} ({formatNumber(item.count)})
            </option>
          ))}
        </select>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Search monitored claims"
          placeholder="Search claim, post, author..."
          className="min-w-0 flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:outline-none"
        />
        <select
          value={sortBy}
          onChange={(e) => setSortBy(e.target.value)}
          aria-label="Sort monitored claims"
          className="rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:outline-none"
        >
          {SORT_OPTIONS.map(([value, label]) => (
            <option key={value} value={value}>
              Sort: {label}
            </option>
          ))}
        </select>
      </section>

      {loading ? (
        <p className="mt-6 text-slate-500" role="status">
          Loading monitor...
        </p>
      ) : visible.length === 0 ? (
        <p className="mt-6 text-slate-500">
          No monitored social claims found for these filters.
        </p>
      ) : (
        <div className="mt-6 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
          <div className="flex flex-wrap gap-2 border-b border-slate-200 bg-slate-50 px-4 py-3">
            <button
              onClick={() => setExpanded(null) || setActiveTier("all")}
              disabled={visible.length === 0}
              aria-pressed={activeTier === "all"}
              className={[
                "flex items-center gap-2 rounded-full px-3 py-1.5 text-sm font-medium transition-colors",
                activeTier === "all"
                  ? "bg-slate-900 text-white"
                  : "bg-white text-slate-600 ring-1 ring-slate-200 hover:bg-slate-100",
              ].join(" ")}
            >
              All claims
              <span
                className={`rounded-full px-1.5 text-xs ${activeTier === "all" ? "bg-white/25" : "bg-slate-100"}`}
              >
                {visible.length}
              </span>
            </button>
            {TIER_DEFS.map((t) => {
              const count = tiers[t.key].length;
              const isActive = activeTier === t.key;
              return (
                <button
                  key={t.key}
                  onClick={() => setExpanded(null) || setActiveTier(t.key)}
                  disabled={count === 0}
                  aria-pressed={isActive}
                  className={[
                    "flex items-center gap-2 rounded-full px-3 py-1.5 text-sm font-medium transition-colors",
                    isActive
                      ? `${t.pill} text-white`
                      : count === 0
                        ? "cursor-not-allowed bg-white text-slate-300 ring-1 ring-slate-200"
                        : "bg-white text-slate-600 ring-1 ring-slate-200 hover:bg-slate-100",
                  ].join(" ")}
                >
                  <span
                    className={`h-2 w-2 rounded-full ${isActive ? "bg-white" : t.dot}`}
                  />
                  {t.label}
                  <span
                    className={`rounded-full px-1.5 text-xs ${isActive ? "bg-white/25" : "bg-slate-100"}`}
                  >
                    {count}
                  </span>
                </button>
              );
            })}
          </div>
          <ClaimTable
            rows={activeRows}
            sortBy={sortBy}
            expanded={expanded}
            setExpanded={setExpanded}
            onCheckClaim={checkMonitoredClaim}
            onReview={review}
            savingPost={savingPost}
          />
        </div>
      )}

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
