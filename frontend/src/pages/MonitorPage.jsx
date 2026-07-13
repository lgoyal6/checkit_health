import { Fragment, useEffect, useMemo, useState } from "react";
import { getStats, getTrending } from "../api.js";
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
  { key: "viral", label: "Viral", pctFloor: 0.97, dot: "bg-red-500", pill: "bg-red-600" },
  { key: "high", label: "High", pctFloor: 0.85, dot: "bg-amber-500", pill: "bg-amber-500" },
  { key: "medium", label: "Medium", pctFloor: 0.5, dot: "bg-blue-500", pill: "bg-blue-500" },
  { key: "low", label: "Low", pctFloor: 0, dot: "bg-slate-400", pill: "bg-slate-500" },
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
  return Number(row.reach ?? 0) || Number(row.like_count || 0) + Number(row.retweet_count || 0);
}

// Buckets `rows` into { viral, high, medium, low } by percentile of reach
// within the current result set. Each bucket is sorted high-to-low.
function tierRows(rows) {
  const buckets = { viral: [], high: [], medium: [], low: [] };
  if (rows.length === 0) return buckets;

  const sorted = [...rows].sort((a, b) => reach(a) - reach(b));
  const n = sorted.length;
  const thresholdAt = (pct) => reach(sorted[Math.max(0, Math.min(n - 1, Math.ceil(pct * n) - 1))]);
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
      <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ring-1 ${verdict.cls}`}>
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
      <div className="text-2xl font-semibold tabular-nums text-slate-900">{value}</div>
      <div className="mt-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
        {label}
      </div>
      {sub && <div className="mt-2 text-xs text-slate-500">{sub}</div>}
    </div>
  );
}

function ClaimTable({ rows, expanded, setExpanded }) {
  if (rows.length === 0) {
    return (
      <p className="px-4 py-6 text-sm text-slate-500">No claims in this tier for the current filters.</p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[880px] text-left text-sm">
        <thead className="bg-slate-100 text-xs uppercase tracking-wide text-slate-500">
          <tr>
            <th className="px-4 py-3">Claim</th>
            <th className="px-4 py-3">Topic</th>
            <th className="px-4 py-3">Reach</th>
            <th className="px-4 py-3">Verdict</th>
            <th className="px-4 py-3">Source</th>
            <th className="px-4 py-3">Date</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {rows.map((row) => {
            const isOpen = expanded === row.post_id;
            return (
              <Fragment key={row.post_id}>
                <tr
                  onClick={() => setExpanded(isOpen ? null : row.post_id)}
                  className="cursor-pointer hover:bg-slate-50"
                >
                  <td className="max-w-md px-4 py-3">
                    <div className="truncate font-medium text-slate-800">{row.claim}</div>
                    <div className="mt-1 truncate text-xs text-slate-500">
                      @{row.username || "unknown"}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-slate-600">{row.topic || "-"}</td>
                  <td className="px-4 py-3 font-semibold tabular-nums text-slate-800">
                    {formatNumber(reach(row))}
                  </td>
                  <td className="px-4 py-3">{verdictBadge(row)}</td>
                  <td className="px-4 py-3 text-slate-600">{row.source || "-"}</td>
                  <td className="px-4 py-3 text-slate-500">
                    {formatDate(row.timestamp_processed || row.timestamp)}
                  </td>
                </tr>
                {isOpen && (
                  <tr className="bg-slate-50">
                    <td colSpan={6} className="px-4 py-4">
                      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_280px]">
                        <div>
                          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                            Full text
                          </p>
                          <p className="mt-1 whitespace-pre-wrap text-slate-800">{row.text}</p>
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
                          <div className="flex justify-between gap-4">
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
                              {row.confidence != null ? `${Math.round(row.confidence * 100)}%` : "-"}
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

  // If a filter/search wipes out the active tier, hop to one that has rows.
  useEffect(() => {
    if (tiers[activeTier]?.length > 0) return;
    const fallback = TIER_DEFS.find((t) => tiers[t.key].length > 0);
    if (fallback) setActiveTier(fallback.key);
  }, [tiers, activeTier]);

  return (
    <div>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold">Viral health misinformation monitor</h1>
          <p className="mt-1 max-w-2xl text-sm text-slate-600">
            Ranked social health claims from monitored sources, split by engagement tier.
          </p>
        </div>
        <div className="text-sm text-slate-500">{visible.length} claims shown</div>
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
          sub={loading ? "Loading" : `${WINDOWS.find(([k]) => k === window)?.[1] || window}`}
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
        <StatCard label="Top topic" value={topTopic} sub="By reach in this window" />
      </section>

      <section className="mt-6 flex flex-col gap-3 rounded-lg border border-slate-200 bg-white p-4 shadow-sm lg:flex-row lg:items-center">
        <select
          value={window}
          onChange={(e) => setWindow(e.target.value)}
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
          placeholder="Search claim, post, author..."
          className="min-w-0 flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:outline-none"
        />
      </section>

      {loading ? (
        <p className="mt-6 text-slate-500">Loading monitor...</p>
      ) : visible.length === 0 ? (
        <p className="mt-6 text-slate-500">
          No monitored social claims found for these filters.
        </p>
      ) : (
        <div className="mt-6 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
          <div className="flex flex-wrap gap-2 border-b border-slate-200 bg-slate-50 px-4 py-3">
            {TIER_DEFS.map((t) => {
              const count = tiers[t.key].length;
              const isActive = activeTier === t.key;
              return (
                <button
                  key={t.key}
                  onClick={() => setExpanded(null) || setActiveTier(t.key)}
                  disabled={count === 0}
                  className={[
                    "flex items-center gap-2 rounded-full px-3 py-1.5 text-sm font-medium transition-colors",
                    isActive
                      ? `${t.pill} text-white`
                      : count === 0
                        ? "cursor-not-allowed bg-white text-slate-300 ring-1 ring-slate-200"
                        : "bg-white text-slate-600 ring-1 ring-slate-200 hover:bg-slate-100",
                  ].join(" ")}
                >
                  <span className={`h-2 w-2 rounded-full ${isActive ? "bg-white" : t.dot}`} />
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
          <ClaimTable rows={tiers[activeTier]} expanded={expanded} setExpanded={setExpanded} />
        </div>
      )}
    </div>
  );
}