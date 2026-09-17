"""Who moves tier: the page a non-engineer reads before new rules go live (BR-304).

Counts from -> to, and a sample of twenty moved rows as sheet-row numbers only. No candidate value
reaches the page, so it may be kept as a CI artifact or attached to a pull request. The page is
self-contained HTML: no scripts, no outside files.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from html import escape

from replay.report import TIERS

SAMPLE_SIZE = 20


@dataclass(frozen=True, slots=True)
class Move:
    sheet_row: int
    before: str
    after: str
    note: str = ""  # e.g. "ruled", never a candidate value


def sample(moves: Sequence[Move], size: int = SAMPLE_SIZE) -> list[Move]:
    """An even spread over the moved rows in sheet order, the same every run."""
    moved = sorted((m for m in moves if m.before != m.after), key=lambda m: m.sheet_row)
    if len(moved) <= size:
        return moved
    step = len(moved) / size
    return [moved[int(i * step)] for i in range(size)]


_STYLE = """
:root { --bg:#fbfaf7; --fg:#1d1d1b; --muted:#6b6a65; --line:#e3e0d8; --card:#ffffff;
        --up:#1f7a4d; --down:#b3261e; --accent:#2f4b7c; --chip:#f1efe9; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#161614; --fg:#ecebe6; --muted:#a3a19a; --line:#34332f; --card:#1f1f1c;
          --up:#5fc18f; --down:#f08a82; --accent:#9db7e6; --chip:#2a2926; }
}
* { box-sizing:border-box }
body { margin:0; background:var(--bg); color:var(--fg);
       font:15px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif }
main { max-width:960px; margin:0 auto; padding:32px 16px 64px }
h1 { font-size:28px; margin:4px 0 8px } h2 { font-size:19px; margin:36px 0 10px }
.kicker { color:var(--muted); font-size:13px; letter-spacing:.04em; text-transform:uppercase }
.lead { color:var(--muted); max-width:70ch }
.stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px;
         margin:24px 0 }
.stat { background:var(--card); border:1px solid var(--line); border-radius:10px;
        padding:12px 14px }
.stat b { display:block; font-size:24px } .stat span { color:var(--muted); font-size:13px }
.verdict { border-left:4px solid var(--accent); background:var(--card); padding:12px 16px;
           border-radius:6px }
.verdict.bad { border-color:var(--down) } .verdict.ok { border-color:var(--up) }
.scroll { overflow-x:auto }
table { border-collapse:collapse; width:100%; background:var(--card);
        font-variant-numeric:tabular-nums }
th, td { border-bottom:1px solid var(--line); padding:7px 10px; text-align:right;
         white-space:nowrap }
th:first-child, td:first-child { text-align:left }
th { color:var(--muted); font-weight:600; font-size:13px }
td.same { color:var(--muted) } td.up { color:var(--up); font-weight:600 }
td.down { color:var(--down); font-weight:600 }
.chip { display:inline-block; background:var(--chip); border-radius:99px; padding:1px 9px;
        font-size:12px; color:var(--muted) }
footer { color:var(--muted); font-size:13px; margin-top:40px }
"""


def _rank(tier: str) -> int:
    return TIERS.index(tier) if tier in TIERS else len(TIERS)


def render_html(
    *,
    title: str,
    before_label: str,
    after_label: str,
    facts: Sequence[tuple[str, str]],
    moves: Sequence[Move],
    verdict: str,
    passed: bool,
) -> str:
    """moves holds every compared row, moved or not; only the moved ones are named."""
    counts = Counter((m.before or "(none)", m.after or "(none)") for m in moves)
    moved = sum(n for (b, a), n in counts.items() if b != a)
    up = sum(n for (b, a), n in counts.items() if b != a and _rank(a) < _rank(b))
    tiers = sorted({t for pair in counts for t in pair}, key=_rank)

    def cell(before: str, after: str) -> str:
        n = counts.get((before, after), 0)
        if before == after:
            css = "same"
        elif not n:
            css = ""
        else:
            css = "up" if _rank(after) < _rank(before) else "down"
        return f'<td class="{css}">{n:,}</td>' if n else "<td>·</td>"

    matrix = "".join(
        f"<tr><th scope='row'>{escape(b)}</th>{''.join(cell(b, a) for a in tiers)}"
        f"<td>{sum(counts.get((b, a), 0) for a in tiers):,}</td></tr>"
        for b in tiers
        if any(counts.get((b, a)) for a in tiers)
    )
    pairs = "".join(
        f"<tr><td>{escape(b)} → {escape(a)}</td><td>{n:,}</td></tr>"
        for (b, a), n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        if b != a
    )
    rows = "".join(
        f"<tr><td>{m.sheet_row}</td><td>{escape(m.before)}</td><td>{escape(m.after)}</td>"
        f"<td>{'<span class=chip>' + escape(m.note) + '</span>' if m.note else ''}</td></tr>"
        for m in sample(moves)
    )
    fact_rows = "".join(f"<tr><td>{escape(k)}</td><td>{escape(v)}</td></tr>" for k, v in facts)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>{_STYLE}</style>
</head>
<body><main>
<div class="kicker">Talent Platform · rule change check · BR-304</div>
<h1>{escape(title)}</h1>
<p class="lead">Every candidate in the master record scored with <b>{escape(before_label)}</b> and
with <b>{escape(after_label)}</b>. This page shows who changes tier. It holds counts and sheet-row
numbers only.</p>
<div class="stats">
  <div class="stat"><b>{len(moves):,}</b><span>candidates compared</span></div>
  <div class="stat"><b>{moved:,}</b><span>change tier</span></div>
  <div class="stat"><b>{up:,}</b><span>move to a better tier</span></div>
  <div class="stat"><b>{moved - up:,}</b><span>move to a lower tier</span></div>
</div>
<p class="verdict {"ok" if passed else "bad"}">{escape(verdict)}</p>
<h2>Tier before (rows) against tier after (columns)</h2>
<div class="scroll"><table>
<tr><th>Before</th>{"".join(f"<th>{escape(t)}</th>" for t in tiers)}<th>Total</th></tr>
{matrix}
</table></div>
<h2>Moves, largest first</h2>
<div class="scroll"><table><tr><th>From → to</th><th>Candidates</th></tr>
{pairs or "<tr><td>Nobody changes tier.</td><td>0</td></tr>"}</table></div>
<h2>Sample of moved rows</h2>
<p class="lead">Up to {SAMPLE_SIZE}, spread evenly through the sheet. Look them up in the master
record; the record itself never leaves company machines.</p>
<div class="scroll"><table><tr><th>Sheet row</th><th>Before</th><th>After</th><th></th></tr>
{rows or '<tr><td colspan="4">No row changes tier.</td></tr>'}</table></div>
<h2>Run</h2>
<div class="scroll"><table>{fact_rows}</table></div>
<footer>A tier change is never fixed by editing the scorer to agree. The criteria owner rules
on it, and the ruling is recorded in src/replay/rulings.py.</footer>
</main></body>
</html>
"""
