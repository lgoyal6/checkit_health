"""Exports: an analyst's working CSV, and a per-narrative situation report.

Two artifacts with different jobs, deliberately not the same thing.

The **working export** is a CSV of whatever the analyst is currently looking
at. It exists because the real workflow ends with someone pasting rows into an
email or a slide, and until now there was no way to get data out of the tool at
all.

The **situation report** is the citable artifact. It is assembled entirely from
stored rows and stamped with the versions that produced it
(``report_version``, ``weights_version``, the evidence chunk ids), so the same
report can be regenerated identically later. That reproducibility is the
difference between a document someone can act on and a screenshot.

Rendering is server-side HTML with a print stylesheet rather than a PDF
library: the browser's own print-to-PDF is one dependency we do not have to
ship, and the HTML stays readable and linkable on its own.
"""

import csv
import html
import io
import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional, Sequence

import config
import narrative_store as ns
import narratives as nar

REPORT_VERSION = "sitrep-1"

DISCLAIMER = (
    "Checkit Health is an analyst-support and monitoring prototype. It is not "
    "medical advice, not an automated moderation system, and not a "
    "determination that any claim is true or false. AI-generated "
    "classifications and summaries can be wrong. Open every cited source and "
    "confirm it addresses the exact claim before acting on it."
)

CSV_COLUMNS = (
    "narrative_id", "narrative_label", "lifecycle_state", "post_id", "username",
    "source", "claim", "topic", "confidence", "like_count", "retweet_count",
    "reach", "timestamp", "timestamp_processed", "status", "evidence_state",
    "fact_check_verdict", "fact_check_source", "fact_check_url",
    "review_status", "reviewer", "reviewed_at", "review_note",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- working export --------------------------------------------------------


def claims_csv(rows: Iterable[Dict[str, Any]]) -> str:
    """Flatten monitor rows to CSV using a stable column order.

    A fixed column order matters more than it looks: analysts build
    spreadsheets on top of these exports, and a column that moves between runs
    breaks every formula downstream.
    """
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer, fieldnames=list(CSV_COLUMNS), extrasaction="ignore", lineterminator="\n"
    )
    writer.writeheader()
    for row in rows:
        record = dict(row)
        record.setdefault(
            "reach",
            int(record.get("like_count") or 0) + int(record.get("retweet_count") or 0),
        )
        if "narrative_label" not in record and record.get("label"):
            record["narrative_label"] = record["label"]
        writer.writerow({key: record.get(key, "") for key in CSV_COLUMNS})
    return buffer.getvalue()


def narratives_csv(rows: Iterable[Dict[str, Any]]) -> str:
    columns = (
        "narrative_id", "label", "canonical_claim", "topic", "lifecycle_state",
        "status", "member_count", "post_count", "platform_count", "reach",
        "reviewed_count", "first_seen_at", "last_seen_at",
    )
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer, fieldnames=list(columns), extrasaction="ignore", lineterminator="\n"
    )
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in columns})
    return buffer.getvalue()


# --- situation report ------------------------------------------------------


def build_situation_report(
    narrative_id: str, db_path: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Assemble every stored fact about one narrative into a citable payload.

    Reads only; generates nothing. Everything here is already in the database,
    which is what makes the report reproducible rather than a fresh opinion.
    """
    narrative = ns.fetch_narrative(narrative_id, db_path=db_path)
    if not narrative:
        return None

    members = ns.fetch_narrative_members(narrative_id, db_path=db_path)
    snapshots = ns.fetch_snapshots(narrative_id, db_path=db_path)
    evidence = ns.fetch_narrative_evidence(narrative_id, db_path=db_path)
    responses = ns.fetch_responses(narrative_id=narrative_id, db_path=db_path)
    traj = nar.trajectory(snapshots)

    reach = sum(
        int(m.get("like_count") or 0) + int(m.get("retweet_count") or 0)
        for m in members
    )
    platforms = sorted({m.get("source") or "unknown" for m in members})
    reviews = [
        {
            "post_id": m["post_id"],
            "review_status": m.get("review_status"),
            "reviewer": m.get("reviewer"),
            "reviewed_at": m.get("reviewed_at"),
            "review_note": m.get("review_note"),
        }
        for m in members
        if m.get("review_status") and m["review_status"] != "unreviewed"
    ]

    return {
        "report_version": REPORT_VERSION,
        "generated_at": _now(),
        "provenance": {
            "weights_version": config.WEIGHTS_VERSION,
            "escalation_rule_version": config.ESCALATION_RULE_VERSION,
            "model": config.MODEL_NAME,
            "embedding_model": (
                config.EMBEDDING_MODEL if config.GOOGLE_API_KEY else "hashed_fallback"
            ),
            "clustering": ns.clustering_quality(),
            "evidence_ids": [e.get("id") for e in evidence],
        },
        "narrative": narrative,
        "summary": {
            "reach": reach,
            "post_count": len(members),
            "platforms": platforms,
            "platform_count": len(platforms),
            "evidence_count": len(evidence),
            "reviewed_count": len(reviews),
            "evidence_state": _dominant_evidence_state(members),
        },
        "trajectory": traj.as_dict(),
        "members": members,
        "evidence": evidence,
        "reviews": reviews,
        "responses": responses,
        "disclaimer": DISCLAIMER,
    }


def _dominant_evidence_state(members: Sequence[Dict[str, Any]]) -> str:
    """The strongest evidence state any member reached.

    Ordered so a single contradicted member is not washed out by a pile of
    members nobody has retrieved evidence for yet.
    """
    order = ["contradicted", "mixed", "supported", "retrieved", "insufficient"]
    present = {m.get("evidence_state") for m in members if m.get("evidence_state")}
    for state in order:
        if state in present:
            return state
    return "insufficient"


def report_json(report: Dict[str, Any]) -> str:
    return json.dumps(report, indent=2, ensure_ascii=False)


# --- HTML rendering --------------------------------------------------------

_STYLE = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; padding: 2.5rem 2rem; font: 14px/1.55 -apple-system,
  BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  color: #0f172a; background: #fff; max-width: 60rem; margin-inline: auto; }
h1 { font-size: 1.5rem; margin: 0 0 .25rem; line-height: 1.25; }
h2 { font-size: .8rem; text-transform: uppercase; letter-spacing: .06em;
  color: #64748b; margin: 2rem 0 .6rem; border-bottom: 1px solid #e2e8f0;
  padding-bottom: .35rem; }
.sub { color: #64748b; margin: 0 0 1.25rem; }
.chips { display: flex; flex-wrap: wrap; gap: .4rem; margin: .75rem 0 0; }
.chip { border: 1px solid #cbd5e1; border-radius: 999px; padding: .15rem .6rem;
  font-size: .75rem; color: #334155; background: #f8fafc; }
.chip.state { border-color: #f59e0b; background: #fffbeb; color: #92400e; }
.chip.contradicted { border-color: #dc2626; background: #fef2f2; color: #991b1b; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(8rem, 1fr));
  gap: .75rem; margin-top: .75rem; }
.stat { border: 1px solid #e2e8f0; border-radius: .5rem; padding: .7rem .8rem; }
.stat b { display: block; font-size: 1.35rem; font-variant-numeric: tabular-nums; }
.stat span { font-size: .7rem; text-transform: uppercase; letter-spacing: .05em;
  color: #64748b; }
table { width: 100%; border-collapse: collapse; margin-top: .5rem;
  font-size: .82rem; }
th { text-align: left; font-size: .68rem; text-transform: uppercase;
  letter-spacing: .05em; color: #64748b; border-bottom: 1px solid #e2e8f0;
  padding: .4rem .5rem; }
td { padding: .45rem .5rem; border-bottom: 1px solid #f1f5f9;
  vertical-align: top; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
.passage { color: #475569; font-size: .78rem; margin-top: .2rem; }
a { color: #1d4ed8; }
.spark { display: block; margin-top: .5rem; }
.note { border: 1px solid #e2e8f0; background: #f8fafc; border-radius: .5rem;
  padding: .8rem 1rem; font-size: .78rem; color: #475569; margin-top: 2rem; }
.prov { font-size: .7rem; color: #94a3b8; margin-top: .75rem;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  word-break: break-all; }
@media print { body { padding: 0; max-width: none; } h2 { break-after: avoid; }
  tr { break-inside: avoid; } .note { break-inside: avoid; } }
"""


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _when(value: Any) -> str:
    """Readable timestamp. Raw ISO with microseconds is noise in a report."""
    if not value:
        return "unknown"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return str(value)
    return parsed.strftime("%d %b %Y, %H:%M UTC")


def _sparkline(points: Sequence[Dict[str, Any]], width: int = 560, height: int = 60) -> str:
    """Inline SVG reach curve, no chart library and no runtime dependency."""
    if len(points) < 2:
        return '<p class="passage">Not enough observations yet to plot growth.</p>'
    values = [max(0, int(p["reach"])) for p in points]
    peak = max(values) or 1
    step = width / (len(values) - 1)
    coords = " ".join(
        f"{i * step:.1f},{height - (v / peak) * (height - 6) - 3:.1f}"
        for i, v in enumerate(values)
    )
    # preserveAspectRatio="none" so the curve fills the column instead of
    # being letterboxed in the middle of it.
    return (
        f'<svg class="spark" viewBox="0 0 {width} {height}" width="100%" '
        f'height="{height}" preserveAspectRatio="none" role="img" '
        f'aria-label="Reach over time">'
        f'<polyline fill="none" stroke="#dc2626" stroke-width="2" '
        f'vector-effect="non-scaling-stroke" stroke-linejoin="round" '
        f'points="{coords}"/></svg>'
    )


def render_html(report: Dict[str, Any]) -> str:
    """Render a situation report as a self-contained, printable HTML page."""
    n = report["narrative"]
    s = report["summary"]
    t = report["trajectory"]
    prov = report["provenance"]

    state_class = "chip state"
    if s["evidence_state"] == "contradicted":
        state_class = "chip contradicted"

    members = "".join(
        f"<tr><td>{_esc(m.get('claim'))}"
        f"<div class='passage'>@{_esc(m.get('username') or 'unknown')} on "
        f"{_esc(m.get('source') or 'unknown')}</div></td>"
        f"<td class='num'>{int(m.get('like_count') or 0):,}</td>"
        f"<td class='num'>{int(m.get('retweet_count') or 0):,}</td>"
        f"<td>{_esc(m.get('review_status') or 'unreviewed')}</td></tr>"
        for m in report["members"]
    ) or "<tr><td colspan='4'>No member posts recorded.</td></tr>"

    evidence = "".join(
        f"<tr><td><a href='{_esc(e.get('url'))}'>{_esc(e.get('title') or e.get('id'))}</a>"
        f"<div class='passage'>{_esc((e.get('passage') or '')[:320])}</div></td>"
        f"<td>{_esc(e.get('publisher'))}</td>"
        f"<td>{_esc(e.get('published_at') or 'unknown')}</td>"
        f"<td class='num'>{_esc(round(float(e.get('relevance_score') or 0), 3))}</td></tr>"
        for e in report["evidence"]
    ) or (
        "<tr><td colspan='4'>No evidence retrieved yet. That is not evidence "
        "the claim is true or false.</td></tr>"
    )

    reviews = "".join(
        f"<tr><td>{_esc(r.get('review_status'))}</td>"
        f"<td>{_esc(r.get('reviewer') or 'unknown')}</td>"
        f"<td>{_esc(_when(r.get('reviewed_at')))}</td>"
        f"<td>{_esc(r.get('review_note') or '')}</td></tr>"
        for r in report["reviews"]
    ) or "<tr><td colspan='4'>No analyst decision recorded yet.</td></tr>"

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Situation report: {_esc(n.get('label'))}</title>
<style>{_STYLE}</style></head><body>
<h1>{_esc(n.get('label'))}</h1>
<p class="sub">Narrative situation report &middot; generated
{_esc(_when(report['generated_at']))}</p>
<div class="chips">
  <span class="chip">{_esc(n.get('lifecycle_state'))}</span>
  <span class="{state_class}">evidence: {_esc(s['evidence_state'])}</span>
  <span class="chip">{_esc(n.get('topic') or 'untopiced')}</span>
  <span class="chip">status: {_esc(n.get('status'))}</span>
</div>

<h2>At a glance</h2>
<div class="grid">
  <div class="stat"><b>{s['reach']:,}</b><span>Total reach</span></div>
  <div class="stat"><b>{s['post_count']}</b><span>Posts</span></div>
  <div class="stat"><b>{s['platform_count']}</b><span>Platforms</span></div>
  <div class="stat"><b>{t['growth_per_hour']:,.1f}</b><span>Reach / hour</span></div>
  <div class="stat"><b>{s['evidence_count']}</b><span>Sources</span></div>
  <div class="stat"><b>{s['reviewed_count']}</b><span>Reviewed</span></div>
</div>

<h2>Reach over time</h2>
{_sparkline(t['points'])}
<p class="passage">First seen {_esc(_when(n.get('first_seen_at')))} &middot; last
seen {_esc(_when(n.get('last_seen_at')))} &middot; {t['observations']} observation(s) over
{t['span_hours']:.1f}h &middot; seen on {_esc(', '.join(s['platforms']))}.</p>

<h2>Posts carrying this narrative</h2>
<table><thead><tr><th>Claim</th><th>Likes</th><th>Reposts</th>
<th>Review</th></tr></thead><tbody>{members}</tbody></table>

<h2>Evidence</h2>
<table><thead><tr><th>Source</th><th>Publisher</th><th>Published</th>
<th>Relevance</th></tr></thead><tbody>{evidence}</tbody></table>

<h2>Analyst decisions</h2>
<table><thead><tr><th>Decision</th><th>Reviewer</th><th>When</th>
<th>Note</th></tr></thead><tbody>{reviews}</tbody></table>

<div class="note"><strong>How to read this.</strong> {_esc(DISCLAIMER)}
<div class="prov">report_version={_esc(report['report_version'])}
&middot; weights={_esc(prov['weights_version'])}
&middot; rules={_esc(prov['escalation_rule_version'])}
&middot; model={_esc(prov['model'])}
&middot; embedding={_esc(prov['embedding_model'])}
&middot; clustering={_esc(prov['clustering']['embedding_mode'])}
@{_esc(prov['clustering']['threshold'])}
&middot; evidence=[{_esc(', '.join(str(i) for i in prov['evidence_ids']))}]</div>
</div>
</body></html>"""
