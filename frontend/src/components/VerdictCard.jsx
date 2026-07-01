// Renders the classifier verdict plus, when present, an existing fact check.
// Card accent color reflects confidence: green > 0.8, amber 0.7–0.8, slate below.

function confidenceStyle(label, confidence) {
  if (label !== "MEDICAL_CLAIM" || confidence == null) {
    return { border: "border-slate-300", chip: "bg-slate-200 text-slate-700" };
  }
  if (confidence > 0.8) {
    return { border: "border-green-500", chip: "bg-green-100 text-green-800" };
  }
  if (confidence >= 0.7) {
    return { border: "border-amber-500", chip: "bg-amber-100 text-amber-800" };
  }
  return { border: "border-slate-300", chip: "bg-slate-200 text-slate-700" };
}

export default function VerdictCard({ result }) {
  const { label, claim, topic, confidence } = result;
  const style = confidenceStyle(label, confidence);
  const pct = confidence != null ? `${Math.round(confidence * 100)}%` : "—";
  const hasFactCheck = Boolean(result.fact_check_verdict);

  return (
    <div
      className={`rounded-xl border-l-4 ${style.border} bg-white p-5 shadow-sm`}
    >
      <div className="flex items-center justify-between">
        <span
          className={`rounded-full px-3 py-1 text-xs font-semibold ${style.chip}`}
        >
          {label}
        </span>
        <span className="text-sm text-slate-500">
          Confidence <span className="font-semibold text-slate-800">{pct}</span>
        </span>
      </div>

      {claim && (
        <p className="mt-4 text-base font-medium text-slate-900">“{claim}”</p>
      )}

      {topic && (
        <div className="mt-3">
          <span className="rounded bg-slate-100 px-2 py-0.5 text-xs uppercase tracking-wide text-slate-600">
            {topic}
          </span>
        </div>
      )}

      {label === "MEDICAL_CLAIM" && result.falsifiable === false && (
        <p className="mt-3 text-sm text-amber-700">
          This reads more like an opinion than a checkable claim.
        </p>
      )}

      {/* Fact-check block — issue #9 */}
      <div className="mt-4 border-t border-slate-100 pt-4">
        {hasFactCheck ? (
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              Existing fact check
            </p>
            <p className="mt-1 text-sm text-slate-800">
              <span className="font-semibold">
                {result.fact_check_source || "Unknown publisher"}:
              </span>{" "}
              {result.fact_check_verdict}
            </p>
            {result.fact_check_url && (
              <a
                href={result.fact_check_url}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-1 inline-block text-sm font-medium text-blue-600 hover:underline"
              >
                Read the full fact check →
              </a>
            )}
          </div>
        ) : (
          <p className="text-sm text-slate-500">
            No existing fact check found — flagged for review.
          </p>
        )}
      </div>
    </div>
  );
}
