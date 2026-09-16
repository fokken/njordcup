"""Self-contained, script-free HTML rendering of a saved audit summary."""
from datetime import datetime, timezone
from html import escape
import os
import tempfile


def write_html(path, summary, implementation=False):
    """Replace reports atomically with private permissions, like audit memory."""
    rendered = render_implementation_html(summary) if implementation else render_html(summary)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as handle:
            temporary = handle.name
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def e(value):
    return escape(str(value), quote=True)


def render_html(summary):
    severity_order = {name: i for i, name in enumerate(('critical', 'high', 'medium', 'low'))}
    findings = sorted(summary['findings'], key=lambda f: (severity_order.get(f['severity'], 4), f['path'], f['line']))
    cards = []
    for number, finding in enumerate(findings, 1):
        level = finding['severity'] if finding['severity'] in severity_order else 'unknown'
        cards.append(f'''<article class="finding" id="finding-{number}">
<div class="eyebrow"><span class="badge {level}">{e(finding['severity'])}</span> {e(finding['cwe'])} · Finding {number}</div>
<h3>{e(finding['title'])}</h3><p class="location">{e(finding['path'])}:{e(finding['line'])}</p>
<h4>Evidence</h4><pre><code>{e(finding.get('evidence', ''))}</code></pre>
<div class="columns"><div><h4>Attack scenario</h4><p>{e(finding.get('attack_scenario', ''))}</p></div>
<div><h4>Remediation</h4><p>{e(finding.get('remediation', ''))}</p></div></div>
<p class="muted">Review areas: {e(', '.join(map(str, finding.get('area_ids', []))))}</p></article>''')
    rows = []
    for area in summary['areas']:
        coverage = summary.get('area_coverage', {}).get(str(area['id']), {})
        def fraction(key):
            return (f"{coverage.get(key + '_reviewed', 0):,} / {coverage[key + '_total']:,}"
                    if key + '_total' in coverage else '—')
        rows.append(f"<tr><td>{e(area['id'])}. {e(area['title'])}</td><td>{e(area['status'])}</td>"
                    f"<td>{e(fraction('lines'))}</td><td>{e(fraction('chunks'))}</td><td>{e(area['findings'])}</td></tr>")
    gaps = [f"Area {item['area_id']}: {item['detail']}" for key in ('limitations', 'errors') for item in summary.get(key, [])]
    gaps.extend(summary.get('flyover_unknowns', []))
    gaps_html = ''.join(f'<li>{e(item)}</li>' for item in dict.fromkeys(gaps)) or '<li>No limitations recorded.</li>'
    scanner_html = ''
    scanner = summary.get('sarif')
    if scanner:
        dispositions = []
        for result in scanner['results']:
            evidence = ''.join(f"<p>{e(c['role'])}: {e(c['path'])}:{e(c['line'])}</p><pre>{e(c['quote'])}</pre>"
                               for c in result.get('evidence', []))
            dispositions.append(f"<details><summary>{e(result['status'])} · {e(result['rule_id'])} · "
                                f"{e(result['path'])}:{e(result['line'])}</summary><p>{e(result.get('reason', ''))}</p>{evidence}</details>")
        scanner_html = '<section><h2>Scanner investigations</h2><p>' + e(', '.join(
            f'{count} {status}' for status, count in scanner['counts'].items())) + '</p>' + ''.join(dispositions) + '</section>'
    counts = summary['area_counts']
    metrics = ''.join(f'<div class="metric"><strong>{value}</strong><span>{label}</span></div>' for value, label in (
        (len(findings), 'Potential issues'), (summary['findings_by_severity']['critical'], 'Critical'),
        (summary['findings_by_severity']['high'], 'High'), (f"{counts['complete']} / {counts['total']}", 'Areas complete')))
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    content = f'''<header><div class="brand">njordcup / Security review</div><h1>Audit report</h1>
<p>{e(summary['summary'])}</p><p class="muted">Audit status: {e(summary['audit_status'])} · Generated {e(now)}<br>
Last saved review: {e(summary.get('last_review_at') or 'No focused reviews yet')}</p>
<p>Saved snapshot only. Findings are potential security issues supported by reviewed source; they require human assessment. Coverage records processing, not proof of security.</p></header>
<div class="metrics">{metrics}</div>
<section><h2>Identified issues</h2>{''.join(cards) or '<p>No issues recorded in the latest review attempts. Pending or incomplete areas may still contain vulnerabilities.</p>'}</section>
<section><h2>Review coverage</h2><p>Reviewed / total within each area. Areas can overlap; these counts must not be added into a repository-wide percentage. SARIF reviews cover reported chunks.</p>
<div class="table-wrap"><table><thead><tr><th>Area</th><th>Status</th><th>Lines</th><th>Chunks</th><th>Issues</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></section>
{scanner_html}<section><h2>Limitations and outstanding questions</h2><ul>{gaps_html}</ul></section>
<footer><p>Technology stack: {e(', '.join(summary['tech_stack']) or 'Not mapped')}<br>
Snapshot: {e(summary['snapshot_fingerprint'])}<br>{e(summary['snapshot_note'])}</p></footer>
'''
    return document('Security audit report', content)


def document(title, content):
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>njordcup — {e(title)}</title>
<style>
:root{{color-scheme:light;--ink:#182b40;--muted:#53677a;--line:#dce4eb;--accent:#116a74}}
*{{box-sizing:border-box}}body{{margin:0;background:#f1f5f8;color:var(--ink);font:16px/1.6 system-ui,sans-serif}}
main{{max-width:1100px;margin:auto;padding:48px 24px}}header{{border-top:6px solid var(--accent);padding:30px 0}}
.brand,.eyebrow{{text-transform:uppercase;font-size:.8rem;letter-spacing:.09em;font-weight:700}}
h1{{font-size:clamp(2rem,5vw,3.3rem);line-height:1.1;margin:12px 0}}h2{{font-size:1.5rem}}h3{{font-size:1.25rem;margin:12px 0}}h4{{margin:18px 0 6px}}
p{{overflow-wrap:anywhere;white-space:pre-wrap}}.muted,footer{{color:var(--muted);font-size:.9rem}}
.metrics,.columns{{display:grid;grid-template-columns:repeat(4,1fr);gap:16px}}.columns{{grid-template-columns:1fr 1fr}}
.metric,.finding,section{{background:white;border:1px solid var(--line);border-radius:12px;padding:24px}}
.metric strong{{display:block;font-size:2rem}}.metric span{{color:var(--muted)}}section{{margin:24px 0}}
.finding{{margin:18px 0;border-left:4px solid var(--accent)}}.badge{{display:inline-block;padding:3px 10px;border-radius:5px;background:#e7edf3}}
.critical{{background:#ffe1e5;color:#941c35}}.high{{background:#fff0df;color:#9c4a00}}.medium{{background:#fff7ce;color:#755500}}.low{{background:#e1f1fb;color:#225879}}
.location,pre{{font-family:ui-monospace,monospace}}pre{{background:#f3f6f9;border:1px solid var(--line);padding:16px;border-radius:6px;white-space:pre-wrap;overflow-wrap:anywhere}}
.table-wrap{{overflow-x:auto}}table{{width:100%;border-collapse:collapse;text-align:left}}th,td{{padding:12px;border-bottom:1px solid var(--line)}}th{{font-size:.85rem;color:var(--muted)}}
details{{border-top:1px solid var(--line);padding:16px 0}}summary{{cursor:pointer;overflow-wrap:anywhere}}a{{color:var(--accent)}}
@media(max-width:650px){{.metrics{{grid-template-columns:1fr 1fr}}.columns{{grid-template-columns:1fr}}main{{padding:20px 12px}}section,.finding{{padding:18px}}}}
@media print{{body{{background:white}}main{{padding:0;max-width:none}}.finding{{break-inside:avoid}}section{{border:0;padding:0}}details{{display:block}}}}
</style></head><body><main>
{content}</main></body></html>'''



def render_implementation_html(report):
    """Render implementation results without reading security memory or source."""
    from .errors import ReviewError
    from .implementation import SCHEMA
    from .provider import validate
    if not isinstance(report, dict) or report.get('kind') != 'implementation_analysis' or report.get('version') != 1:
        raise ReviewError('Expected a saved implementation-analysis result')
    try:
        pages = list(report['pages'].values())
        sections, rows = [], []
        completed = 0
        def listing(values, empty='None recorded.'):
            return '<ul>' + (''.join(f'<li>{e(value)}</li>' for value in values) or f'<li>{empty}</li>') + '</ul>'
        for number, page in enumerate(pages, 1):
            status = page['status']
            rows.append(f"<tr><td><a href=\"#component-{number}\">{e(page['component'])}</a></td>"
                        f"<td>{e(status)}</td><td>{e(len(page['paths']))}</td>"
                        f"<td>{e(page.get('coverage', {}).get('sampled_files', '—'))}</td></tr>")
            if status != 'complete':
                sections.append(f'<section id="component-{number}"><h2>{e(page["component"])}</h2><p>Implementation description pending.</p></section>')
                continue
            completed += 1
            analysis = page['analysis']
            validate(analysis, SCHEMA)
            features = ''.join(f"<h3>{e(area['title'])}</h3><p>{e(area['reason'])}</p>"
                               + listing(area['features']) + f"<p class=\"muted\">Source: {e(', '.join(area['paths']))}</p>"
                               for area in analysis['areas'])
            dependencies = listing([f"{d['name']} — {d['evidence_path']}" for d in analysis['dependencies']])
            sections.append(f'''<section id="component-{number}"><div class="eyebrow">Component page {number}</div>
<h2>{e(page['component'])}</h2><p>{e(analysis['summary'])}</p>
<p class="muted">Languages: {e(', '.join(analysis['languages']))}<br>Stack: {e(', '.join(analysis['tech_stack']))}</p>
<h3>Important functionality</h3>{features or '<p>No functionality descriptions recorded.</p>'}
<h3>Implementation details</h3>{listing(analysis['implementation_details'])}
<div class="columns"><div><h3>Data and control flows</h3>{listing(analysis['data_flows'])}</div>
<div><h3>Dependencies</h3>{dependencies}</div></div>
<h3>Unknowns and limitations</h3>{listing(analysis['unknowns'])}
<details><summary>Source files in this page</summary>{listing(page['paths'])}</details></section>''')
        errors = '<section><h2>Analysis errors</h2>' + listing(report['errors']) + '</section>' if report.get('errors') else ''
        content = f'''<header><div class="brand">njordcup / Implementation analysis</div><h1>Implementation report</h1>
<p>{e(report['summary'])}</p><p class="muted">Analysis status: {e(report['status'])} · Saved {e(report.get('saved_at', 'unknown'))}</p>
<p>This describes a saved source snapshot using bounded samples. It is separate from security findings and does not establish security-review coverage.</p></header>
<div class="metrics"><div class="metric"><strong>{completed} / {len(pages)}</strong><span>Pages described</span></div>
<div class="metric"><strong>{len(report.get('languages', []))}</strong><span>Languages identified</span></div>
<div class="metric"><strong>{e(report.get('index_stats', {}).get('files', '—'))}</strong><span>Files indexed</span></div>
<div class="metric"><strong>{len(pages) - completed}</strong><span>Pages pending</span></div></div>
<section><h2>Languages and technology stack</h2><div class="columns"><div><h3>Languages</h3>{listing(report.get('languages', []))}</div>
<div><h3>Technology stack</h3>{listing(report.get('tech_stack', []))}</div></div></section>
<section><h2>Component coverage</h2><p>Pages describe sampled implementation, not every function or source line.</p>
<div class="table-wrap"><table><thead><tr><th>Component</th><th>Status</th><th>Files</th><th>Sampled files</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></section>
{''.join(sections)}{errors}<footer><p>Snapshot: {e(report['fingerprint'])}<br>Current filesystem contents have not been checked.</p></footer>'''
    except (KeyError, TypeError, AttributeError) as exc:
        raise ReviewError('Malformed implementation-analysis result') from exc
    return document('Implementation analysis report', content)
