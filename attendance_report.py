"""
Convert ALOG_001.txt attendance log to a PDF attendance report.
Fixed: exactly 2 pages, 2 employees per page, no split rows, no blank page.
"""

import re
import sys
from datetime import datetime, timedelta
from collections import defaultdict

from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, KeepTogether
)


# ---------------------------------------------------------------------------
# Tunable constants
# ---------------------------------------------------------------------------
FONT_NAME = 'Helvetica'
FONT_SIZE = 6.5
PADDING   = 3
MIN_DAY_W = 8 * mm

EXCLUDED_ENNOS = {"00000004"}   # pratik

HEADER_BG    = colors.HexColor("#1F3864")
HEADER_FG    = colors.white
WEEKEND_BG   = colors.HexColor("#EFEFEF")
ABSENT_BG    = colors.HexColor("#FCE4E4")
PRESENT_BG   = colors.HexColor("#E2EFDA")
BORDER_CLR   = colors.HexColor("#9CA3AF")
ROW_LABEL_BG = colors.HexColor("#F3F4F6")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def parse_alog(filepath):
    content = None
    for encoding in ('utf-16', 'utf-8-sig', 'utf-8', 'cp1252', 'latin-1'):
        try:
            with open(filepath, 'r', encoding=encoding) as f:
                content = f.read()
            print(f"  Detected encoding: {encoding}")
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    if content is None:
        raise ValueError(f"Could not decode {filepath}")

    records = []
    for line in content.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split('\t')
        if len(parts) < 10:
            parts = re.split(r'\s+', line, maxsplit=9)
        if len(parts) < 10:
            continue
        try:
            records.append({
                'enno':     parts[2].strip(),
                'name':     parts[3].strip(),
                'inout':    parts[6].strip(),
                'datetime': parts[9].strip(),
            })
        except IndexError:
            continue
    return records


def build_employee_map(records):
    emp_map = {}
    for r in records:
        enno, name = r['enno'], r['name']
        if name and (enno not in emp_map or not emp_map[enno]):
            emp_map[enno] = name
        elif enno not in emp_map:
            emp_map[enno] = f"Employee_{enno}"
    return emp_map


def group_by_employee_day(records):
    grouped = defaultdict(list)
    for r in records:
        dt = datetime.strptime(r['datetime'], '%Y-%m-%d %H:%M:%S')
        grouped[(r['enno'], dt.date())].append((dt, r))
    for k in grouped:
        grouped[k].sort(key=lambda x: x[0])
    return grouped


def compute_daily_status(recs):
    if not recs:
        return None, None, "00:00"
    ons  = [dt for dt, r in recs if r['inout'] == 'DutyOn']
    outs = [dt for dt, r in recs if r['inout'] in ('DutyOff', 'GoOut')]
    arrived = min(ons) if ons else None
    dept    = max(outs) if outs else None
    if arrived and dept and dept > arrived:
        m = int((dept - arrived).total_seconds() // 60)
        work = f"{m//60:02d}:{m%60:02d}"
    else:
        work = "00:00"
    return arrived, dept, work


def fmt(dt):
    return "-" if dt is None else dt.strftime('%H:%M')


def get_month_days(year, month):
    if month == 12:
        nxt = datetime(year + 1, 1, 1)
    else:
        nxt = datetime(year, month + 1, 1)
    last = (nxt - timedelta(days=1)).day
    return [datetime(year, month, d) for d in range(1, last + 1)]


def is_weekend(dt):
    return dt.weekday() >= 5


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------
def make_styles():
    s = getSampleStyleSheet()
    s.add(ParagraphStyle('EmpTitle', parent=s['Heading3'],
                         fontSize=9.5, textColor=HEADER_BG,
                         spaceBefore=0, spaceAfter=1,
                         fontName='Helvetica-Bold'))
    s.add(ParagraphStyle('ReportTitle', parent=s['Title'],
                         fontSize=16, textColor=HEADER_BG,
                         spaceAfter=1, alignment=1))
    s.add(ParagraphStyle('ReportSub', parent=s['Normal'],
                         fontSize=8.5, textColor=colors.HexColor("#555555"),
                         spaceAfter=3, alignment=1))
    return s


# ---------------------------------------------------------------------------
# Column measurement
# ---------------------------------------------------------------------------
LABELS = ["Date", "Day", "Arrived", "Dept.", "Work Hrs.", "O.T.", "Status", "Shift"]


def measure_day_col_width(days, daily_data):
    samples = ["00:00", "OFF", "001", "10:27", "18:55", "Date", "Work"]
    for d in days:
        info = daily_data[d.date()]
        if info['arrived']:
            samples.append(info['arrived'].strftime('%H:%M'))
        if info['dept']:
            samples.append(info['dept'].strftime('%H:%M'))
        samples.append(info['working'])
    widest = max(stringWidth(s, FONT_NAME, FONT_SIZE) for s in samples)
    return max(widest + PADDING, MIN_DAY_W)


def measure_label_col_width():
    return max(stringWidth(l, 'Helvetica-Bold', FONT_SIZE) for l in LABELS) + 6


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------
SUMMARY_ROW_H = 4 * mm


def summary_table(present, absent, work_total):
    data = [
        ["Present", "Absent", "Paid Days", "Work Hrs.", "OvTim"],
        [str(present), str(absent), str(present), work_total, "0:00"],
    ]
    t = Table(data, colWidths=[20 * mm] * 5,
              rowHeights=[SUMMARY_ROW_H, SUMMARY_ROW_H])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), HEADER_BG),
        ('TEXTCOLOR',  (0, 0), (-1, 0), HEADER_FG),
        ('FONTNAME',   (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTNAME',   (0, 1), (-1, 1), 'Helvetica'),
        ('FONTSIZE',   (0, 0), (-1, -1), 7),
        ('ALIGN',      (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN',     (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID',       (0, 0), (-1, -1), 0.4, BORDER_CLR),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    return t


DAILY_ROW_H   = FONT_SIZE + 3.5
DAILY_TABLE_H = 8 * DAILY_ROW_H


def daily_table(days, daily_data, usable_width):
    n = len(days)

    header_date = ["Date"] + [f"{d.day:02d}" for d in days]
    header_day  = ["Day"]  + [d.strftime('%a')[:2] for d in days]
    row_arr     = ["Arrived"] + [fmt(daily_data[d.date()]['arrived']) for d in days]
    row_dept    = ["Dept."]   + [fmt(daily_data[d.date()]['dept'])    for d in days]
    row_work    = ["Work Hrs."] + [daily_data[d.date()]['working']    for d in days]
    row_ot      = ["O.T."]    + ["00:00"] * n
    row_status  = ["Status"]  + [
        "A" if daily_data[d.date()]['is_weekend']
        else ("P" if daily_data[d.date()]['arrived'] else "A")
        for d in days
    ]
    row_shift   = ["Shift"]   + [
        "OFF" if daily_data[d.date()]['is_weekend'] else "001"
        for d in days
    ]

    data = [header_date, header_day, row_arr, row_dept,
            row_work, row_ot, row_status, row_shift]

    label_w = measure_label_col_width()
    day_w   = measure_day_col_width(days, daily_data)

    natural = label_w + day_w * n
    if natural > usable_width:
        day_w = max((usable_width - label_w) / n, 6 * mm)

    col_widths = [label_w] + [day_w] * n
    t = Table(data, colWidths=col_widths,
              rowHeights=[DAILY_ROW_H] * 8)

    cmds = [
        ('FONTNAME',      (0, 0), (-1, -1), FONT_NAME),
        ('FONTSIZE',      (0, 0), (-1, -1), FONT_SIZE),
        ('ALIGN',         (1, 0), (-1, -1), 'CENTER'),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID',          (0, 0), (-1, -1), 0.3, BORDER_CLR),
        ('BACKGROUND',    (0, 0), (0, -1), ROW_LABEL_BG),
        ('FONTNAME',      (0, 0), (0, -1), 'Helvetica-Bold'),
        ('TOPPADDING',    (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ('LEFTPADDING',   (0, 0), (-1, -1), 1),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 1),
    ]

    for idx, d in enumerate(days, start=1):
        if is_weekend(d):
            cmds.append(('BACKGROUND', (idx, 0), (idx, -1), WEEKEND_BG))

    status_row = 6
    for idx, d in enumerate(days, start=1):
        info = daily_data[d.date()]
        if info['arrived'] and not info['is_weekend']:
            cmds.append(('BACKGROUND', (idx, status_row),
                         (idx, status_row), PRESENT_BG))
        elif not info['is_weekend']:
            cmds.append(('BACKGROUND', (idx, status_row),
                         (idx, status_row), ABSENT_BG))

    t.setStyle(TableStyle(cmds))
    return t


# ---------------------------------------------------------------------------
# PDF builder
# ---------------------------------------------------------------------------
def build_pdf(records, year, month, output_file, employees_per_page=2):
    emp_map = build_employee_map(records)
    grouped = group_by_employee_day(records)
    days    = get_month_days(year, month)
    styles  = make_styles()

    valid_dates = {d.date() for d in days}

    # --- Employee list ---
    all_ennos = set(r['enno'] for r in records)
    employees = []
    for enno in sorted(all_ennos, key=lambda x: int(x)):
        if enno in EXCLUDED_ENNOS:
            print(f"  Excluding EnNo {enno} (blocklist)")
            continue
        has_duty_on = any(
            r['inout'] == 'DutyOn' and
            datetime.strptime(r['datetime'], '%Y-%m-%d %H:%M:%S').date() in valid_dates
            for r in records if r['enno'] == enno
        )
        if not has_duty_on:
            print(f"  Skipping EnNo {enno}: no DutyOn punches")
            continue
        employees.append(enno)

    print(f"Employees in report: {len(employees)} "
          f"({', '.join(employees)})")

    # --- Layout sizing ---
    dummy = {d.date(): {'arrived': None, 'dept': None,
                        'working': "00:00", 'is_weekend': is_weekend(d)}
             for d in days}
    label_w = measure_label_col_width()
    day_w   = measure_day_col_width(days, dummy)
    table_w = label_w + day_w * len(days)

    margin_x = 10 * mm
    margin_y = 8 * mm

    # Layout budgets
    TITLE_BLOCK_H = 16 * mm
    EMP_TITLE_H   = 4.5 * mm
    SUMMARY_H     = 2 * SUMMARY_ROW_H
    GAP_H         = 3 * mm

    # Height of one employee block (title line + summary + daily grid + gap)
    BLOCK_H = EMP_TITLE_H + SUMMARY_H + 0.8*mm + DAILY_TABLE_H + GAP_H

    # Add generous slack so the title + first block always fit page 1
    SLACK = 8 * mm

    page_w = max(table_w + margin_x * 2, 297 * mm)
    page_h = (TITLE_BLOCK_H + SLACK
              + BLOCK_H * employees_per_page
              + margin_y * 2)

    print(f"Block height: {BLOCK_H / mm:.1f} mm")
    print(f"Page size: {page_w / mm:.1f} x {page_h / mm:.1f} mm")
    print(f"Page fits {employees_per_page} employees")

    doc = SimpleDocTemplate(
        output_file,
        pagesize=(page_w, page_h),
        leftMargin=margin_x, rightMargin=margin_x,
        topMargin=margin_y, bottomMargin=margin_y,
        title=f"Attendance Report {month:02d}-{year}",
        author="Attendance System",
    )

    story = []

    # Title block — appears only on the first page
    story.append(Paragraph(
        f"Attendance Report &mdash; {month:02d}/{year}", styles['ReportTitle']))
    story.append(Paragraph(
        f"Report Date From: 01-{month:02d}-{year} "
        f"&nbsp; To: {days[-1].day:02d}-{month:02d}-{year}",
        styles['ReportSub']))

    def make_employee_block(enno):
        emp_name = emp_map.get(enno, f"Employee_{enno}")

        daily_data = {}
        present = 0
        total_min = 0
        for d in days:
            recs = grouped.get((enno, d.date()), [])
            arr, dep, work = compute_daily_status(recs)
            if arr:
                present += 1
                if work != "00:00":
                    h, m = work.split(':')
                    total_min += int(h) * 60 + int(m)
            daily_data[d.date()] = {
                'arrived': arr,
                'dept': dep,
                'working': work,
                'is_weekend': is_weekend(d),
            }

        absent     = len(days) - present
        work_total = f"{total_min // 60}:{total_min % 60:02d}"

        block = []
        block.append(Paragraph(
            f"Employee: <b>{emp_name}</b> &nbsp; (EmpCode: {enno})",
            styles['EmpTitle']))
        block.append(summary_table(present, absent, work_total))
        block.append(Spacer(1, 0.8 * mm))
        block.append(daily_table(days, daily_data, usable_width=table_w))
        block.append(Spacer(1, GAP_H))
        return block

    # Explicit page grouping: N employees per page
    for page_idx in range(0, len(employees), employees_per_page):
        chunk = employees[page_idx: page_idx + employees_per_page]

        for i, enno in enumerate(chunk):
            flowables = make_employee_block(enno)
            # Wrap in KeepTogether EXCEPT the very first block on page 1,
            # so it can share the page with the title.
            if page_idx == 0 and i == 0:
                for f in flowables:
                    story.append(f)
            else:
                story.append(KeepTogether(flowables))

        if page_idx + employees_per_page < len(employees):
            story.append(PageBreak())

    doc.build(story)
    print(f"PDF generated: {output_file}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    alog = sys.argv[1] if len(sys.argv) > 1 else "ALOG_001.txt"
    out  = sys.argv[2] if len(sys.argv) > 2 else "Attendance_Report_September_2026.pdf"

    year  = 2026
    month = 9
    employees_per_page = 2

    print(f"Reading: {alog}")
    records = parse_alog(alog)
    print(f"Parsed {len(records)} records")

    build_pdf(records, year, month, out,
              employees_per_page=employees_per_page)


if __name__ == "__main__":
    main()
