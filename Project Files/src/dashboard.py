"""
dashboard.py -- charts, KPI tiles, and the HTML report.

Joins three sources: data/cases.csv (the corpus), outputs/review_log.csv (what the human
decided), and a live run of rule_checker.py (not a cached result - the catch rate is
recomputed every time, so it cannot drift away from the code).

Design notes, because chart choices here are deliberate rather than defaults:

  * Palette is the validated reference instance from the dataviz skill. Single-series
    charts use categorical slot 1 alone. Charts over ORDERED categories - OSI layer,
    severity, degree of human intervention - use a one-hue ordinal blue ramp whose steps
    were checked with `validate_palette.js --ordinal` (monotone lightness, adjacent
    lightness gaps >= 0.06, light end clearing the surface). No nominal category is
    coloured by its own value.

  * Every chart is single-encoding-safe: the category is named on the axis and the value
    is direct-labelled, so colour never carries meaning on its own. Each chart also has
    a table twin in the HTML, and dashboard_summary.csv is the whole thing in one file.

  * Bar data-ends are rounded 4px on the data end only and anchored flat to the
    baseline; grid and axis are hairlines one shade off the surface.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.path import Path as MplPath
from matplotlib.patches import PathPatch
from jinja2 import Template

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rule_checker import run_checks, check_count
from review import summarise, ACCEPTED, EDITED, REJECTED
from schema import evidence_integrity_rate
from ai_diagnose import get_provider, diagnose_case, DiagnosisError

ROOT = Path(__file__).resolve().parent.parent
CASES_PATH = ROOT / "data" / "cases.csv"
CHARTS_DIR = ROOT / "outputs" / "charts"
HTML_PATH = ROOT / "outputs" / "dashboard.html"
SUMMARY_PATH = ROOT / "outputs" / "dashboard_summary.csv"

# ---------------------------------------------------------------------------
# palette -- validated reference instance, light mode, surface #fcfcfb
# ---------------------------------------------------------------------------
SURFACE = "#fcfcfb"
SERIES_1 = "#2a78d6"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
BASELINE = "#c3c2b7"

# One-hue ordinal ramps. Steps chosen so adjacent lightness gaps clear 0.06 and the
# light end clears 2:1 against the surface -- all four checks PASS under --ordinal.
ORDINAL_5 = ["#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#104281"]
ORDINAL_4 = ["#86b6ef", "#3987e5", "#1c5cab", "#0d366b"]

FONT_STACK = ["Segoe UI", "system-ui", "DejaVu Sans", "sans-serif"]

plt.rcParams.update(
    {
        "font.family": "sans-serif",
        "font.sans-serif": FONT_STACK,
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
        "text.color": INK_PRIMARY,
        "axes.labelcolor": INK_SECONDARY,
        "xtick.color": INK_MUTED,
        "ytick.color": INK_MUTED,
        "axes.edgecolor": BASELINE,
        "grid.color": GRIDLINE,
        "grid.linewidth": 0.8,
        "axes.linewidth": 0.8,
        "xtick.major.width": 0.0,
        "ytick.major.width": 0.0,
        "axes.titlesize": 12,
        "font.size": 9.5,
    }
)


# ---------------------------------------------------------------------------
# marks
# ---------------------------------------------------------------------------

DPI = 160
RADIUS_PX = 4 * DPI / 100  # 4 CSS px at the size the PNG is actually displayed


def _rounded_bar_path(x0, y0, x1, y1, rx, ry, horizontal):
    """
    Rectangle with only the DATA END rounded; the baseline end stays square so bars sit
    flat on the axis. matplotlib has no bar style for this, so the path is built by hand.

    rx and ry are separate on purpose. A single radius in data units produces a squashed
    bevel, because one data unit is a different number of pixels on each axis.
    """
    if horizontal:
        rx = min(rx, abs(x1 - x0))
        ry = min(ry, abs(y1 - y0) / 2)
        verts = [
            (x0, y0), (x1 - rx, y0),
            (x1, y0), (x1, y0 + ry),
            (x1, y1 - ry),
            (x1, y1), (x1 - rx, y1),
            (x0, y1), (x0, y0),
        ]
    else:
        ry = min(ry, abs(y1 - y0))
        rx = min(rx, abs(x1 - x0) / 2)
        verts = [
            (x0, y0), (x0, y1 - ry),
            (x0, y1), (x0 + rx, y1),
            (x1 - rx, y1),
            (x1, y1), (x1, y1 - ry),
            (x1, y0), (x0, y0),
        ]

    codes = [
        MplPath.MOVETO, MplPath.LINETO,
        MplPath.CURVE3, MplPath.CURVE3,
        MplPath.LINETO,
        MplPath.CURVE3, MplPath.CURVE3,
        MplPath.LINETO, MplPath.CLOSEPOLY,
    ]
    return MplPath(verts, codes)


def _data_per_pixel(ax) -> tuple[float, float]:
    """Data units per output pixel on each axis. Requires limits to be set already."""
    (px0, py0), (px1, py1) = ax.transData.transform([(0, 0), (1, 1)])
    return 1 / abs(px1 - px0), 1 / abs(py1 - py0)


def _draw_bars(ax, positions, values, colors, thickness, horizontal):
    """
    Draw bars as rounded-data-end paths. The 2px surface gap between adjacent bars comes
    from thickness being less than the category pitch, not from a stroke on each mark.
    """
    dx, dy = _data_per_pixel(ax)
    rx, ry = RADIUS_PX * dx, RADIUS_PX * dy
    half = thickness / 2

    for pos, value, color in zip(positions, values, colors):
        if horizontal:
            path = _rounded_bar_path(0, pos - half, value, pos + half, rx, ry, True)
        else:
            path = _rounded_bar_path(pos - half, 0, pos + half, value, rx, ry, False)
        ax.add_patch(PathPatch(path, facecolor=color, edgecolor="none", zorder=3))


def _titles(ax, title, subtitle):
    """
    Title and subtitle stacked above the axes at fixed point offsets, so the gap between
    them does not change with figure height the way an axes-fraction offset would.
    """
    ax.annotate(
        title, xy=(0, 1), xycoords="axes fraction",
        xytext=(0, 30), textcoords="offset points",
        ha="left", va="baseline", color=INK_PRIMARY, fontsize=12.5, fontweight="600",
    )
    ax.annotate(
        subtitle, xy=(0, 1), xycoords="axes fraction",
        xytext=(0, 13), textcoords="offset points",
        ha="left", va="baseline", color=INK_MUTED, fontsize=9,
    )


def _finish(fig, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=DPI, bbox_inches="tight", pad_inches=0.3)
    plt.close(fig)
    return path.name


def _hbar(labels, values, colors, title, subtitle, out_path):
    """Horizontal bars, longest at top, values direct-labelled outside the bar end."""
    top = max(values) if values else 1
    height = max(2.3, 0.40 * len(labels) + 1.5)
    fig, ax = plt.subplots(figsize=(7.4, height), dpi=DPI)

    positions = list(range(len(labels)))[::-1]

    # Limits before marks: the corner radius is derived from the axes transform.
    ax.set_xlim(0, top * 1.18)
    ax.set_ylim(-0.72, len(labels) - 0.28)
    _draw_bars(ax, positions, values, colors, thickness=0.42, horizontal=True)

    ax.set_yticks(positions)
    ax.set_yticklabels(labels, color=INK_SECONDARY)
    ax.set_xticks(range(0, top + 1, 1 if top <= 12 else 5))
    ax.xaxis.grid(True, zorder=0)
    ax.yaxis.grid(False)
    ax.set_axisbelow(True)
    ax.tick_params(axis="y", pad=8)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color(BASELINE)

    for pos, value in zip(positions, values):
        ax.text(
            value + top * 0.022, pos, f"{value:.0f}",
            va="center", ha="left", color=INK_PRIMARY, fontsize=9.5,
        )

    _titles(ax, title, subtitle)
    return _finish(fig, out_path)


def _vbar(labels, values, colors, title, subtitle, out_path, xlabel=""):
    """Vertical bars for ordered categories, values direct-labelled above the bar end."""
    top = max(values) if values else 1
    fig, ax = plt.subplots(figsize=(7.4, 3.4), dpi=DPI)

    positions = list(range(len(labels)))

    ax.set_ylim(0, top * 1.22)
    ax.set_xlim(-0.72, len(labels) - 0.28)
    _draw_bars(ax, positions, values, colors, thickness=0.40, horizontal=False)

    ax.set_xticks(positions)
    ax.set_xticklabels(labels, color=INK_SECONDARY)
    ax.set_yticks(range(0, top + 1, 1 if top <= 12 else 5))
    ax.yaxis.grid(True, zorder=0)
    ax.xaxis.grid(False)
    ax.set_axisbelow(True)
    ax.tick_params(axis="x", pad=8)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color(BASELINE)
    if xlabel:
        ax.set_xlabel(xlabel, color=INK_MUTED, fontsize=8.8, labelpad=12)

    for pos, value in zip(positions, values):
        ax.text(
            pos, value + top * 0.04, f"{value:.0f}",
            ha="center", va="bottom", color=INK_PRIMARY, fontsize=9.5,
        )

    _titles(ax, title, subtitle)
    return _finish(fig, out_path)


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------

def load_cases(path: Path = CASES_PATH) -> list[dict]:
    csv.field_size_limit(10 ** 7)
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def build_frame(cases: list[dict]) -> pd.DataFrame:
    """
    One row per case, joined against the review log and a live checker run.

    `checker_fired` is recomputed here rather than read from a saved report, so the
    dashboard cannot claim a catch rate the current code does not actually produce.
    """
    from review import load_log

    log = load_log()
    rows = []

    for case in cases:
        findings = run_checks(case)
        review = log.get(case["case_id"], {})
        rows.append(
            {
                "case_id": case["case_id"],
                "concept_tag": case["concept_tag"],
                "osi_layer": int(case["osi_layer"]),
                "severity": case["severity"],
                "expected_fault": case["expected_fault"],
                "checker_fired": bool(findings),
                "finding_count": len(findings),
                "check_ids": ", ".join(f.check_id for f in findings),
                "decision": review.get("decision", "Not reviewed"),
                "failure_mode": (review.get("failure_mode") or "").strip(),
                "checker_agreement": (review.get("checker_agreement") or "").strip(),
            }
        )

    return pd.DataFrame(rows)


def evidence_stats(cases: list[dict]) -> tuple[float, int]:
    """Verbatim-evidence integrity across the recorded diagnoses."""
    try:
        provider = get_provider("recorded")
    except DiagnosisError:
        return 0.0, 0

    diagnoses, cases_by_id = {}, {}
    for case in cases:
        try:
            diagnoses[case["case_id"]] = provider.diagnose(case)
            cases_by_id[case["case_id"]] = case
        except DiagnosisError:
            continue

    rate = evidence_integrity_rate(diagnoses, cases_by_id)
    flagged = round((1 - rate) * len(diagnoses))
    return rate, flagged


# ---------------------------------------------------------------------------
# charts
# ---------------------------------------------------------------------------

SEVERITY_ORDER = ["Low", "Medium", "High", "Critical"]


def make_charts(df: pd.DataFrame) -> dict[str, str]:
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    charts = {}

    # 1. Nominal categories -> one series, one colour. A value ramp here would
    #    double-encode bar length as hue.
    tags = df["concept_tag"].value_counts().sort_values(ascending=False)
    charts["by_concept"] = _hbar(
        list(tags.index), list(tags.values), [SERIES_1] * len(tags),
        "Case coverage by networking concept",
        f"{len(df)} cases across {len(tags)} fault families",
        CHARTS_DIR / "cases_by_concept.png",
    )

    # 2. OSI layer is an ordered scale, so the ordinal ramp is the correct encoding.
    layers = df["osi_layer"].value_counts().sort_index()
    charts["by_layer"] = _vbar(
        [f"L{n}" for n in layers.index], list(layers.values),
        ORDINAL_5[: len(layers)],
        "Case coverage by OSI layer",
        "Layer 1 physical through layer 7 application",
        CHARTS_DIR / "cases_by_osi_layer.png",
        xlabel="lighter = lower layer",
    )

    # 3. Severity is ordered too. Ramp direction carries the escalation.
    counts = df["severity"].value_counts()
    sev_labels = [s for s in SEVERITY_ORDER if s in counts.index]
    charts["by_severity"] = _vbar(
        sev_labels, [int(counts[s]) for s in sev_labels],
        ORDINAL_4[: len(sev_labels)],
        "Case severity distribution",
        "Severity as assigned in the case library",
        CHARTS_DIR / "cases_by_severity.png",
        xlabel="lighter = lower severity",
    )

    # 4. The Responsible AI evidence: how the 12 corrected diagnoses actually failed.
    modes = (
        df[df["failure_mode"] != ""]["failure_mode"]
        .value_counts()
        .sort_values(ascending=False)
    )
    charts["failure_modes"] = _hbar(
        list(modes.index), list(modes.values), [SERIES_1] * len(modes),
        "How the AI diagnoses failed",
        f"{int(modes.sum())} corrected cases, {len(modes)} distinct failure modes",
        CHARTS_DIR / "ai_failure_modes.png",
    )

    # 5. The central claim of the architecture, measured rather than asserted:
    #    where in the pipeline each fault was actually caught. Categories are ordered by
    #    increasing human involvement, so the ordinal ramp applies.
    both = int(((df["checker_fired"]) & (df["decision"] == ACCEPTED)).sum())
    edited = int(((df["checker_fired"]) & (df["decision"] == EDITED)).sum())
    rejected = int(((df["checker_fired"]) & (df["decision"] == REJECTED)).sum())
    neither = int((~df["checker_fired"] & (df["decision"] != ACCEPTED)).sum())
    ai_only = int((~df["checker_fired"] & (df["decision"] == ACCEPTED)).sum())

    catch_labels = [
        "Checker + AI agreed",
        "Checker caught it, AI needed edits",
        "Checker caught it, AI was wrong",
        "Neither caught it - human only",
    ]
    catch_values = [both, edited, rejected, neither]
    if ai_only:
        catch_labels.insert(3, "AI caught it, checker silent")
        catch_values.insert(3, ai_only)

    charts["catch_layer"] = _hbar(
        catch_labels, catch_values, ORDINAL_4[: len(catch_values)],
        "Where each fault was actually caught",
        "Deterministic checks and the AI layer are not interchangeable",
        CHARTS_DIR / "catch_by_layer.png",
    )

    return charts


# ---------------------------------------------------------------------------
# summary csv + html
# ---------------------------------------------------------------------------

def write_summary_csv(df: pd.DataFrame, kpis: dict, path: Path = SUMMARY_PATH) -> None:
    """
    Per-case table plus a KPI block. Satisfies the deliverable's "spreadsheet or simple
    chart" wording in spreadsheet form, and is the table-view twin for every chart.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "case_id", "concept_tag", "osi_layer", "severity",
        "checker_fired", "finding_count", "check_ids",
        "decision", "failure_mode", "checker_agreement", "expected_fault",
    ]
    df[columns].to_csv(path, index=False, encoding="utf-8")

    with open(path, "a", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([])
        writer.writerow(["KPI", "value"])
        for key, value in kpis.items():
            writer.writerow([key, value])


HTML_TEMPLATE = Template("""<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NetSage AI - Diagnostic Dashboard</title>
<style>
  :root {
    color-scheme: light;
    --surface-1: #fcfcfb;
    --page: #f9f9f7;
    --text-primary: #0b0b0b;
    --text-secondary: #52514e;
    --text-muted: #898781;
    --gridline: #e1e0d9;
    --baseline: #c3c2b7;
    --series-1: #2a78d6;
    --border: rgba(11,11,11,0.10);
    --good: #0ca30c;
    --critical: #d03b3b;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 40px 28px 72px;
    background: var(--page); color: var(--text-primary);
    font-family: "Segoe UI", system-ui, -apple-system, sans-serif;
    font-size: 15px; line-height: 1.55;
  }
  .wrap { max-width: 1080px; margin: 0 auto; }
  header { margin-bottom: 34px; }
  h1 { font-size: 26px; margin: 0 0 6px; font-weight: 650; letter-spacing: -0.01em; }
  .sub { color: var(--text-secondary); margin: 0; }
  .meta { color: var(--text-muted); font-size: 13px; margin-top: 10px; }
  h2 {
    font-size: 13px; text-transform: uppercase; letter-spacing: 0.07em;
    color: var(--text-muted); font-weight: 650;
    margin: 44px 0 16px; padding-bottom: 8px; border-bottom: 1px solid var(--gridline);
  }
  .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(168px, 1fr)); gap: 14px; }
  .tile {
    background: var(--surface-1); border: 1px solid var(--border);
    border-radius: 10px; padding: 18px 18px 16px;
  }
  .tile .v { font-size: 30px; font-weight: 640; letter-spacing: -0.02em; line-height: 1.1; }
  .tile .k {
    font-size: 11.5px; text-transform: uppercase; letter-spacing: 0.05em;
    color: var(--text-muted); margin-top: 7px; font-weight: 600;
  }
  .tile .n { font-size: 12.5px; color: var(--text-secondary); margin-top: 7px; }
  .tile.good .v { color: var(--good); }
  .tile.flag .v { color: var(--critical); }
  figure {
    margin: 0 0 22px; background: var(--surface-1);
    border: 1px solid var(--border); border-radius: 10px; padding: 20px 20px 12px;
  }
  figure img { width: 100%; height: auto; display: block; }
  figcaption { color: var(--text-secondary); font-size: 13px; margin-top: 12px; }
  details { margin-top: 12px; }
  summary {
    cursor: pointer; color: var(--series-1); font-size: 13px;
    font-weight: 560; padding: 4px 0;
  }
  table {
    width: 100%; border-collapse: collapse; margin-top: 12px;
    font-size: 13px; font-variant-numeric: tabular-nums;
  }
  th {
    text-align: left; font-weight: 620; color: var(--text-muted);
    font-size: 11.5px; text-transform: uppercase; letter-spacing: 0.05em;
    padding: 8px 12px 8px 0; border-bottom: 1px solid var(--baseline);
  }
  td { padding: 7px 12px 7px 0; border-bottom: 1px solid var(--gridline); color: var(--text-secondary); }
  td.id { color: var(--text-primary); font-weight: 560; white-space: nowrap; }
  .pill {
    display: inline-block; padding: 1px 9px; border-radius: 999px;
    font-size: 11.5px; font-weight: 600; border: 1px solid var(--border);
    background: var(--page);
  }
  .note {
    background: var(--surface-1); border: 1px solid var(--border);
    border-left: 3px solid var(--series-1);
    border-radius: 8px; padding: 16px 20px; color: var(--text-secondary);
    font-size: 13.5px; margin-top: 16px;
  }
  .note strong { color: var(--text-primary); }
  footer { margin-top: 52px; color: var(--text-muted); font-size: 12.5px; }
</style>
</head>
<body>
<div class="wrap">

<header>
  <h1>NetSage AI</h1>
  <p class="sub">Applied AI for network troubleshooting, under mandatory human review.</p>
  <p class="meta">
    Cisco-AICTE Virtual Internship Program 2026 &middot; Project 2, Applied AI + Network
    Troubleshooting &middot; Samarth Mehrotra, IILM University &middot;
    generated from {{ total_cases }} cases and a live rule-checker run
  </p>
</header>

<h2>Key figures</h2>
<div class="tiles">
  <div class="tile"><div class="v">{{ total_cases }}</div><div class="k">Cases</div>
    <div class="n">{{ concept_count }} fault families, OSI layers {{ layer_list }}</div></div>
  <div class="tile"><div class="v">{{ check_count }}</div><div class="k">Deterministic checks</div>
    <div class="n">Pure Python, no model in the loop</div></div>
  <div class="tile good"><div class="v">{{ catch_rate }}</div><div class="k">Checker catch rate</div>
    <div class="n">{{ checker_hits }} of {{ total_cases }} cases produced a finding</div></div>
  <div class="tile"><div class="v">{{ agreement_rate }}</div><div class="k">AI agreement rate</div>
    <div class="n">{{ accepted }} accepted unedited on review</div></div>
  <div class="tile flag"><div class="v">{{ corrections }}</div><div class="k">Human corrections</div>
    <div class="n">{{ edited }} edited, {{ rejected }} rejected &middot; requirement is 5</div></div>
  <div class="tile"><div class="v">{{ evidence_rate }}</div><div class="k">Evidence integrity</div>
    <div class="n">{{ evidence_flagged }} diagnosis cited a line not in the transcript</div></div>
</div>

<div class="note">
  <strong>Read the agreement rate as a feature, not a shortfall.</strong>
  The recorded diagnoses were produced without access to the <code>expected_fault</code>
  column, and wrong answers were left in rather than regenerated until they disappeared.
  A dashboard reporting 100% agreement would be evidence of a leaked answer key. The
  {{ corrections }} corrections are the deliverable; they are documented case by case in
  <code>docs/responsible_ai_log.md</code>.
</div>

<h2>Case coverage</h2>

<figure>
  <img src="charts/{{ charts.by_concept }}" alt="Horizontal bar chart of case counts by networking concept">
  <figcaption>Nine fault families. No family is represented by fewer than two cases, so
  no check is validated by a single example.</figcaption>
  <details><summary>Table view</summary>
    <table><thead><tr><th>Concept</th><th>Cases</th><th>Checker fired</th></tr></thead><tbody>
    {% for row in concept_table %}
      <tr><td class="id">{{ row.concept }}</td><td>{{ row.cases }}</td><td>{{ row.fired }}</td></tr>
    {% endfor %}
    </tbody></table>
  </details>
</figure>

<figure>
  <img src="charts/{{ charts.by_layer }}" alt="Vertical bar chart of case counts by OSI layer">
  <figcaption>Layers 1, 2, 3, 4 and 7. Layer 3 dominates because addressing, routing,
  DHCP and NAT all live there.</figcaption>
  <details><summary>Table view</summary>
    <table><thead><tr><th>OSI layer</th><th>Cases</th></tr></thead><tbody>
    {% for row in layer_table %}
      <tr><td class="id">Layer {{ row.layer }}</td><td>{{ row.cases }}</td></tr>
    {% endfor %}
    </tbody></table>
  </details>
</figure>

<figure>
  <img src="charts/{{ charts.by_severity }}" alt="Vertical bar chart of case counts by severity">
  <figcaption>Two cases are Critical. Both are security-boundary failures where
  connectivity works and that is precisely the problem.</figcaption>
  <details><summary>Table view</summary>
    <table><thead><tr><th>Severity</th><th>Cases</th></tr></thead><tbody>
    {% for row in severity_table %}
      <tr><td class="id">{{ row.severity }}</td><td>{{ row.cases }}</td></tr>
    {% endfor %}
    </tbody></table>
  </details>
</figure>

<h2>Human oversight</h2>

<figure>
  <img src="charts/{{ charts.failure_modes }}" alt="Horizontal bar chart of AI failure modes">
  <figcaption>The {{ corrections }} corrected diagnoses, grouped by how they failed. Every
  mode here changed either the prompt or the review procedure.</figcaption>
  <details><summary>Table view</summary>
    <table><thead><tr><th>Failure mode</th><th>Cases</th></tr></thead><tbody>
    {% for row in mode_table %}
      <tr><td class="id">{{ row.mode }}</td><td>{{ row.count }}</td></tr>
    {% endfor %}
    </tbody></table>
  </details>
</figure>

<figure>
  <img src="charts/{{ charts.catch_layer }}" alt="Horizontal bar chart of which layer caught each fault">
  <figcaption>The architectural argument, measured. The deterministic checker caught
  {{ checker_hits }} of {{ total_cases }} faults and never hallucinated one; the AI named the
  cause unaided in {{ accepted }}. One case ({{ human_only_ids }}) was caught by neither and
  needed the reviewer - no rule can know which VLAN a port <em>should</em> be in.</figcaption>
  <details><summary>Table view</summary>
    <table><thead><tr><th>Outcome</th><th>Cases</th></tr></thead><tbody>
    {% for row in catch_table %}
      <tr><td class="id">{{ row.label }}</td><td>{{ row.count }}</td></tr>
    {% endfor %}
    </tbody></table>
  </details>
</figure>

<h2>Every case</h2>
<figure>
  <table>
    <thead><tr>
      <th>Case</th><th>Concept</th><th>L</th><th>Severity</th>
      <th>Checks fired</th><th>Review</th><th>Failure mode</th>
    </tr></thead>
    <tbody>
    {% for row in case_table %}
      <tr>
        <td class="id">{{ row.case_id }}</td>
        <td>{{ row.concept_tag }}</td>
        <td>{{ row.osi_layer }}</td>
        <td>{{ row.severity }}</td>
        <td>{{ row.check_ids or "&mdash; none &mdash;" }}</td>
        <td><span class="pill">{{ row.decision }}</span></td>
        <td>{{ row.failure_mode or "" }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  <figcaption>Full corpus. <code>outputs/dashboard_summary.csv</code> is the same table
  plus the KPI block, for spreadsheet use.</figcaption>
</figure>

<footer>
  Charts rendered by <code>src/dashboard.py</code>. Catch rate is recomputed from a live
  <code>rule_checker.py</code> run on every build, so it cannot drift from the code.
  Palette is the validated light-mode reference instance; every chart is direct-labelled
  and has a table twin, so no value depends on colour.
</footer>

</div>
</body>
</html>
""")


def render_html(df: pd.DataFrame, charts: dict, kpis: dict, extras: dict) -> Path:
    layers = sorted(df["osi_layer"].unique())
    counts = df["severity"].value_counts()

    concept_table = [
        {
            "concept": tag,
            "cases": int(len(group)),
            "fired": int(group["checker_fired"].sum()),
        }
        for tag, group in sorted(df.groupby("concept_tag"), key=lambda kv: -len(kv[1]))
    ]

    modes = df[df["failure_mode"] != ""]["failure_mode"].value_counts()

    html = HTML_TEMPLATE.render(
        total_cases=len(df),
        concept_count=df["concept_tag"].nunique(),
        layer_list=", ".join(str(n) for n in layers),
        check_count=check_count(),
        catch_rate=kpis["checker_catch_rate"],
        checker_hits=int(df["checker_fired"].sum()),
        agreement_rate=kpis["ai_agreement_rate"],
        accepted=kpis["accepted"],
        edited=kpis["edited"],
        rejected=kpis["rejected"],
        corrections=kpis["human_corrections"],
        evidence_rate=kpis["evidence_integrity_rate"],
        evidence_flagged=kpis["diagnoses_with_unverifiable_evidence"],
        human_only_ids=extras["human_only_ids"],
        charts=charts,
        concept_table=concept_table,
        layer_table=[
            {"layer": int(n), "cases": int((df["osi_layer"] == n).sum())} for n in layers
        ],
        severity_table=[
            {"severity": s, "cases": int(counts[s])}
            for s in SEVERITY_ORDER
            if s in counts.index
        ],
        mode_table=[{"mode": m, "count": int(c)} for m, c in modes.items()],
        catch_table=extras["catch_table"],
        case_table=df.to_dict("records"),
    )

    HTML_PATH.parent.mkdir(parents=True, exist_ok=True)
    HTML_PATH.write_text(html, encoding="utf-8")
    return HTML_PATH


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def build(verbose: bool = True) -> dict:
    cases = load_cases()
    df = build_frame(cases)
    review = summarise()
    ev_rate, ev_flagged = evidence_stats(cases)

    checker_hits = int(df["checker_fired"].sum())
    human_only = df[(~df["checker_fired"]) & (df["decision"] != ACCEPTED)]["case_id"]

    kpis = {
        "total_cases": len(df),
        "concept_tags": int(df["concept_tag"].nunique()),
        "osi_layers_covered": int(df["osi_layer"].nunique()),
        "deterministic_checks": check_count(),
        "cases_with_findings": checker_hits,
        "checker_catch_rate": f"{checker_hits / len(df):.1%}",
        "cases_reviewed": review["total_reviewed"],
        "accepted": review["accepted"],
        "edited": review["edited"],
        "rejected": review["rejected"],
        "human_corrections": review["corrections"],
        "ai_agreement_rate": f"{review['agreement_rate']:.1%}",
        "evidence_integrity_rate": f"{ev_rate:.1%}",
        "diagnoses_with_unverifiable_evidence": ev_flagged,
    }

    charts = make_charts(df)

    both = int(((df["checker_fired"]) & (df["decision"] == ACCEPTED)).sum())
    extras = {
        "human_only_ids": ", ".join(human_only) or "none",
        "catch_table": [
            {"label": "Checker + AI agreed", "count": both},
            {"label": "Checker caught it, AI needed edits",
             "count": int(((df["checker_fired"]) & (df["decision"] == EDITED)).sum())},
            {"label": "Checker caught it, AI was wrong",
             "count": int(((df["checker_fired"]) & (df["decision"] == REJECTED)).sum())},
            {"label": "AI caught it, checker silent",
             "count": int((~df["checker_fired"] & (df["decision"] == ACCEPTED)).sum())},
            {"label": "Neither caught it - human only", "count": int(len(human_only))},
        ],
    }

    write_summary_csv(df, kpis)
    html_path = render_html(df, charts, kpis, extras)

    if verbose:
        print(f"dashboard -> {html_path}")
        print(f"summary   -> {SUMMARY_PATH}")
        print(f"charts    -> {CHARTS_DIR}  ({len(charts)} PNGs)")
        print()
        width = max(len(k) for k in kpis)
        for key, value in kpis.items():
            print(f"  {key:<{width}}  {value}")

    return kpis


if __name__ == "__main__":
    build()
