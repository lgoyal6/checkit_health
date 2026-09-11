import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { exportUrl, getMeta, getNarratives } from "../api.js";
import {
  ClusteringNotice,
  LifecycleBadge,
  formatNumber,
} from "../components/Signals.jsx";

// Ordered by how urgently a state wants an analyst's attention rather than
// alphabetically, so the filter row reads like the triage priority it is.
const STATES = [
  "emerging",
  "accelerating",
  "peaking",
  "watching",
  "declining",
  "dormant",
];

const SORTS = [
  ["reach", "Total reach"],
  ["growth", "Posts (spread)"],
  ["recent", "Most recent"],
  ["unreviewed", "Least reviewed"],
];

function sortValue(row, key) {
  switch (key) {
    case "growth":
      return (
        Number(row.post_count || 0) * 1000 + Number(row.platform_count || 0)
      );
    case "recent":
      return new Date(row.last_seen_at || 0).getTime() || 0;
    case "unreviewed":
      return Number(row.post_count || 0) - Number(row.reviewed_count || 0);
    case "reach":
    default:
      return Number(row.reach || 0);
  }
}

export default function NarrativeQueue() {
  const [rows, setRows] = useState([]);
  const [meta, setMeta] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [state, setState] = useState("");
  const [query, setQuery] = useState("");
  const [sortBy, setSortBy] = useState("reach");

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    Promise.all([
      getNarratives({ limit: 200, lifecycleState: state }),
      getMeta(),
    ])
      .then(([narratives, settings]) => {
        if (!active) return;
        setRows(Array.isArray(narratives) ? narratives : []);
        setMeta(settings);
      })
      .catch((err) => active && setError(err.message))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [state]);

  const visible = useMemo(() => {
    const term = query.trim().toLowerCase();
    const filtered = term
      ? rows.filter((row) =>
          [row.label, row.canonical_claim, row.topic]
            .filter(Boolean)
            .some((value) => String(value).toLowerCase().includes(term)),
        )
      : rows;
    return [...filtered].sort(
      (a, b) => sortValue(b, sortBy) - sortValue(a, sortBy),
    );
  }, [rows, query, sortBy]);

  const totals = useMemo(
    () => ({
      reach: rows.reduce((sum, r) => sum + Number(r.reach || 0), 0),
      posts: rows.reduce((sum, r) => sum + Number(r.post_count || 0), 0),
      unreviewed: rows.reduce(
        (sum, r) =>
          sum +
          Math.max(
            0,
            Number(r.post_count || 0) - Number(r.reviewed_count || 0),
          ),
        0,
      ),
      hot: rows.filter((r) =>
        ["emerging", "accelerating"].includes(r.lifecycle_state),
      ).length,
    }),
    [rows],
  );

  return (
    <div>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold">Narrative queue</h1>
          <p className="mt-1 max-w-2xl text-sm text-slate-600">
            One row per rumor, not per post. A narrative groups every post
            carrying the same claim so you track the thing that spreads rather
            than the copies of it.
          </p>
        </div>
        <a
          href={exportUrl("narratives")}
          className="self-start rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-100"
        >
          Export CSV
        </a>
      </div>

      {error && (
        <div className="mt-6 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          {error}
        </div>
      )}

      <ClusteringNotice clustering={meta?.clustering} />

      <section className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {[
          [
            "Narratives tracked",
            formatNumber(rows.length),
            `${totals.posts} posts`,
          ],
          [
            "Emerging or accelerating",
            formatNumber(totals.hot),
            "worth looking at now",
          ],
          ["Total reach", formatNumber(totals.reach), "likes plus reposts"],
          [
            "Posts awaiting review",
            formatNumber(totals.unreviewed),
            "across all narratives",
          ],
        ].map(([label, value, sub]) => (
          <div
            key={label}
            className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm"
          >
            <div className="text-2xl font-semibold tabular-nums text-slate-900">
              {value}
            </div>
            <div className="mt-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
              {label}
            </div>
            <div className="mt-2 text-xs text-slate-500">{sub}</div>
          </div>
        ))}
      </section>

      <section className="mt-6 flex flex-col gap-3 rounded-lg border border-slate-200 bg-white p-4 shadow-sm lg:flex-row lg:items-center">
        <select
          value={state}
          onChange={(e) => setState(e.target.value)}
          aria-label="Filter by lifecycle state"
          className="rounded-md border border-slate-300 px-3 py-2 text-sm"
        >
          <option value="">All states</option>
          {STATES.map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </select>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Search narratives"
          placeholder="Search rumors..."
          className="min-w-0 flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm"
        />
        <select
          value={sortBy}
          onChange={(e) => setSortBy(e.target.value)}
          aria-label="Sort narratives"
          className="rounded-md border border-slate-300 px-3 py-2 text-sm"
        >
          {SORTS.map(([value, label]) => (
            <option key={value} value={value}>
              Sort: {label}
            </option>
          ))}
        </select>
      </section>

      {loading ? (
        <p className="mt-6 text-slate-500" role="status">
          Loading narratives...
        </p>
      ) : visible.length === 0 ? (
        <p className="mt-6 text-slate-500">
          No narratives yet. Run the ingestion pipeline, or check a claim on the
          Check page and it will be folded into the ledger.
        </p>
      ) : (
        <div className="mt-6 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[860px] text-left text-sm">
              <thead className="bg-slate-100 text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-4 py-3">Narrative</th>
                  <th className="px-4 py-3">State</th>
                  <th className="px-4 py-3 text-right">Reach</th>
                  <th className="px-4 py-3 text-right">Posts</th>
                  <th className="px-4 py-3 text-right">Platforms</th>
                  <th className="px-4 py-3 text-right">Reviewed</th>
                  <th className="px-4 py-3" />
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {visible.map((row) => {
                  const pending =
                    Number(row.post_count || 0) -
                    Number(row.reviewed_count || 0);
                  return (
                    <tr key={row.narrative_id} className="hover:bg-slate-50">
                      <td className="max-w-md px-4 py-3">
                        <Link
                          to={`/narratives/${encodeURIComponent(row.narrative_id)}`}
                          className="font-medium text-slate-800 hover:text-blue-700 hover:underline"
                        >
                          {row.label}
                        </Link>
                        <div className="mt-0.5 text-xs text-slate-500">
                          {row.topic || "untopiced"} &middot; first seen{" "}
                          {(row.first_seen_at || "").slice(0, 10)}
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <LifecycleBadge state={row.lifecycle_state} />
                      </td>
                      <td className="px-4 py-3 text-right font-semibold tabular-nums text-slate-800">
                        {formatNumber(row.reach)}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums text-slate-600">
                        {row.post_count}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums text-slate-600">
                        {row.platform_count}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums">
                        {pending > 0 ? (
                          <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-semibold text-amber-900">
                            {pending} pending
                          </span>
                        ) : (
                          <span className="text-xs text-slate-400">done</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <Link
                          to={`/narratives/${encodeURIComponent(row.narrative_id)}`}
                          className="whitespace-nowrap rounded-md bg-slate-900 px-3 py-1.5 text-xs font-semibold text-white hover:bg-slate-700"
                        >
                          Open
                        </Link>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
