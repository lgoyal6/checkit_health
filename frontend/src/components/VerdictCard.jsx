// Renders the classifier verdict plus, when present, an existing fact check.
// Card accent color reflects confidence: green > 0.8, amber 0.7–0.8, slate below.
import { truthVerdict } from "../verdict.js";
import ReportCard from "./ReportCard.jsx";

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

export default function VerdictCard({
  result,
  report,
  reportLoading,
  reportError,
  onRetryReport,
}) {
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
  const assessment = result.assessment;

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

      {reasoning && (
        <p className="mt-3 text-sm italic text-slate-600">
          <span className="font-semibold not-italic text-slate-500">Why:</span>{" "}
          {reasoning}
        </p>
      )}

      {assessment && (
        <section className="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              Analyst signals
            </span>
            <span className="rounded-full bg-white px-2 py-1 text-xs text-slate-600 ring-1 ring-slate-200">
              Evidence: {assessment.evidence_quality.level}
            </span>
            <span className="rounded-full bg-white px-2 py-1 text-xs text-slate-600 ring-1 ring-slate-200">
              Language: {assessment.language.name}
            </span>
            <span className="rounded-full bg-white px-2 py-1 text-xs text-slate-600 ring-1 ring-slate-200">
              Cluster {assessment.cluster_id}
            </span>
          </div>
          <p className="mt-2 text-xs leading-relaxed text-slate-600">
            {assessment.evidence_quality.reason}
          </p>
          <p className="mt-2 text-xs font-medium text-slate-700">
            Next step: {assessment.response_guidance}
          </p>
          {assessment.escalation.human_review_required && (
            <p className="mt-2 rounded-md bg-amber-100 px-3 py-2 text-xs font-medium text-amber-900">
              Human review recommended:{" "}
              {assessment.escalation.reasons.join(", ")}.
            </p>
          )}
          {assessment.adverse_event.detected && (
            <p className="mt-2 text-xs text-rose-700">
              Possible adverse-event language detected and routed for review.
              This is not a diagnosis or emergency assessment.
            </p>
          )}
          <details className="mt-3 text-xs text-slate-500">
            <summary className="cursor-pointer font-medium text-slate-600">
              Method and limitations
            </summary>
            <p className="mt-2 leading-relaxed">
              Signals use deterministic rules around the disclosed AI
              classification. Coordination is not inferred from a single post,
              translation is not silently applied, and final judgment remains
              with a qualified person.
            </p>
          </details>
        </section>
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

      {/* Quick pointer to an existing published fact-check, if we have one —
          kept short since the full evidence report below covers the rest. */}
      {label === "MEDICAL_CLAIM" && hasFactCheck && (
        <div className="mt-4 border-t border-slate-100 pt-4">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
            Closest published fact-check
          </p>
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
              Open it to confirm it matches your exact claim.
            </p>
          </div>
        </div>
      )}

      {label === "MEDICAL_CLAIM" &&
        !hasFactCheck &&
        !reportLoading &&
        !report && (
          <div className="mt-4 border-t border-slate-100 pt-4">
            <p className="text-sm text-slate-600">
              No published fact-check exists for this yet. That doesn&apos;t
              mean it&apos;s true or false — just unchecked.
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

      {/* Full evidence report — Rumor / Confidence Level / Summary / Key
          Facts / Analysis / Conclusion, generated by the /report endpoint. */}
      {label === "MEDICAL_CLAIM" && (
        <ReportCard
          report={report}
          loading={reportLoading}
          error={reportError}
          onRetry={onRetryReport}
        />
      )}
    </div>
  );
}
