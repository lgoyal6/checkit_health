// Boil a fact-checker's free-text rating ("False", "Flawed Paper", a whole
// sentence…) down to one prominent verdict, shared by the check card and the
// history table so they read consistently.
export function truthVerdict(rating) {
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
      label: "Likely false",
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
      label: "Misleading",
      cls: "bg-amber-100 text-amber-800 ring-amber-200",
      border: "border-amber-500",
    };
  }
  if (has(["mostly true", "accurate", "correct", "confirmed", "is true"])) {
    return {
      label: "Likely true",
      cls: "bg-green-100 text-green-800 ring-green-200",
      border: "border-green-500",
    };
  }
  return {
    label: "See fact-check",
    cls: "bg-slate-100 text-slate-700 ring-slate-200",
    border: "border-slate-300",
  };
}
