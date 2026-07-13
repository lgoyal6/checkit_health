// Renders the classifier verdict plus, when present, an existing fact check.
// Card accent color reflects confidence: green > 0.8, amber 0.7–0.8, slate below.
//
// The structured evidence report (rumor / confidence_level / summary /
// key_facts / analysis / conclusion) comes from a SEPARATE call to POST
// /report — see api.js:getReport(). It's not part of the /check response, so
// it's passed in as its own `report` prop, fetched on demand via
// `onLoadReport` (see CheckPage.jsx), rather than expected on `result`.
import { truthVerdict } from "../verdict.js";

// Plain-English name + one-line meaning for each machine label, so users don't
// have to decode MEDICAL_CLAIM / GENERAL_HEALTH / NOISE.
const LABELS = {
  MEDICAL_CLAIM: {
    name: "Medical claim",
    blurb: "A specific, checkable claim — worth fact-checking.",
  },
  GENERAL_HEALTH: {
    name: "General health",
    blurb: "Health-related, but too vague or generally-true to fact-check.",
  },
  NOISE: {
    name: "Not a health claim",
    blurb: "No checkable health claim here (opinion, slogan, or off-topic).",
  },
};

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

// confidence_level is "low" | "medium" | "high" — how settled the underlying
// science is, per the /report response. Distinct from the numeric
// `confidence` on the /check response (how sure the classifier is that this
// counts as a claim at all).
const EVIDENCE_STYLE = {
  low: { label: "Low", pct: 25, bar: "bg-red-500" },
  medium: { label: "Medium", pct: 60, bar: "bg-amber-500" },
  high: { label: "High", pct: 90, bar: "bg-green-500" },
};

function EvidenceMeter({ level }) {
  const style = EVIDENCE_STYLE[level];
  if (!style) return null;
  return (
    <div className="mt-4">
      <div className="text-xs font-medium text-slate-500">
        Evidence confidence: <span className="font-semibold text-slate-800">{style.label}</span>
      </div>
      <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-slate-200">
        <div className={`h-full rounded-full ${style.bar}`} style={{ width: `${style.pct}%` }} />
      </div>
    </div>
  );
}

export default function VerdictCard({ result, report, reportLoading, reportError, onLoadReport }) {
  const { label, claim, topic, confidence, reasoning } = result;
  const style = confidenceStyle(label, confidence);
  const meta = LABELS[label] || { name: label, blurb: "" };
  const pct = confidence != null ? `${Math.round(confidence * 100)}%` : null;
  const hasFactCheck = Boolean(result.fact_check_verdict);
  const verdict = hasFactCheck ? truthVerdict(result.fact_check_verdict) : null;
  // When a fact-check exists, the card accent should reflect the TRUTH (red for
  // false), not the claim-detection confidence — a green bar next to a false
  // claim is misleading.
  const accentBorder = verdict ? verdict.border : style.border;
  const searchUrl = `https://www.google.com/search?q=${encodeURIComponent(
    `fact check ${claim || ""}`,
  )}`;

  return (
    <div
      className={`rounded-xl border-l-4 ${accentBorder} bg-white p-5 shadow-sm`}
    >
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between sm:gap-3">
        <div>
          <span
            className={`rounded-full px-3 py-1 text-xs font-semibold ${style.chip}`}
          >
            {meta.name}
          </span>
          {meta.blurb && (
            <p className="mt-2 text-sm text-slate-500">{meta.blurb}</p>
          )}
        </div>
        {pct && (
          <div
            className="whitespace-nowrap sm:text-right"
            title="How sure the AI is that this is a checkable claim — NOT whether the claim is true."
          >
            <div className="text-sm text-slate-500">
              Claim confidence{" "}
              <span className="font-semibold text-slate-800">{pct}</span>
            </div>
            <div className="text-xs text-slate-400">
              is this a claim — not if it&apos;s true
            </div>
          </div>
        )}
      </div>

      {/* Rumor-as-question framing once the report has loaded; falls back to
          the plain claim sentence before that. */}
      {report?.rumor ? (
        <p className="mt-4 text-base font-medium text-slate-900">{report.rumor}</p>
      ) : (
        claim && <p className="mt-4 text-base font-medium text-slate-900">“{claim}”</p>
      )}

      {report?.confidence_level && <EvidenceMeter level={report.confidence_level} />}

      {topic && (
        <div className="mt-3">
          <span className="rounded bg-slate-100 px-2 py-0.5 text-xs uppercase tracking-wide text-slate-600">
            {topic}
          </span>
        </div>
      )}

      {reasoning && !report && (
        <p className="mt-3 text-sm italic text-slate-600">
          <span className="font-semibold not-italic text-slate-500">Why:</span>{" "}
          {reasoning}
        </p>
      )}

      {label === "MEDICAL_CLAIM" && result.falsifiable === false && (
        <p className="mt-3 text-sm text-amber-700">
          This reads more like an opinion than a checkable claim.
        </p>
      )}

      {/* Nudge vague/general statements toward a checkable claim, without
          blocking — the classifier already routed it here. */}
      {label === "GENERAL_HEALTH" && (
        <div className="mt-4 rounded-lg border border-blue-100 bg-blue-50 p-3 text-sm text-slate-700">
          <span className="font-semibold">Want a fact-check?</span> Make it a
          specific claim. Instead of “COVID is dangerous,” try “COVID-19 is
          deadlier than the flu” — something that could be proven true or false.
        </div>
      )}

      {/* On-demand evidence report — POST /report is a heavier call than
          /check, so it's fetched only when the user asks for it, not
          automatically on every check. */}
      {label === "MEDICAL_CLAIM" && (
        <div className="mt-4 border-t border-slate-100 pt-4">
          {!report && !reportLoading && (
            <button
              type="button"
              onClick={onLoadReport}
              className="text-sm font-medium text-blue-600 hover:underline"
            >
              Show full evidence report →
            </button>
          )}
          {reportLoading && (
            <p className="text-sm text-slate-500">Building the evidence report…</p>
          )}
          {reportError && (
            <p className="text-sm text-red-700">
              {reportError}{" "}
              <button
                type="button"
                onClick={onLoadReport}
                className="font-medium text-blue-600 hover:underline"
              >
                Try again
              </button>
            </p>
          )}
          {report && (
            <div className="space-y-4">
              {report.summary && (
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
                    Summary
                  </p>
                  <p className="mt-1 text-sm leading-relaxed text-slate-800">{report.summary}</p>
                </div>
              )}
              {report.key_facts?.length > 0 && (
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
                    Key facts
                  </p>
                  <ol className="mt-1 list-decimal space-y-1 pl-5 text-sm leading-relaxed text-slate-800">
                    {report.key_facts.map((fact, i) => (
                      <li key={i}>{fact}</li>
                    ))}
                  </ol>
                </div>
              )}
              {report.analysis && (
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
                    Analysis
                  </p>
                  <p className="mt-1 text-sm leading-relaxed text-slate-800">{report.analysis}</p>
                </div>
              )}
              {report.conclusion && (
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
                    Conclusion
                  </p>
                  <p className="mt-1 text-sm leading-relaxed text-slate-800">{report.conclusion}</p>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Fact-check block — this is the "is it TRUE?" answer, separate from the
          claim-confidence score above. */}
      {label === "MEDICAL_CLAIM" && (
        <div className="mt-4 border-t border-slate-100 pt-4">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
            Is it true? — what fact-checkers say
          </p>
          {hasFactCheck ? (
            <div className="mt-2">
              <span
                className={`inline-block rounded-md px-3 py-1 text-sm font-bold uppercase tracking-wide ring-1 ${verdict.cls}`}
              >
                {verdict.label}
              </span>
              <p className="mt-2 text-sm text-slate-800">
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
              <p className="mt-2 text-xs text-slate-400">
                This is the closest published fact-check we found — open it to
                confirm it matches your exact claim.
              </p>
            </div>
          ) : (
            <div className="mt-1">
              <p className="text-sm text-slate-600">
                No published fact-check exists for this yet, so it&apos;s{" "}
                <span className="font-medium">flagged for review.</span> That
                doesn&apos;t mean it&apos;s true or false — just unchecked.
              </p>
              {claim && (
                <a
                  href={searchUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mt-1 inline-block text-sm font-medium text-blue-600 hover:underline"
                >
                  Search the web for this claim →
                </a>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}