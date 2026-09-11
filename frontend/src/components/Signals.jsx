// Shared display pieces for the analyst-facing signals.
//
// The priority score and the lifecycle state were both computed on the server
// and never rendered. They are the two things that turn a table of claims into
// a work queue, so they get real components rather than a line of text.

export const LIFECYCLE = {
  emerging: {
    label: "Emerging",
    cls: "bg-amber-100 text-amber-900 ring-amber-200",
    hint: "New and growing. Still early enough to pre-bunk the tactic.",
  },
  accelerating: {
    label: "Accelerating",
    cls: "bg-red-100 text-red-900 ring-red-200",
    hint: "Growing faster than it was. Most people have seen it.",
  },
  peaking: {
    label: "Peaking",
    cls: "bg-orange-100 text-orange-900 ring-orange-200",
    hint: "Still growing, but the rate is easing off.",
  },
  declining: {
    label: "Declining",
    cls: "bg-slate-100 text-slate-600 ring-slate-200",
    hint: "Fading. Responding now tends to revive it.",
  },
  dormant: {
    label: "Dormant",
    cls: "bg-slate-100 text-slate-500 ring-slate-200",
    hint: "No new activity observed recently.",
  },
  watching: {
    label: "Watching",
    cls: "bg-blue-100 text-blue-900 ring-blue-200",
    hint: "Too little movement so far to call a trend.",
  },
};

export const EVIDENCE_STATE = {
  contradicted: "bg-red-100 text-red-900 ring-red-200",
  mixed: "bg-amber-100 text-amber-900 ring-amber-200",
  supported: "bg-green-100 text-green-900 ring-green-200",
  retrieved: "bg-blue-100 text-blue-900 ring-blue-200",
  insufficient: "bg-slate-100 text-slate-600 ring-slate-200",
};

export const REVIEW_STATUSES = [
  ["accepted", "Accept"],
  ["rejected", "Reject"],
  ["needs_evidence", "Needs evidence"],
  ["in_review", "In review"],
];

export function formatNumber(n) {
  return new Intl.NumberFormat().format(Number(n || 0));
}

export function LifecycleBadge({ state }) {
  const meta = LIFECYCLE[state] || LIFECYCLE.watching;
  return (
    <span
      title={meta.hint}
      className={`whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-semibold ring-1 ${meta.cls}`}
    >
      {meta.label}
    </span>
  );
}

export function EvidenceBadge({ state }) {
  const cls = EVIDENCE_STATE[state] || EVIDENCE_STATE.insufficient;
  return (
    <span
      title="What retrieved sources say about this claim, not how confident the model is."
      className={`whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ${cls}`}
    >
      {String(state || "insufficient").replace(/_/g, " ")}
    </span>
  );
}

const COMPONENT_LABELS = {
  reach: "Reach",
  potential_harm: "Harm",
  uncertainty: "Uncertainty",
};

// The score is only useful if an analyst can see what drove it. Showing the
// three components as bars means "why is this at the top" is answerable at a
// glance instead of requiring trust in a single number.
export function PriorityMeter({ priority, compact = false }) {
  if (!priority) return <span className="text-xs text-slate-400">-</span>;
  const {
    score,
    components = {},
    weights = {},
    weights_version: version,
  } = priority;

  if (compact) {
    return (
      <div className="flex items-center gap-2">
        <span className="w-8 text-right text-sm font-semibold tabular-nums text-slate-900">
          {score}
        </span>
        <div className="flex h-1.5 w-16 gap-px overflow-hidden rounded-full bg-slate-100">
          {Object.entries(components).map(([key, value]) => (
            <div
              key={key}
              title={`${COMPONENT_LABELS[key] || key}: ${value}`}
              style={{ width: `${Math.max(2, (weights[key] || 0.33) * 100)}%` }}
              className="h-full bg-slate-200"
            >
              <div
                style={{ height: `${Math.round(Number(value) * 100)}%` }}
                className="w-full bg-slate-700"
              />
            </div>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-baseline gap-2">
        <span className="text-2xl font-semibold tabular-nums text-slate-900">
          {score}
        </span>
        <span className="text-xs text-slate-500">review priority / 100</span>
      </div>
      <div className="mt-2 space-y-1.5">
        {Object.entries(components).map(([key, value]) => (
          <div key={key} className="flex items-center gap-2">
            <span className="w-20 text-xs text-slate-500">
              {COMPONENT_LABELS[key] || key}
            </span>
            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-100">
              <div
                className="h-full rounded-full bg-slate-700"
                style={{ width: `${Math.round(Number(value) * 100)}%` }}
              />
            </div>
            <span className="w-16 text-right text-xs tabular-nums text-slate-500">
              {Number(value).toFixed(2)} &times; {weights[key] ?? "-"}
            </span>
          </div>
        ))}
      </div>
      <p className="mt-2 text-xs text-slate-400">
        Priority for review, not a judgment that the claim is false. Weights:{" "}
        <code>{version}</code>.
      </p>
    </div>
  );
}

// Inline triage. Review used to live only on the History page, which reads the
// last 50 rows, so an analyst could not act from the page that actually ranks
// claims. Putting the decision next to the claim is what makes the feedback
// loop collect anything.
export function ReviewButtons({ value, onChoose, disabled, busy }) {
  return (
    <div className="flex flex-wrap gap-1">
      {REVIEW_STATUSES.map(([status, label]) => {
        const active = value === status;
        return (
          <button
            key={status}
            type="button"
            disabled={disabled || busy}
            aria-pressed={active}
            onClick={() => onChoose(status)}
            className={[
              "rounded-md px-2 py-1 text-xs font-semibold transition-colors",
              "disabled:cursor-not-allowed disabled:opacity-50",
              active
                ? "bg-slate-900 text-white"
                : "bg-white text-slate-600 ring-1 ring-slate-300 hover:bg-slate-100",
            ].join(" ")}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}

// An inline SVG so a reach curve costs no chart library and no extra request.
export function Sparkline({ points = [], width = 140, height = 28 }) {
  if (!points || points.length < 2) {
    return <span className="text-xs text-slate-400">not enough data</span>;
  }
  const values = points.map((p) => Math.max(0, Number(p.reach) || 0));
  const peak = Math.max(...values) || 1;
  const step = width / (values.length - 1);
  const coords = values
    .map(
      (v, i) =>
        `${(i * step).toFixed(1)},${(height - (v / peak) * (height - 3) - 1.5).toFixed(1)}`,
    )
    .join(" ");
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      role="img"
      aria-label="Reach over time"
      className="overflow-visible"
    >
      <polyline
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
        points={coords}
      />
    </svg>
  );
}

// Says plainly when grouping is running on the offline embedding. That path
// scores 0.57 AUC against the labeled pair set, which is barely better than
// chance, so presenting its output as semantic narrative detection would be a
// lie the interface tells on the system's behalf.
export function ClusteringNotice({ clustering }) {
  if (!clustering || clustering.semantic) return null;
  return (
    <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
      <span className="font-semibold">Grouping is limited right now.</span>{" "}
      {clustering.note} Narratives below merge only near-identical wording, so
      the same rumor may appear more than once.
    </div>
  );
}
