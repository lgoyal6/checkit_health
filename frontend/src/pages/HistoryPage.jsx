import { Fragment, useEffect, useMemo, useState } from "react";
import { getHistory } from "../api.js";
import { truthVerdict } from "../verdict.js";

function formatDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString();
}

function confidenceColor(c) {
  if (c == null) return "text-slate-500";
  if (c > 0.8) return "text-green-700";
  if (c >= 0.7) return "text-amber-700";
  return "text-slate-600";
}

export default function HistoryPage() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("all");
  const [expanded, setExpanded] = useState(null);

  useEffect(() => {
    getHistory()
      .then(setRows)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  const visible = useMemo(() => {
    if (filter === "all") return rows;
    return rows.filter((r) => r.status === filter);
  }, [rows, filter]);

  if (loading) return <p className="text-slate-500">Loading history…</p>;
  if (error)
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
        {error}
      </div>
    );

  const emptyMessage =
    filter === "verified"
      ? "No claims have a matching published fact-check yet. Most flagged claims are too new or niche for fact-checkers to have covered."
      : filter === "unverified"
        ? "Nothing here — every stored claim already has a matching fact-check."
        : "No claims stored yet. Check a claim on the Check page and it'll appear here.";

  return (
    <div>
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-2xl font-bold">Claim history</h1>
        <select
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="rounded-lg border border-slate-300 px-3 py-2 text-sm shadow-sm focus:outline-none"
        >
          <option value="all">All claims</option>
          <option value="verified">Fact-checked</option>
          <option value="unverified">Needs review</option>
        </select>
      </div>

      <p className="mt-2 text-sm text-slate-500">
        Every medical claim checked here is saved.{" "}
        <span className="font-medium text-slate-600">Fact-checked</span> means a
        published fact-check was found;{" "}
        <span className="font-medium text-slate-600">needs review</span> means
        none exists yet.
      </p>

      {visible.length === 0 ? (
        <p className="mt-6 text-slate-500">{emptyMessage}</p>
      ) : (
        <div className="mt-6 overflow-hidden rounded-lg border border-slate-200 bg-white">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-100 text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-4 py-3">Claim</th>
                <th className="px-4 py-3">Topic</th>
                <th className="px-4 py-3">Confidence</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">Date</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {visible.map((r) => {
                const isOpen = expanded === r.post_id;
                return (
                  <Fragment key={r.post_id}>
                    <tr
                      onClick={() => setExpanded(isOpen ? null : r.post_id)}
                      className="cursor-pointer hover:bg-slate-50"
                    >
                      <td className="max-w-xs truncate px-4 py-3 font-medium text-slate-800">
                        {r.claim}
                      </td>
                      <td className="px-4 py-3 text-slate-600">
                        {r.topic || "—"}
                      </td>
                      <td
                        className={`px-4 py-3 font-semibold ${confidenceColor(r.confidence)}`}
                      >
                        {r.confidence != null
                          ? `${Math.round(r.confidence * 100)}%`
                          : "—"}
                      </td>
                      <td className="px-4 py-3">
                        {r.fact_check_verdict ? (
                          <span
                            className={`rounded-full px-2 py-0.5 text-xs font-semibold ring-1 ${truthVerdict(r.fact_check_verdict).cls}`}
                          >
                            {truthVerdict(r.fact_check_verdict).label}
                          </span>
                        ) : (
                          <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">
                            Needs review
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-slate-500">
                        {formatDate(r.timestamp_processed)}
                      </td>
                    </tr>
                    {isOpen && (
                      <tr className="bg-slate-50">
                        <td colSpan={5} className="px-4 py-4">
                          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                            Full text
                          </p>
                          <p className="mt-1 whitespace-pre-wrap text-slate-800">
                            {r.text}
                          </p>
                          {r.fact_check_verdict && (
                            <p className="mt-3 text-sm text-slate-700">
                              <span className="font-semibold">
                                {r.fact_check_source || "Fact check"}:
                              </span>{" "}
                              {r.fact_check_verdict}
                            </p>
                          )}
                          {r.fact_check_url && (
                            <a
                              href={r.fact_check_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="mt-1 inline-block text-sm font-medium text-blue-600 hover:underline"
                            >
                              Read the full fact check →
                            </a>
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
