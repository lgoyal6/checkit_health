// Renders the classifier verdict plus, when present, an existing fact check.
// Card accent color reflects confidence: green > 0.8, amber 0.7–0.8, slate below.

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

// Fact-checkers return free-text ratings ("False", "Flawed Paper", a whole
// sentence…). Boil it down to one prominent verdict so the user instantly sees
// whether the claim is true — separate from the claim-confidence score.
function truthVerdict(rating) {
  const t = (rating || "").toLowerCase();
  const has = (words) => words.some((w) => t.includes(w));
  if (
    has([
      "false",
      "untrue",
      "debunk",
      "no evidence",
      "no data",
      "no link",
      "no scientific",
      "incorrect",
      "myth",
      "hoax",
      "fake",
      "misinformation",
      "baseless",
      "unfounded",
      "not true",
      "pants on fire",
    ])
  ) {
    return {
      label: "Likely FALSE",
      cls: "bg-red-100 text-red-800 ring-red-200",
      border: "border-red-500",
    };
  }
  if (
    has([
      "misleading",
      "mixture",
      "partly",
      "partially",
      "exaggerat",
      "needs context",
      "lacks context",
      "out of context",
      "flawed",
      "unproven",
      "unverified",
      "disputed",
    ])
  ) {
    return {
      label: "Misleading / disputed",
      cls: "bg-amber-100 text-amber-800 ring-amber-200",
      border: "border-amber-500",
    };
  }
  if (has(["mostly true", "accurate", "correct", "confirmed", "is true"])) {
    return {
      label: "Likely TRUE",
      cls: "bg-green-100 text-green-800 ring-green-200",
      border: "border-green-500",
    };
  }
  return {
    label: "See the fact-check",
    cls: "bg-slate-100 text-slate-700 ring-slate-200",
    border: "border-slate-300",
  };
}

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
      <div className="flex items-start justify-between gap-3">
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
            className="whitespace-nowrap text-right"
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
