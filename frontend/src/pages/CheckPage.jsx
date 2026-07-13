import { useEffect, useState } from "react";
import { checkClaim, getReport, warmUp } from "../api.js";
import VerdictCard from "../components/VerdictCard.jsx";

const EXAMPLES = [
  "Ivermectin cures COVID-19 in 48 hours.",
  "The MMR vaccine causes autism.",
  "Drinking celery juice every morning reverses arthritis.",
  "5G towers spread the coronavirus.",
];

export default function CheckPage() {
  const [text, setText] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);

  // Full evidence report (Rumor/Confidence/Summary/Key Facts/Analysis/
  // Conclusion), fetched separately from /check since it's a heavier call
  // that should only run once we know we have a real medical claim.
  const [report, setReport] = useState(null);
  const [reportLoading, setReportLoading] = useState(false);
  const [reportError, setReportError] = useState("");

  // Wake the free-tier backend on load so the first check isn't a cold start.
  useEffect(() => {
    warmUp();
  }, []);

  function clearAll() {
    setText("");
    setResult(null);
    setError("");
    setReport(null);
    setReportLoading(false);
    setReportError("");
  }

  async function fetchReport(data) {
    setReport(null);
    setReportError("");
    setReportLoading(true);
    try {
      const claimText = data.claim || text;
      const rep = await getReport({
        claim: claimText,
        topic: data.topic,
        factCheckVerdict: data.fact_check_verdict,
        factCheckSource: data.fact_check_source,
      });
      setReport(rep);
    } catch (err) {
      setReportError(
        err.message ||
          "Couldn't generate the evidence report. Please try again.",
      );
    } finally {
      setReportLoading(false);
    }
  }

  async function runCheck(claimText) {
    if (!claimText.trim() || loading) return;
    setLoading(true);
    setError("");
    setResult(null);
    setReport(null);
    setReportError("");
    try {
      const data = await checkClaim(claimText.trim());
      setResult(data);
      if (data.label === "MEDICAL_CLAIM") {
        fetchReport(data);
      }
    } catch (err) {
      setError(err.message || "Something went wrong. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  function onSubmit(e) {
    e.preventDefault();
    runCheck(text);
  }

  function useExample(ex) {
    setText(ex);
    runCheck(ex);
  }

  return (
    <div className="max-w-3xl">
      <h1 className="text-2xl font-bold">Check a health claim</h1>
      <p className="mt-1 text-slate-600">
        Paste a social-media post or statement about health, and our AI will
        tell you whether it&apos;s a specific medical claim worth fact-checking.
      </p>

      <form onSubmit={onSubmit} className="mt-6">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={loading}
          rows={5}
          placeholder="e.g. Ivermectin cures COVID-19 in 48 hours…"
          className="w-full resize-y rounded-lg border border-slate-300 p-3 text-slate-900 shadow-sm focus:border-slate-500 focus:outline-none focus:ring-1 focus:ring-slate-500 disabled:bg-slate-100"
        />
        <div className="mt-3 flex items-center gap-2">
          <button
            type="submit"
            disabled={loading || !text.trim()}
            className="rounded-lg bg-slate-900 px-5 py-2.5 font-medium text-white transition-colors hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-300"
          >
            {loading ? "Checking…" : "Check it"}
          </button>
          {(text || result || error) && !loading && (
            <button
              type="button"
              onClick={clearAll}
              className="rounded-lg px-3 py-2.5 text-sm font-medium text-slate-500 hover:text-slate-800"
            >
              Clear
            </button>
          )}
        </div>
      </form>

      {/* Quick-try examples */}
      <div className="mt-5">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
          Or try an example
        </p>
        <div className="mt-2 flex flex-wrap gap-2">
          {EXAMPLES.map((ex) => (
            <button
              key={ex}
              type="button"
              onClick={() => useExample(ex)}
              disabled={loading}
              className="rounded-full border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-700 shadow-sm transition-colors hover:border-slate-400 hover:bg-slate-50 disabled:opacity-50"
            >
              {ex}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="mt-6 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          {error}
        </div>
      )}

      {result && (
        <div className="mt-6">
          <VerdictCard
            result={result}
            report={report}
            reportLoading={reportLoading}
            reportError={reportError}
            onRetryReport={() => fetchReport(result)}
          />
        </div>
      )}

      {/* Explainer for users */}
      <section className="mt-10 rounded-xl border border-slate-200 bg-white p-6">
        <h2 className="text-lg font-bold">What counts as a medical claim?</h2>
        <p className="mt-1 text-sm text-slate-600">
          The tool sorts every statement into one of three buckets. Only the
          first one — a specific, checkable claim — gets fact-checked.
        </p>

        <div className="mt-4 space-y-3">
          <div className="rounded-lg border-l-4 border-green-500 bg-green-50 p-3">
            <p className="text-sm font-semibold text-green-800">
              ✅ Medical claim
            </p>
            <p className="mt-1 text-sm text-slate-700">
              A specific statement that could be proven true or false with
              evidence — usually &ldquo;X causes / cures / prevents Y.&rdquo;
            </p>
            <p className="mt-1 text-sm italic text-slate-500">
              &ldquo;Vitamin C cures cancer.&rdquo; · &ldquo;The MMR vaccine
              causes autism.&rdquo;
            </p>
          </div>

          <div className="rounded-lg border-l-4 border-slate-400 bg-slate-50 p-3">
            <p className="text-sm font-semibold text-slate-700">
              ℹ️ General health
            </p>
            <p className="mt-1 text-sm text-slate-700">
              Health-related, but too vague or generally-true to fact-check —
              opinions, personal stories, or common knowledge.
            </p>
            <p className="mt-1 text-sm italic text-slate-500">
              &ldquo;COVID-19 is dangerous.&rdquo; · &ldquo;I felt better after
              resting.&rdquo;
            </p>
          </div>

          <div className="rounded-lg border-l-4 border-slate-300 bg-slate-50 p-3">
            <p className="text-sm font-semibold text-slate-500">
              🚫 Not a health claim
            </p>
            <p className="mt-1 text-sm text-slate-700">
              Not about health at all, or just an insult, slogan, or rant with
              no checkable fact.
            </p>
            <p className="mt-1 text-sm italic text-slate-500">
              &ldquo;Do your own research!&rdquo; · &ldquo;Big pharma is
              evil.&rdquo;
            </p>
          </div>
        </div>

        <p className="mt-4 text-xs text-slate-500">
          <span className="font-semibold">Tip:</span> to see a real fact-check
          result, make it specific and checkable — e.g. &ldquo;X causes Y&rdquo;
          rather than &ldquo;X is bad.&rdquo;
        </p>
      </section>
    </div>
  );
}
