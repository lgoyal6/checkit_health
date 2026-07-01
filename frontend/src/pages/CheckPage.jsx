import { useState } from "react";
import { checkClaim } from "../api.js";
import VerdictCard from "../components/VerdictCard.jsx";

export default function CheckPage() {
  const [text, setText] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);

  async function onSubmit(e) {
    e.preventDefault();
    if (!text.trim() || loading) return;
    setLoading(true);
    setError("");
    setResult(null);
    try {
      const data = await checkClaim(text.trim());
      setResult(data);
    } catch (err) {
      setError(err.message || "Something went wrong.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div>
      <h1 className="text-2xl font-bold">Check a health claim</h1>
      <p className="mt-1 text-slate-600">
        Paste a post or statement and see how the triage classifier scores it.
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
        <button
          type="submit"
          disabled={loading || !text.trim()}
          className="mt-3 rounded-lg bg-slate-900 px-5 py-2.5 font-medium text-white transition-colors hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          {loading ? "Checking…" : "Check it"}
        </button>
      </form>

      {error && (
        <div className="mt-6 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          {error}
        </div>
      )}

      {result && (
        <div className="mt-6">
          <VerdictCard result={result} />
        </div>
      )}
    </div>
  );
}
