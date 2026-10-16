"""
build_dashboard.py  -  STEP 4
Turns results.json (from run_batch.py) into one self-contained dashboard.html.
No server, no installs: just open the HTML file in your browser.

Usage:
  python build_dashboard.py
  python build_dashboard.py --results results.json --out dashboard.html --open
"""

import argparse
import json
import sys
import webbrowser
from pathlib import Path

TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fresher Job Dashboard</title>
<style>
  :root { --bg:#f6f7f9; --card:#fff; --text:#1c2230; --muted:#667085; --line:#e3e6ec;
          --accent:#2457d6; --ok:#12805c; --okbg:#e3f5ee; --warn:#b25e09; --warnbg:#fdf0dc;
          --bad:#b42318; --badbg:#fde7e4; --chip:#eef1f6; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#12151c; --card:#1b2029; --text:#e8ebf1; --muted:#9aa4b5; --line:#2a313d;
            --accent:#7aa2ff; --ok:#5fd0a5; --okbg:#143327; --warn:#f0b35a; --warnbg:#3a2a12;
            --bad:#ff8e84; --badbg:#3d1a17; --chip:#242b37; }
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--text);
         font:15px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }
  .wrap { max-width:1000px; margin:0 auto; padding:20px 16px 60px; }
  h1 { font-size:22px; margin:0 0 4px; }
  .sub { color:var(--muted); font-size:13px; margin-bottom:14px; }
  .banner { background:var(--warnbg); color:var(--warn); border-radius:8px; padding:10px 12px; margin-bottom:14px; font-size:14px; }
  .filters { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:14px; }
  .filters input, .filters select { padding:8px 10px; border:1px solid var(--line); border-radius:8px;
         background:var(--card); color:var(--text); font-size:14px; }
  .filters input { flex:1 1 220px; }
  .count { color:var(--muted); font-size:13px; margin:0 0 10px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 14px; margin-bottom:10px; }
  .title { font-weight:600; font-size:16px; color:var(--accent); text-decoration:none; }
  .title:hover { text-decoration:underline; }
  .meta { color:var(--muted); font-size:13px; margin:2px 0 8px; }
  .badges { display:flex; flex-wrap:wrap; gap:6px; }
  .b { font-size:12px; padding:2px 8px; border-radius:999px; background:var(--chip); color:var(--text); }
  .b.ok { background:var(--okbg); color:var(--ok); }
  .b.warn { background:var(--warnbg); color:var(--warn); }
  .note { font-size:12px; color:var(--warn); margin-top:6px; }
  .empty { color:var(--muted); padding:24px; text-align:center; border:1px dashed var(--line); border-radius:10px; }
  h2 { font-size:15px; margin:26px 0 8px; }
  .panel { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:10px 14px; font-size:13px; }
  .panel .row { padding:4px 0; border-bottom:1px solid var(--line); }
  .panel .row:last-child { border-bottom:none; }
  .bad { color:var(--bad); }
  .live { display:flex; align-items:center; gap:10px; margin-bottom:14px; font-size:13px; color:var(--muted); }
  .live button { padding:8px 14px; border:none; border-radius:8px; background:var(--accent); color:#fff; font-size:14px; cursor:pointer; }
  .live button:disabled { opacity:.55; cursor:default; }
</style>
</head>
<body>
<div class="wrap">
  <h1>Fresher Job Dashboard</h1>
  <div class="sub" id="sub"></div>
  <!--LIVE-->
  <div class="banner" id="stale" hidden></div>
  <div class="filters">
    <input id="q" type="search" placeholder="Search title, company, location">
    <select id="role"></select>
    <select id="company"></select>
    <select id="type"><option value="">Job + internship</option><option value="full-time">Full-time only</option><option value="internship">Internship only</option></select>
    <select id="exp"><option value="">Any experience tag</option><option value="fit">Experience fits</option><option value="unspecified">Experience unspecified</option></select>
    <select id="match"><option value="">Exact + related titles</option><option value="exact">Exact title only</option></select>
    <select id="sort"><option value="best">Best match first</option><option value="new">Newest first</option></select>
  </div>
  <p class="count" id="count"></p>
  <div id="list"></div>
  <h2 class="bad">Could not check (these are NOT empty results)</h2>
  <div class="panel" id="broken"></div>
  <h2>Reached, nothing matching in the window</h2>
  <div class="panel" id="quiet"></div>
</div>

<script id="data" type="application/json">__DATA__</script>
<script>
const data = JSON.parse(document.getElementById('data').textContent);
const $ = id => document.getElementById(id);

// Merge the same job found under several roles.
const jobs = [], seen = new Map();
for (const r of data.results) for (const j of r.jobs) {
  if (seen.has(j.url)) { seen.get(j.url).roles.add(r.role); continue; }
  const o = Object.assign({}, j, { company: r.company, roles: new Set([r.role]) });
  seen.set(j.url, o); jobs.push(o);
}

function fill(sel, first, values) {
  sel.appendChild(new Option(first, ''));
  [...new Set(values)].sort().forEach(v => sel.appendChild(new Option(v, v)));
}
fill($('role'), 'All roles', data.profile.roles || []);
fill($('company'), 'All companies', data.results.map(r => r.company));

// Header + staleness warning
const gen = new Date(data.generated_at);
const hours = (Date.now() - gen.getTime()) / 3600000;
$('sub').textContent = 'Updated ' + gen.toLocaleString() + ' | last ' + data.profile.days +
  ' days | up to ' + data.profile.max_exp + ' yrs experience | internships ' +
  (data.profile.include_internships ? 'included' : 'excluded');
if (data.never_run) $('sub').textContent = 'No search has been run yet. Click "Refresh now" to start.';
if (hours > 24 && !data.never_run) {
  $('stale').hidden = false;
  $('stale').textContent = 'These results are ' + Math.round(hours) +
    ' hours old. Jobs go stale in about 3 days - run run_batch.py and build_dashboard.py again.';
}

function badge(text, cls) { const s = document.createElement('span'); s.className = 'b ' + (cls || ''); s.textContent = text; return s; }

function card(j) {
  const d = document.createElement('div'); d.className = 'card';
  const a = document.createElement('a'); a.className = 'title'; a.textContent = j.title;
  if (/^https?:\/\//i.test(j.url)) { a.href = j.url; a.target = '_blank'; a.rel = 'noopener noreferrer'; }
  d.appendChild(a);
  const m = document.createElement('div'); m.className = 'meta';
  m.textContent = j.company + ' | ' + (j.location || 'location n/a') + ' | ' + j.age_text + ' | via ' + j.source;
  d.appendChild(m);
  const b = document.createElement('div'); b.className = 'badges';
  b.appendChild(badge(j.match === 'exact' ? 'Exact title' : 'Related title', j.match === 'exact' ? 'ok' : ''));
  b.appendChild(badge(j.job_type === 'internship' ? 'Internship' : 'Full-time'));
  if (j.experience === 'fit') b.appendChild(badge('Experience fits', 'ok'));
  else b.appendChild(badge(j.description ? 'Experience not stated' : 'Experience unverified', 'warn'));
  if (j.date_kind !== 'published') b.appendChild(badge('Date approximate', 'warn'));
  [...j.roles].forEach(r => b.appendChild(badge('for: ' + r)));
  d.appendChild(b);
  if (j.note) { const n = document.createElement('div'); n.className = 'note'; n.textContent = j.note; d.appendChild(n); }
  return d;
}

function render() {
  const q = $('q').value.trim().toLowerCase(), role = $('role').value, co = $('company').value;
  const type = $('type').value, exp = $('exp').value, match = $('match').value;
  let rows = jobs.filter(j =>
    (!q || (j.title + ' ' + j.company + ' ' + j.location).toLowerCase().includes(q)) &&
    (!role || j.roles.has(role)) && (!co || j.company === co) &&
    (!type || j.job_type === type) && (!exp || j.experience === exp) && (!match || j.match === match));
  if ($('sort').value === 'new')
    rows.sort((a, b) => (b.posted ? Date.parse(b.posted) : 0) - (a.posted ? Date.parse(a.posted) : 0));
  $('count').textContent = rows.length + ' of ' + jobs.length + ' job(s)';
  const list = $('list'); list.replaceChildren();
  if (!rows.length) {
    const e = document.createElement('div'); e.className = 'empty';
    e.textContent = data.never_run ? 'Nothing to show yet - run a search first.' : jobs.length ? 'No jobs match these filters.' : 'No matching jobs in this window. That is normal - check back after the next run.';
    list.appendChild(e);
  }
  rows.forEach(j => list.appendChild(card(j)));
}

function panel(el, items, emptyText) {
  el.replaceChildren();
  if (!items.length) { el.textContent = emptyText; return; }
  items.forEach(t => { const r = document.createElement('div'); r.className = 'row'; r.textContent = t; el.appendChild(r); });
}
panel($('broken'),
  data.results.filter(r => r.status === 'unreachable').map(r => r.company + ' (' + r.role + '): ' + (r.reason || 'unknown reason')),
  'Every company was reachable.');
panel($('quiet'),
  data.results.filter(r => r.status === 'no_role').map(r => r.company + ' (' + r.role + ')'),
  'None.');

['q','role','company','type','exp','match','sort'].forEach(id => $(id).addEventListener('input', render));
render();
</script>
</body>
</html>
"""


LIVE_TOOLBAR = r"""<div class="live"><button id="refresh">Refresh now</button><span id="rstatus"></span></div>
<script>
(function () {
  const btn = document.getElementById('refresh'), st = document.getElementById('rstatus');
  async function poll() {
    let s;
    try { s = await (await fetch('/status')).json(); } catch (e) { st.textContent = 'Server not reachable.'; return; }
    if (s.running) {
      btn.disabled = true; st.textContent = 'Searching portals... this can take a minute or two.';
      setTimeout(poll, 2000); return;
    }
    btn.disabled = false;
    if (sessionStorage.getItem('refreshing')) {
      sessionStorage.removeItem('refreshing');
      if (!s.error) { location.reload(); return; }
    }
    st.textContent = s.error ? 'Last run failed: ' + s.error : '';
  }
  btn.onclick = async function () {
    btn.disabled = true; sessionStorage.setItem('refreshing', '1');
    try { await fetch('/refresh', { method: 'POST' }); } catch (e) {}
    poll();
  };
  poll();
})();
</script>"""


def build_html(data: dict, live: bool = False) -> str:
    # "</" is escaped so no job title can ever close the <script> tag early.
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = TEMPLATE.replace("__DATA__", payload)
    return html.replace("<!--LIVE-->", LIVE_TOOLBAR if live else "")


def main() -> int:
    p = argparse.ArgumentParser(description="Build dashboard.html from results.json")
    p.add_argument("--results", default="results.json")
    p.add_argument("--out", default="dashboard.html")
    p.add_argument("--open", action="store_true", help="open the dashboard in your browser")
    args = p.parse_args()
    try:
        with open(args.results, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"Cannot read {args.results}: {e}\nRun run_batch.py first.")
        return 1
    Path(args.out).write_text(build_html(data), encoding="utf-8")
    jobs = sum(len(r["jobs"]) for r in data["results"])
    print(f"Wrote {args.out} ({jobs} job entries from {len(data['results'])} searches)")
    if args.open:
        webbrowser.open(Path(args.out).resolve().as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())