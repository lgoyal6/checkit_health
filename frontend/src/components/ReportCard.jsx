// Full evidence report for a claim: Rumor / Confidence Level / Summary /
// Key Facts / Analysis / Conclusion. Mirrors the structure returned by the
// /report endpoint (see claim_report.py).

const CONFIDENCE = {
  low: { label: "Low", pct: 33, bar: "bg-sky-400", text: "text-slate-700" },
  medium: {
    label: "Medium",
    pct: 66,
    bar: "bg-sky-500",
    text: "text-slate-700",
  },
  high: { label: "High", pct: 100, bar: "bg-sky-600", text: "text-slate-700" },
};

function ReportSkeleton() {
  return (
    <div className="mt-4 animate-pulse space-y-4 rounded-xl border border-slate-200 bg-white p-5">
      <div className="h-5 w-3/4 rounded bg-slate-200" />
      <div className="h-3 w-1/3 rounded bg-slate-200" />
      <div className="h-2 w-full rounded bg-slate-200" />
      <div className="space-y-2 pt-2">
        <div className="h-3 w-full rounded bg-slate-100" />
        <div className="h-3 w-5/6 rounded bg-slate-100" />
        <div className="h-3 w-2/3 rounded bg-slate-100" />
      </div>
    </div>
  );
}

export default function ReportCard({ report, loading, error, onRetry }) {
  if (loading) return <ReportSkeleton />;

  if (error) {
    return (
      <div className="mt-4 rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
        <p>{error}</p>
        {onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="mt-2 font-medium text-red-800 underline hover:no-underline"
          >
            Try generating the report again
          </button>
        )}
      </div>
    );
  }

  if (!report) return null;

  const conf = CONFIDENCE[report.confidence_level] || CONFIDENCE.low;
  const evidenceById = Object.fromEntries(
    (report.evidence || []).map((item) => [item.id, item]),
  );

  return (
    <div className="mt-4 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
      <p className="border-b border-amber-100 bg-amber-50 px-5 py-3 text-xs leading-relaxed text-amber-900">
        AI-generated analyst summary — not medical advice or a substitute for
        reviewing authoritative sources. A published fact-check, when linked
        above, is the source to verify.
      </p>
      {/* Rumor header */}
      <div className="border-l-4 border-sky-500 bg-sky-50 p-5">
        <p className="text-base font-bold text-slate-900">
          <span className="text-slate-500">Rumor:</span> {report.rumor}
        </p>

        <div className="mt-4">
          <p className={`text-sm font-medium ${conf.text}`}>
            Confidence Level:{" "}
            <span className="font-semibold">{conf.label}</span>
          </p>
          <div className="mt-1.5 h-2 w-full overflow-hidden rounded-full bg-slate-200">
            <div
              className={`h-full rounded-full ${conf.bar}`}
              style={{ width: `${conf.pct}%` }}
            />
          </div>
        </div>
      </div>

      <div className="divide-y divide-slate-100">
        <section className="p-5">
          <h3 className="text-sm font-bold uppercase tracking-wide text-slate-800">
            Evidence result
          </h3>
          <span className="mt-2 inline-block rounded-full bg-slate-100 px-3 py-1 text-sm font-semibold capitalize text-slate-700">
            {report.evidence_state?.replace("_", " ") || "insufficient"}
          </span>
        </section>
        {report.summary && (
          <section className="p-5">
            <h3 className="text-sm font-bold uppercase tracking-wide text-slate-800">
              Summary
            </h3>
            <p className="mt-2 text-sm leading-relaxed text-slate-700">
              {report.summary}
            </p>
          </section>
        )}

        {report.key_facts?.length > 0 && (
          <section className="p-5">
            <h3 className="text-sm font-bold uppercase tracking-wide text-slate-800">
              Key Facts
            </h3>
            <ol className="mt-2 overflow-hidden rounded-lg border border-slate-100">
              {report.key_facts.map((fact, i) => (
                <li
                  key={i}
                  className={`flex gap-3 px-4 py-3 text-sm text-slate-700 ${
                    i % 2 === 0 ? "bg-slate-50" : "bg-white"
                  }`}
                >
                  <span className="font-semibold text-slate-400">{i + 1}.</span>
                  <span>{fact}</span>
                </li>
              ))}
            </ol>
          </section>
        )}

        {report.analysis && (
          <section className="p-5">
            <h3 className="text-sm font-bold uppercase tracking-wide text-slate-800">
              Analysis
            </h3>
            <p className="mt-2 text-sm leading-relaxed text-slate-700">
              {report.analysis}
            </p>
          </section>
        )}

        {report.conclusion && (
          <section className="p-5">
            <h3 className="text-sm font-bold uppercase tracking-wide text-slate-800">
              Conclusion
            </h3>
            <p className="mt-2 text-sm leading-relaxed text-slate-700">
              {report.conclusion}
            </p>
          </section>
        )}
        {report.citations?.length > 0 && (
          <section className="p-5">
            <h3 className="text-sm font-bold uppercase tracking-wide text-slate-800">
              Sources cited
            </h3>
            <ul className="mt-3 space-y-3">
              {report.citations.map((id) => {
                const item = evidenceById[id];
                if (!item) return null;
                return (
                  <li key={id} className="text-sm">
                    <a
                      href={item.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="font-medium text-blue-700 hover:underline"
                    >
                      {item.title}
                    </a>
                    <p className="mt-1 text-xs text-slate-500">
                      {item.publisher} · {item.published_at || "Date unavailable"} ·
                      relevance {Math.round((item.relevance_score || 0) * 100)}%
                    </p>
                    <blockquote className="mt-1 border-l-2 border-slate-200 pl-3 text-xs text-slate-600">
                      {item.passage}
                    </blockquote>
                  </li>
                );
              })}
            </ul>
          </section>
        )}
      </div>
    </div>
  );
}
