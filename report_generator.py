# -*- coding: utf-8 -*-
"""
Enterprise-Grade Reporting Engine for Versa SD-WAN Router Health Auditor.
Generates:
1. Styled Excel Workbooks with Executive Summary sheet, KPI cards, Pie Charts,
   and color-coded SLA Scorecards using openpyxl.
2. Self-Contained HTML Audit Dossiers with Tata Communications branding,
   interactive SLA breakdowns, and embedded base64 screenshots for ticket attachments.
"""

import os
import re
import base64
from datetime import datetime
import pandas as pd

try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.chart import PieChart, Reference
    from openpyxl.chart.series import DataPoint
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

# ── Color Palette Definitions ──
NAVY_HEADER = "1B365D"
WHITE = "FFFFFF"
LIGHT_GRAY = "F8FAFC"
BORDER_GRAY = "D1D5DB"
PASS_GREEN_BG = "E2EFDA"
PASS_GREEN_FG = "276A3C"
FAIL_RED_BG = "FCE4D6"
FAIL_RED_FG = "A61C1C"

def generate_rich_excel(results, output_path, meta_info=None):
    """
    Creates an enterprise formatted Excel workbook with:
    1. Executive Summary tab (KPI Metrics, OpenPyXL Pie Chart, Worst-performing links)
    2. Detailed SLA Scorecard tab (Auto-color coded cells, frozen headers, auto-widths)
    """
    if not results:
        return False, "No results to export."

    if not HAS_OPENPYXL:
        try:
            df = pd.DataFrame(results)
            df.to_excel(output_path, index=False)
            return True, output_path
        except Exception as e:
            return False, str(e)

    wb = openpyxl.Workbook()
    # Remove default sheet
    default_sheet = wb.active
    
    # ── 1. Detailed SLA Scorecard Sheet ──
    ws_detail = wb.create_sheet(title="SLA Scorecard")
    
    headers = list(results[0].keys())
    ws_detail.append(headers)

    # Styling for header
    header_font = Font(name="Calibri", size=11, bold=True, color=WHITE)
    header_fill = PatternFill(start_color=NAVY_HEADER, end_color=NAVY_HEADER, fill_type="solid")
    header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    thin_border = Border(
        left=Side(style='thin', color=BORDER_GRAY),
        right=Side(style='thin', color=BORDER_GRAY),
        top=Side(style='thin', color=BORDER_GRAY),
        bottom=Side(style='thin', color=BORDER_GRAY)
    )

    for col_idx in range(1, len(headers) + 1):
        cell = ws_detail.cell(row=1, column=col_idx)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align
        cell.border = thin_border
    
    ws_detail.row_dimensions[1].height = 28

    # Add Rows & Apply Conditional Cell Styling
    pass_fill = PatternFill(start_color=PASS_GREEN_BG, end_color=PASS_GREEN_BG, fill_type="solid")
    pass_font = Font(name="Calibri", size=10, bold=True, color=PASS_GREEN_FG)
    fail_fill = PatternFill(start_color=FAIL_RED_BG, end_color=FAIL_RED_BG, fill_type="solid")
    fail_font = Font(name="Calibri", size=10, bold=True, color=FAIL_RED_FG)
    normal_font = Font(name="Calibri", size=10)

    verdict_col_idx = None
    if "SLA Verdict" in headers:
        verdict_col_idx = headers.index("SLA Verdict") + 1

    for row_idx, row_data in enumerate(results, start=2):
        row_values = [row_data.get(h, "") for h in headers]
        ws_detail.append(row_values)
        ws_detail.row_dimensions[row_idx].height = 20

        is_pass = "PASS" in str(row_data.get("SLA Verdict", "")).upper()
        is_fail = "FAIL" in str(row_data.get("SLA Verdict", "")).upper()

        for col_idx in range(1, len(headers) + 1):
            cell = ws_detail.cell(row=row_idx, column=col_idx)
            cell.border = thin_border
            cell.font = normal_font
            cell.alignment = Alignment(vertical="center")

            # Color verdict column specifically
            if col_idx == verdict_col_idx:
                cell.alignment = Alignment(horizontal="center", vertical="center")
                if is_pass:
                    cell.fill = pass_fill
                    cell.font = pass_font
                elif is_fail:
                    cell.fill = fail_fill
                    cell.font = fail_font

    # Freeze Header and Enable Auto-filter
    ws_detail.freeze_panes = "A2"
    ws_detail.auto_filter.ref = ws_detail.dimensions

    # Auto-adjust column widths
    for col in ws_detail.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            val_str = str(cell.value or "")
            if len(val_str) > max_len:
                max_len = len(val_str)
        ws_detail.column_dimensions[col_letter].width = max(max_len + 3, 12)

    # ── 2. Executive Summary Sheet ──
    ws_exec = wb.create_sheet(title="Executive Summary", index=0)
    ws_exec.views.sheetView[0].showGridLines = True

    # Title Banner
    ws_exec.merge_cells("B2:J2")
    title_cell = ws_exec["B2"]
    title_cell.value = "TATA COMMUNICATIONS — VERSA SD-WAN AUDIT EXECUTIVE SUMMARY"
    title_cell.font = Font(name="Calibri", size=14, bold=True, color=WHITE)
    title_cell.fill = PatternFill(start_color=NAVY_HEADER, end_color=NAVY_HEADER, fill_type="solid")
    title_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws_exec.row_dimensions[2].height = 36

    # Meta Info Row
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ws_exec["B3"] = f"Audit Run Timestamp: {now_str}  |  Generated by: Versa SD-WAN Health Auditor Enterprise"
    ws_exec["B3"].font = Font(name="Calibri", size=9, italic=True, color="64748B")

    # Compute Statistics
    total_tests = len(results)
    pass_tests = sum(1 for r in results if "PASS" in str(r.get("SLA Verdict", "")).upper())
    fail_tests = total_tests - pass_tests
    pass_pct = (pass_tests / total_tests * 100) if total_tests > 0 else 0.0

    unique_routers = len(set(r.get("Source Router") for r in results if r.get("Source Router")))

    # KPI Metrics Box
    kpis = [
        ("Total Routers Audited", unique_routers, "0284C7"),
        ("Total Circuit Tests", total_tests, "334155"),
        ("SLA Pass Links", pass_tests, "16A34A"),
        ("SLA Fail Links", fail_tests, "DC2626" if fail_tests > 0 else "16A34A"),
        ("SLA Compliance Rate", f"{pass_pct:.1f}%", "16A34A" if pass_pct >= 90 else "DC2626")
    ]

    start_c = 2  # Column B
    for label, val, color in kpis:
        c_letter = get_column_letter(start_c)
        next_letter = get_column_letter(start_c + 1)
        ws_exec.merge_cells(f"{c_letter}5:{next_letter}5")
        ws_exec.merge_cells(f"{c_letter}6:{next_letter}6")

        ws_exec[f"{c_letter}5"] = label
        ws_exec[f"{c_letter}5"].font = Font(name="Calibri", size=9, bold=True, color="64748B")
        ws_exec[f"{c_letter}5"].alignment = Alignment(horizontal="center", vertical="center")

        ws_exec[f"{c_letter}6"] = val
        ws_exec[f"{c_letter}6"].font = Font(name="Calibri", size=16, bold=True, color=color)
        ws_exec[f"{c_letter}6"].alignment = Alignment(horizontal="center", vertical="center")

        # Borders
        for r_i in [5, 6]:
            for c_i in [start_c, start_c + 1]:
                ws_exec.cell(row=r_i, column=c_i).border = thin_border
                ws_exec.cell(row=r_i, column=c_i).fill = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")

        start_c += 2

    # Data block for Chart
    ws_exec["B9"] = "SLA Verdict"
    ws_exec["C9"] = "Count"
    ws_exec["B10"] = "Pass"
    ws_exec["C10"] = pass_tests
    ws_exec["B11"] = "Fail"
    ws_exec["C11"] = fail_tests

    for r_i in [9, 10, 11]:
        for c_i in [2, 3]:
            ws_exec.cell(row=r_i, column=c_i).font = normal_font

    # Build OpenPyXL Pie Chart
    pie = PieChart()
    pie.title = "SLA Pass / Fail Distribution"
    labels = Reference(ws_exec, min_col=2, min_row=10, max_row=11)
    data = Reference(ws_exec, min_col=3, min_row=9, max_row=11)
    pie.add_data(data, titles_from_data=True)
    pie.set_categories(labels)
    pie.width = 14
    pie.height = 7.5

    # Color slices (Pass=Green, Fail=Red)
    slice_pass = DataPoint(idx=0)
    slice_pass.graphicalProperties.solidFill = "22C55E"
    slice_fail = DataPoint(idx=1)
    slice_fail.graphicalProperties.solidFill = "EF4444"
    pie.series[0].data_points = [slice_pass, slice_fail]

    ws_exec.add_chart(pie, "E9")

    # Worst-Performing Links Table (Under Chart)
    ws_exec["B19"] = "WORST-PERFORMING / FAILED ACCESS CIRCUITS"
    ws_exec["B19"].font = Font(name="Calibri", size=11, bold=True, color=NAVY_HEADER)

    worst_headers = ["Router", "Destination Site", "Circuit", "Packet Loss (%)", "Avg RTT (ms)", "SLA Verdict"]
    for c_i, h in enumerate(worst_headers, start=2):
        c = ws_exec.cell(row=20, column=c_i)
        c.value = h
        c.font = Font(name="Calibri", size=10, bold=True, color=WHITE)
        c.fill = PatternFill(start_color="334155", end_color="334155", fill_type="solid")
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.border = thin_border

    # Sort results by Packet Loss desc, Avg RTT desc
    def _sort_key(r):
        loss = float(r.get("Packet Loss (%)", 0.0) or 0.0)
        rtt = float(r.get("Avg RTT (ms)", 0.0) or 0.0)
        return (loss, rtt)

    sorted_results = sorted(results, key=_sort_key, reverse=True)
    worst_links = [r for r in sorted_results if "FAIL" in str(r.get("SLA Verdict", "")).upper()][:10]
    if not worst_links:
        worst_links = sorted_results[:5]  # If none failed, show top 5 highest latency

    for r_idx, w in enumerate(worst_links, start=21):
        vals = [
            w.get("Source Router", ""),
            w.get("Destination Site", ""),
            w.get("Access Circuit", ""),
            f"{float(w.get('Packet Loss (%)', 0.0) or 0.0):.1f}%",
            f"{float(w.get('Avg RTT (ms)', 0.0) or 0.0):.3f} ms" if w.get("Avg RTT (ms)") is not None else "Timeout",
            w.get("SLA Verdict", "")
        ]
        is_f = "FAIL" in str(w.get("SLA Verdict", "")).upper()
        for c_idx, val in enumerate(vals, start=2):
            cell = ws_exec.cell(row=r_idx, column=c_idx)
            cell.value = val
            cell.border = thin_border
            cell.font = fail_font if (is_f and c_idx == 7) else normal_font
            cell.fill = fail_fill if (is_f and c_idx == 7) else PatternFill(fill_type=None)
            cell.alignment = Alignment(horizontal="center" if c_idx >= 5 else "left", vertical="center")

    # Column widths for Summary
    for col_i in range(1, 12):
        col_letter = get_column_letter(col_i)
        ws_exec.column_dimensions[col_letter].width = 16

    wb.remove(default_sheet)
    wb.save(output_path)
    return True, output_path


def generate_html_dossier(results, output_path, screenshot_dir=None, meta_info=None):
    """
    Generates a single self-contained HTML audit dossier with embedded base64 screenshots,
    Tata Communications branding, executive KPI cards, and ticket-ready SLA breakdown.
    """
    if not results:
        return False, "No results to export."

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total_tests = len(results)
    pass_tests = sum(1 for r in results if "PASS" in str(r.get("SLA Verdict", "")).upper())
    fail_tests = total_tests - pass_tests
    pass_pct = (pass_tests / total_tests * 100) if total_tests > 0 else 0.0
    unique_routers = sorted(list(set(r.get("Source Router") for r in results if r.get("Source Router"))))

    # Helper to encode image to base64
    def _img_to_b64(path):
        if path and os.path.isfile(path):
            try:
                with open(path, "rb") as f:
                    return f"data:image/png;base64,{base64.b64encode(f.read()).decode('utf-8')}"
            except Exception:
                return None
        return None

    # Collect Evidence Screenshots
    router_evidence = {}
    for r_name in unique_routers:
        r_dir = os.path.join(screenshot_dir or os.path.join(os.getcwd(), "screenshots"), r_name)
        if os.path.isdir(r_dir):
            ss_files = sorted([os.path.join(r_dir, f) for f in os.listdir(r_dir) if f.lower().endswith(('.png', '.jpg'))])
            if ss_files:
                router_evidence[r_name] = []
                for sf in ss_files:
                    b64 = _img_to_b64(sf)
                    if b64:
                        router_evidence[r_name].append({
                            "name": os.path.basename(sf).replace(".png", ""),
                            "b64": b64
                        })

    tata_logo_b64 = _img_to_b64(os.path.join(os.getcwd(), "logo_header.png")) or _img_to_b64(os.path.join(os.getcwd(), "logo.jpg"))
    logo_tag = f'<img src="{tata_logo_b64}" style="height: 44px; background: white; padding: 4px 8px; border-radius: 6px; vertical-align: middle;" alt="TATA">' if tata_logo_b64 else ''

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Versa SD-WAN Audit Dossier - {now_str}</title>
<style>
    :root {{
        --primary: #1B365D;
        --primary-dark: #0f172a;
        --success: #16a34a;
        --danger: #dc2626;
        --warning: #f59e0b;
        --bg: #f8fafc;
        --card-bg: #ffffff;
        --text: #1e293b;
        --muted: #64748b;
        --border: #e2e8f0;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; }}
    body {{ background: var(--bg); color: var(--text); padding: 24px; line-height: 1.5; }}
    .container {{ max-width: 1400px; margin: 0 auto; }}
    
    /* Header */
    .header {{
        background: linear-gradient(135deg, var(--primary) 0%, var(--primary-dark) 100%);
        color: white;
        padding: 28px 32px;
        border-radius: 12px;
        box-shadow: 0 4px 14px rgba(0,0,0,0.1);
        margin-bottom: 24px;
        display: flex;
        justify-content: space-between;
        align-items: center;
        flex-wrap: wrap;
        gap: 16px;
    }}
    .header h1 {{ font-size: 24px; font-weight: 700; letter-spacing: -0.5px; }}
    .header p {{ color: #94a3b8; font-size: 13px; margin-top: 4px; }}
    .header-badge {{
        background: rgba(255, 255, 255, 0.15);
        border: 1px solid rgba(255, 255, 255, 0.25);
        padding: 8px 16px;
        border-radius: 20px;
        font-size: 13px;
        font-weight: 600;
    }}

    /* KPI Cards */
    .kpi-grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        gap: 16px;
        margin-bottom: 28px;
    }}
    .kpi-card {{
        background: var(--card-bg);
        border: 1px solid var(--border);
        border-radius: 10px;
        padding: 20px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }}
    .kpi-card .label {{ font-size: 12px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; }}
    .kpi-card .value {{ font-size: 28px; font-weight: 800; margin-top: 6px; }}
    .val-primary {{ color: var(--primary); }}
    .val-success {{ color: var(--success); }}
    .val-danger {{ color: var(--danger); }}

    /* Section Cards */
    .card {{
        background: var(--card-bg);
        border: 1px solid var(--border);
        border-radius: 10px;
        padding: 24px;
        margin-bottom: 28px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }}
    .card-title {{
        font-size: 18px;
        font-weight: 700;
        margin-bottom: 16px;
        color: var(--primary);
        display: flex;
        align-items: center;
        gap: 8px;
    }}

    /* Table Styling */
    .table-wrapper {{ overflow-x: auto; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; text-align: left; }}
    th {{ background: #f1f5f9; color: #475569; font-weight: 600; padding: 10px 14px; border-bottom: 2px solid var(--border); white-space: nowrap; }}
    td {{ padding: 10px 14px; border-bottom: 1px solid var(--border); }}
    tr:hover td {{ background: #f8fafc; }}

    .badge {{
        display: inline-block;
        padding: 3px 8px;
        border-radius: 4px;
        font-size: 11px;
        font-weight: 700;
        text-transform: uppercase;
    }}
    .badge-pass {{ background: #dcfce7; color: #15803d; }}
    .badge-fail {{ background: #fee2e2; color: #b91c1c; }}

    /* Screenshot Gallery */
    .gallery {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(360px, 1fr)); gap: 16px; margin-top: 14px; }}
    .gallery-item {{
        border: 1px solid var(--border);
        border-radius: 8px;
        overflow: hidden;
        background: #0f172a;
    }}
    .gallery-item img {{ width: 100%; display: block; object-fit: contain; }}
    .gallery-title {{
        background: #1e293b;
        color: white;
        padding: 8px 12px;
        font-size: 12px;
        font-weight: 600;
    }}

    /* Print & ticket styling */
    @media print {{
        body {{ background: white; padding: 0; }}
        .header {{ box-shadow: none; border-radius: 0; }}
        .card {{ box-shadow: none; border: 1px solid #ccc; page-break-inside: avoid; }}
    }}
</style>
</head>
<body>
<div class="container">

    <!-- Header Banner -->
    <div class="header">
        <div style="display: flex; align-items: center; gap: 16px;">
            {logo_tag}
            <div>
                <h1>Tata Communications • Versa SD-WAN Audit Dossier</h1>
                <p>Automated Health & Rapid Ping Diagnostic Report | Generated: {now_str}</p>
            </div>
        </div>
        <div class="header-badge">
            SLA Compliance: {pass_pct:.1f}% ({pass_tests}/{total_tests} Links)
        </div>
    </div>

    <!-- Executive KPI Summary -->
    <div class="kpi-grid">
        <div class="kpi-card">
            <div class="label">Total Routers Audited</div>
            <div class="value val-primary">{len(unique_routers)}</div>
        </div>
        <div class="kpi-card">
            <div class="label">Total Circuit Tests</div>
            <div class="value">{total_tests}</div>
        </div>
        <div class="kpi-card">
            <div class="label">Passed Links (SLA Met)</div>
            <div class="value val-success">{pass_tests}</div>
        </div>
        <div class="kpi-card">
            <div class="label">Failed Links (SLA Violated)</div>
            <div class="value val-danger">{fail_tests}</div>
        </div>
    </div>

    <!-- Full SLA Scorecard Table -->
    <div class="card">
        <div class="card-title">📊 Granular Circuit Audit Scorecard</div>
        <div class="table-wrapper">
            <table>
                <thead>
                    <tr>
                        <th>Source Router</th>
                        <th>Destination Site</th>
                        <th>Circuit</th>
                        <th>Destination IP</th>
                        <th>Control BGP</th>
                        <th>BGP Uptime</th>
                        <th>WAN Interface</th>
                        <th>Loss (%)</th>
                        <th>Avg RTT</th>
                        <th>Verdict</th>
                    </tr>
                </thead>
                <tbody>
"""

    for r in results:
        loss = float(r.get("Packet Loss (%)", 0.0) or 0.0)
        rtt_val = r.get("Avg RTT (ms)")
        rtt_str = f"{float(rtt_val):.2f} ms" if rtt_val is not None else "Timeout"
        is_pass = "PASS" in str(r.get("SLA Verdict", "")).upper()
        verdict_badge = '<span class="badge badge-pass">PASS</span>' if is_pass else '<span class="badge badge-fail">FAIL</span>'

        html += f"""                    <tr>
                        <td><strong>{r.get("Source Router", "")}</strong></td>
                        <td>{r.get("Destination Site", "")}</td>
                        <td>{r.get("Access Circuit", "")}</td>
                        <td><code>{r.get("Destination IP", "")}</code></td>
                        <td>{r.get("Control BGP (IP/Uptime)", "N/A")}</td>
                        <td>{r.get("BGP Uptime", "N/A")}</td>
                        <td>{r.get("WAN Interface", "N/A")}</td>
                        <td>{loss:.1f}%</td>
                        <td>{rtt_str}</td>
                        <td>{verdict_badge}</td>
                    </tr>\n"""

    html += """                </tbody>
            </table>
        </div>
    </div>
"""

    # Embedded Screenshots Section
    if router_evidence:
        html += """    <!-- Diagnostic Evidence Captures -->
    <div class="card">
        <div class="card-title">📷 Attached Terminal Evidence Captures (Embedded)</div>
        <p style="font-size: 12px; color: var(--muted); margin-bottom: 16px;">
            The following terminal screen captures are self-contained and embedded directly into this document for ticket attachment.
        </p>
"""
        for r_name, captures in router_evidence.items():
            html += f"""        <h3 style="font-size: 15px; margin: 20px 0 10px 0; color: #334155;">Router: {r_name} ({len(captures)} captures)</h3>
        <div class="gallery">
"""
            for cap in captures:
                html += f"""            <div class="gallery-item">
                <div class="gallery-title">{cap["name"]}</div>
                <img src="{cap["b64"]}" alt="{cap["name"]}" loading="lazy" />
            </div>
"""
            html += "        </div>\n"
        html += "    </div>\n"

    html += """
    <div style="text-align: center; font-size: 12px; color: var(--muted); padding: 20px 0;">
        Tata Communications Enterprise Network Engineering • Confidential • Generated by Versa SD-WAN Auditor
    </div>
</div>
</body>
</html>
"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    return True, output_path
