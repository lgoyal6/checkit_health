"""Generate a self-contained HTML dashboard from output/claims.json.

Usage:
    python dashboard.py
    python dashboard.py --input output/claims.json --output output/dashboard.html

Open the resulting file in a browser or email it as an attachment.
"""

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Checkit Health — v1 Triage Results</title>
<style>
  :root { --bg:#0f1115; --panel:#171a21; --ink:#e7e9ee; --muted:#9aa3b2; --accent:#5eead4; --warn:#fbbf24; --bad:#f87171; --line:#262b36; }
  * { box-sizing: border-box; }
  body { margin:0; font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; background:var(--bg); color:var(--ink); }
  header { padding:32px 40px 16px; border-bottom:1px solid var(--line); }
  h1 { margin:0 0 4px; font-size:24px; font-weight:600; }
  .sub { color:var(--muted); font-size:13px; }
  .stats { display:flex; gap:16px; padding:24px 40px; flex-wrap:wrap; }
  .stat { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px 18px; min-width:140px; }
  .stat .n { font-size:22px; font-weight:600; }
  .stat .l { color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:0.04em; }
  .controls { padding:0 40px 12px; display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
  .controls input, .controls select { background:var(--panel); border:1px solid var(--line); color:var(--ink); border-radius:6px; padding:8px 10px; font:inherit; }
  .controls input { width:280px; }
  .controls .count { color:var(--muted); font-size:12px; margin-left:auto; }
  table { width:calc(100% - 80px); margin:0 40px 40px; border-collapse:collapse; background:var(--panel); border:1px solid var(--line); border-radius:8px; overflow:hidden; }
  th, td { padding:12px 14px; text-align:left; border-bottom:1px solid var(--line); vertical-align:top; }
  th { background:#1d2129; font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:0.04em; color:var(--muted); cursor:pointer; user-select:none; }
  th:hover { color:var(--ink); }
  tr:last-child td { border-bottom:none; }
  .topic { display:inline-block; padding:2px 8px; border-radius:4px; font-size:11px; text-transform:uppercase; letter-spacing:0.04em; background:#26303a; color:var(--accent); }
  .conf { font-variant-numeric: tabular-nums; font-weight:600; }
  .conf.high { color:var(--accent); }
  .conf.mid  { color:var(--warn); }
  .conf.low  { color:var(--bad); }
  .claim { color:var(--ink); }
  .text  { color:var(--muted); font-size:12px; margin-top:6px; max-width:560px; }
  footer { padding:24px 40px; color:var(--muted); font-size:12px; border-top:1px solid var(--line); }
  code { background:#1d2129; padding:1px 6px; border-radius:4px; font-size:12px; }
</style>
</head>
<body>
<header>
  <h1>Checkit Health — v1 triage results</h1>
  <div class="sub">Generated {generated_at} · Source: <code>{source}</code></div>
</header>

<section class="stats">
  <div class="stat"><div class="n">{ingested}</div><div class="l">Posts ingested</div></div>
  <div class="stat"><div class="n">{classified_ok}</div><div class="l">Successfully classified</div></div>
  <div class="stat"><div class="n">{flagged}</div><div class="l">Medical claims flagged</div></div>
  <div class="stat"><div class="n">{threshold}</div><div class="l">Confidence threshold</div></div>
</section>

<div class="controls">
  <input id="q" placeholder="Search claim or post text…" />
  <select id="topic">
    <option value="">All topics</option>
    {topic_options}
  </select>
  <span class="count" id="count"></span>
</div>

<table id="t">
  <thead>
    <tr>
      <th data-k="topic">Topic</th>
      <th data-k="confidence">Conf.</th>
      <th data-k="claim">Claim / Post</th>
      <th data-k="username">Author</th>
    </tr>
  </thead>
  <tbody></tbody>
</table>

<footer>
  v1 prototype — claims flagged here are <code>pending_fact_check</code>. No fact-checking has been performed yet.
  Underlying data: 296 posts scraped via Apify; classifier: Google Gemini Flash Lite with JSON-mode triage.
</footer>

<script>
const DATA = {data_json};
const tbody = document.querySelector('#t tbody');
const q = document.getElementById('q');
const topicSel = document.getElementById('topic');
const countEl = document.getElementById('count');
let sortKey = 'confidence', sortDir = -1;

function confClass(c) { return c >= 0.9 ? 'high' : c >= 0.75 ? 'mid' : 'low'; }
function escape(s) { return (s||'').replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m])); }

function render() {
  const term = q.value.toLowerCase();
  const tp = topicSel.value;
  let rows = DATA.filter(r =>
    (!tp || r.topic === tp) &&
    (!term || (r.claim||'').toLowerCase().includes(term) || (r.text||'').toLowerCase().includes(term))
  );
  rows.sort((a,b) => {
    const av = a[sortKey], bv = b[sortKey];
    if (typeof av === 'number') return (av - bv) * sortDir;
    return String(av||'').localeCompare(String(bv||'')) * sortDir;
  });
  tbody.innerHTML = rows.map(r => `
    <tr>
      <td><span class="topic">${escape(r.topic)}</span></td>
      <td class="conf ${confClass(r.confidence)}">${r.confidence.toFixed(2)}</td>
      <td>
        <div class="claim">${escape(r.claim)}</div>
        <div class="text">${escape((r.text||'').slice(0,220))}${(r.text||'').length>220?'…':''}</div>
      </td>
      <td>${escape(r.username)}</td>
    </tr>`).join('');
  countEl.textContent = `${rows.length} of ${DATA.length} claims shown`;
}

document.querySelectorAll('th').forEach(th => th.onclick = () => {
  const k = th.dataset.k;
  if (sortKey === k) sortDir = -sortDir; else { sortKey = k; sortDir = (k === 'confidence') ? -1 : 1; }
  render();
});
q.oninput = render; topicSel.onchange = render;
render();
</script>
</body>
</html>
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="output/claims.json")
    ap.add_argument("--output", default="output/dashboard.html")
    ap.add_argument("--ingested", type=int, default=None,
                    help="Total posts ingested (defaults to len(claims) if not given)")
    args = ap.parse_args()

    src = Path(args.input)
    claims = json.loads(src.read_text(encoding="utf-8"))

    rows = [
        {
            "topic": c.get("topic") or "other",
            "confidence": float(c.get("confidence") or 0),
            "claim": c.get("claim") or "",
            "text": c.get("text") or "",
            "username": c.get("username") or "unknown",
        }
        for c in claims
    ]

    topics = Counter(r["topic"] for r in rows)
    topic_options = "\n    ".join(
        f'<option value="{t}">{t} ({n})</option>' for t, n in topics.most_common()
    )

    replacements = {
        "{generated_at}": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "{source}": str(src),
        "{ingested}": str(args.ingested if args.ingested is not None else len(rows)),
        "{classified_ok}": str(len(rows)),
        "{flagged}": str(len(rows)),
        "{threshold}": "0.70",
        "{topic_options}": topic_options,
        "{data_json}": json.dumps(rows, ensure_ascii=False),
    }
    html = HTML_TEMPLATE
    for k, v in replacements.items():
        html = html.replace(k, v)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"Wrote dashboard: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
