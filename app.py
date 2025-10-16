# app.py — BizPlan Deep Report (50–100 pages) — One-Prompt Streamlit Generator
# - Deterministic financials (IRR, DSCR, Payback, EBITDA) + debt schedule
# - Narrative expander with multi-subsection content per section (1–12)
# - Big annexures: equipment, SOPs, risk register, compliance & incentives
# - DOCX with tables + charts; Markdown export
# - Verification: numeric consistency + glossary terms

from dataclasses import dataclass
from typing import Dict, Any, List, Tuple
from io import BytesIO
from datetime import datetime
import math
import random

import streamlit as st
from jinja2 import Template
import pandas as pd
from docx import Document
from docx.shared import Pt, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH

# -----------------------------
# Glossary + helpers (formatting)
# -----------------------------
GLOSSARY = {
    "EBITDA": "Earnings before interest, taxes, depreciation, and amortization.",
    "DSCR": "Debt Service Coverage Ratio = Cash Available for Debt Service / (Interest + Principal).",
    "IRR": "Internal Rate of Return on project cashflows."
}

def fmt_currency(n) -> str:
    try:
        return f"{int(round(float(n))):,}"
    except Exception:
        return "0"

def fmt_pct(x, digits=2):
    try:
        return f"{float(x)*100:.{digits}f}%"
    except Exception:
        return "0.00%"

# -----------------------------
# IRR (bisection) + NPV helpers
# -----------------------------
def npv(rate: float, cashflows: List[float]) -> float:
    total = 0.0
    for i, cf in enumerate(cashflows):
        total += cf / ((1.0 + rate) ** i)
    return total

def irr(cashflows: List[float], low=-0.9999, high=1.0, tol=1e-7, max_iter=200) -> float:
    f_low = npv(low, cashflows)
    f_high = npv(high, cashflows)
    if f_low * f_high > 0:
        for _ in range(10):
            high *= 2.0
            f_high = npv(high, cashflows)
            if f_low * f_high <= 0:
                break
        else:
            return 0.0
    lo, hi = low, high
    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        f_mid = npv(mid, cashflows)
        if abs(f_mid) < tol:
            return mid
        if f_low * f_mid < 0:
            hi = mid
            f_high = f_mid
        else:
            lo = mid
            f_low = f_mid
    return (lo + hi) / 2.0

# -----------------------------
# Deterministic Financial Engine
# -----------------------------
@dataclass
class Snapshot:
    id: str
    metrics: Dict[str, Any]
    assumptions_used: Dict[str, Any]
    schedules: Dict[str, pd.DataFrame]

def run_calculations(ass: Dict[str, Any]) -> Snapshot:
    capex_total     = float(ass.get("capex_total",     1_200_000_000))  # ₹
    years           = int(  ass.get("years",           10))
    year1_revenue   = float(ass.get("year1_revenue",   450_000_000))
    revenue_growth  = float(ass.get("revenue_growth",  0.08))
    ebitda_margin   = float(ass.get("ebitda_margin",   0.22))
    debt_amount     = float(ass.get("debt_amount",     capex_total * 0.6))
    interest_rate   = float(ass.get("interest_rate",   0.11))
    tenor_years     = int(  ass.get("tenor_years",     7))

    # Projections
    revenues = [year1_revenue * ((1.0 + revenue_growth) ** i) for i in range(years)]
    ebitdas  = [r * ebitda_margin for r in revenues]

    # Debt schedule (flat principal)
    principal_annual = debt_amount / tenor_years if tenor_years else 0.0
    outstanding = debt_amount
    dscr_list, interest_list, principal_list, debt_service_list, closing_list = [], [], [], [], []
    for y in range(years):
        interest_y   = outstanding * interest_rate if outstanding > 0 else 0.0
        principal_y  = principal_annual if y < tenor_years else 0.0
        debt_service = interest_y + principal_y
        cash_avail   = ebitdas[y]
        dscr_list.append((cash_avail / debt_service) if debt_service > 0 else float("inf"))
        interest_list.append(interest_y)
        principal_list.append(principal_y)
        debt_service_list.append(debt_service)
        if y < tenor_years:
            outstanding -= principal_annual
        closing_list.append(max(outstanding, 0.0))

    # Cashflows for IRR (pre-financing Project IRR)
    cashflows = [-capex_total] + ebitdas
    project_irr = irr(cashflows)

    # Payback index
    cum, payback_year = 0.0, None
    for i, cf in enumerate(cashflows):
        cum += cf
        if cum >= 0 and payback_year is None:
            payback_year = i

    # Schedules as DataFrames (for tables/annex)
    years_idx = list(range(1, years + 1))
    df_proj = pd.DataFrame({
        "Year": years_idx,
        "Revenue (₹)": revenues,
        "EBITDA (₹)": ebitdas
    })
    df_debt = pd.DataFrame({
        "Year": years_idx,
        "Interest (₹)": interest_list,
        "Principal (₹)": principal_list,
        "Debt Service (₹)": debt_service_list,
        "Closing Debt (₹)": closing_list,
        "DSCR": dscr_list,
    })

    return Snapshot(
        id=datetime.now().strftime("%Y%m%d%H%M%S"),
        metrics={
            "revenue_yearly": revenues,
            "ebitda_yearly": ebitdas,
            "ebitda_margin": ebitda_margin,
            "irr_project": project_irr,
            "dscr_min": min(dscr_list),
            "dscr_avg": sum(dscr_list)/len(dscr_list),
            "payback_year": payback_year
        },
        assumptions_used=ass,
        schedules={"projections": df_proj, "debt": df_debt}
    )

# -----------------------------
# Verification Gates
# -----------------------------
def verify_snapshot(snapshot: Snapshot, combined_text: str) -> List[str]:
    issues = []
    m = snapshot.metrics
    rev = m.get("revenue_yearly", [])
    ebt = m.get("ebitda_yearly", [])
    if len(rev) != len(ebt):
        issues.append("EBITDA and Revenue years mismatch.")
    if any(x < 0 for x in rev):
        issues.append("Negative revenue detected.")
    dscr_min = m.get("dscr_min", 0)
    if dscr_min != float('inf') and dscr_min < 1.0:  # tighten covenant to 1.0 for banker-readiness
        issues.append(f"DSCR minimum below 1.00: {dscr_min:.2f}")
    for k in ["EBITDA", "DSCR", "IRR"]:
        if k not in combined_text:
            issues.append(f"Term '{k}' not present where expected in narrative.")
    return issues

# -----------------------------
# Narrative (multi-subsection generator)
# -----------------------------
def render(tpl: str, ctx: Dict[str, Any]) -> str:
    return Template(tpl).render(**ctx)

def paragraphs_for(topic: str, ctx: Dict[str, Any], n: int) -> str:
    """Generate n context-grounded paragraphs for a topic."""
    base = (
        f"{topic}: The project targets robust unit economics anchored by EBITDA margins near "
        f"{fmt_pct(ctx['m'].get('ebitda_margin', 0.0),1)} with staged ramp-up. "
        "Key assumptions are validated against vendor quotes, utility tariffs, and logistics benchmarks. "
        "Where uncertainty exists, sensitivity bands are applied and monitored during execution."
    )
    extras = [
        "Procurement contracts will incorporate quality bands, delivery KPIs, and dispute mechanisms to reduce volatility.",
        "Operational excellence will be driven by SOPs, SPC charts, and OEE monitoring at critical machines.",
        "Digital systems (PLC/SCADA + MES-lite) enable traceability and exception alerts for deviations.",
        "Foreign exchange exposure (for export sales or imported machinery) is hedged per treasury policy.",
        "Sustainability initiatives cover heat recovery, water recycling, and waste valorization."
    ]
    out = [base]
    for i in range(n-1):
        out.append(extras[i % len(extras)])
    return "\n\n".join(out)

def long_section(title: str, subtopics: List[str], ctx: Dict[str, Any], depth: int) -> str:
    chunks = [f"**{title}**"]
    for t in subtopics[:depth]:
        chunks.append(f"**{t}**\n\n" + paragraphs_for(t, ctx, 3))
    return "\n\n".join(chunks)

def draft_all_sections(prompt: str, assumptions: Dict[str, Any], metrics: Dict[str, Any],
                       detail_mode: str) -> Dict[str, Dict[str, str]]:
    depth = {"Standard": 4, "Extended": 8, "Investor Deep-Dive (60–100 pages)": 14}[detail_mode]
    ctx = {"prompt": prompt, "ass": assumptions, "m": metrics}
    S: Dict[str, Dict[str, str]] = {}

    # Section blueprints
    def sec(i, title, body):
        S[str(i)] = {"title": title, "markdown": body}

    # 1 Executive Summary (concise but factual)
    s1 = f"""
**Objective:** {prompt}

**Products & Co-products:** Protein concentrate, starch, fiber, animal feed.

**Market Overview:** Expected revenue CAGR ~ {fmt_pct(assumptions.get('revenue_growth',0.08),1)} with export traction in clean-label ingredients.

**Business Case & Incentives:** Central/state schemes (MoFPI/AIF/state capital subsidy) subject to eligibility; ESG-first design.

**Key Vendors & Technology Partners:** Dry & wet extraction OEMs; spray dryer specialists; utilities integrators.

**Financial Highlights:** IRR ≈ {fmt_pct(metrics.get('irr_project',0.0),2)}, Min DSCR {metrics.get('dscr_min',0):.2f}, Payback year index {metrics.get('payback_year',None)}, EBITDA margin ≈ {fmt_pct(metrics.get('ebitda_margin',0.0),1)}.
"""
    sec(1, "Executive Summary", s1)

    # 2 Promoter Profile (expanded)
    sec(2, "Promoter Profile", long_section(
        "Promoter Capability & Governance",
        [
            "Company background & legal structure",
            "Leadership experience & domain expertise",
            "Execution track record & case studies",
            "Allied capabilities (automation, procurement, logistics)",
            "Governance, board oversight & MIS cadence",
            "Advisory ecosystem (process, financial, legal)",
            "ESG policy & safety culture",
            "Banking relationships & credit history",
            "Insurance strategy (asset, business interruption)",
            "Supplier relationships & NDAs"
        ], ctx, depth))

    # 3 Industry & Market Analysis
    sec(3, "Industry & Market Analysis", long_section(
        "Industry Structure & Demand Drivers",
        [
            "Global plant protein trends & benchmarks",
            "Domestic demand drivers & consumer shifts",
            "Export markets & non-tariff barriers",
            "Competitive landscape & pricing corridors",
            "Customer segments (B2B/B2C/Export) & needs",
            "Distribution, logistics & INCOTERMS",
            "Regulatory standards, certifications & labs",
            "Brand positioning & claims validation",
            "Seasonality & inventory strategy",
            "Go-to-market phasing & pipeline build"
        ], ctx, depth))

    # 4 Technical Feasibility
    sec(4, "Technical Feasibility", long_section(
        "Process Design & Product Specs",
        [
            "Process overview (dry + wet route) & bottleneck analysis",
            "Unit operations & mass balance (yields, recoveries)",
            "Process control philosophy (PLC/SCADA, interlocks)",
            "Utility design (steam, power, water, refrigeration)",
            "Quality specs (protein %, micro, moisture, solubility)",
            "Food safety (HACCP, allergen controls, traceability)",
            "Scale-up & commissioning strategy",
            "Maintenance philosophy (PPM, spares, AMC)",
            "Data logging & analytics (SPC, OEE)",
            "Change control & validation"
        ], ctx, depth))

    # 5 Location & Infrastructure
    sec(5, "Location & Infrastructure", long_section(
        "Site Selection & Infra",
        [
            "Agro belt advantages & RM logistics",
            "Land use, zoning & future expansion",
            "Power availability & tariff structure",
            "Boiler/fuel options & cost curves",
            "Water sourcing & recycling",
            "Effluent handling & emissions",
            "Fire, safety & statutory compliance",
            "Warehouse layout & cold chain",
            "Connectivity (road/rail/port/air)",
            "Township & workforce access"
        ], ctx, depth))

    # 6 CAPEX (with total)
    s6 = f"""
**CAPEX Summary**

- Land & site development
- Civil & building works
- Plant & machinery (dry, wet, spray-dryer)
- Utilities & ancillary systems
- Electrical & automation
- Pre-operative & contingencies

**Total CAPEX (input): ₹{fmt_currency(assumptions.get('capex_total',0))}**
"""
    sec(6, "Project Costs (CAPEX)", s6 + "\n\n" + long_section(
        "CAPEX Rationale & Procurement",
        [
            "Technical specs & vendor shortlist",
            "Bid evaluation & total cost of ownership",
            "Delivery timelines & LD mechanisms",
            "Erection & commissioning inclusions",
            "Performance guarantees & acceptance tests",
            "Spares, tools & training scope",
            "Taxes, duties & logistics",
            "Insurance & risk cover during transit",
            "Contingency strategy",
            "Milestone-based payment plan"
        ], ctx, depth))

    # 7 OPEX
    sec(7, "Operating Costs (OPEX)", long_section(
        "Operating Cost Model",
        [
            "Raw material procurement & contracts",
            "Power & energy mapping",
            "Boiler fuel selection & efficiency",
            "Chemicals, consumables & packaging",
            "Manpower plan & skill matrix",
            "Repairs & maintenance (PPM/AMC)",
            "Quality/lab costs & consumables",
            "Logistics & distribution",
            "Insurance, admin & audits",
            "Overheads & cost control levers"
        ], ctx, depth))

    # 8 Revenue & Financial Analysis (with KPI bullets)
    s8 = f"""
**KPI Summary**

- Revenue Year-1: ₹{fmt_currency(metrics.get('revenue_yearly',[0])[0] if metrics.get('revenue_yearly') else 0)}
- EBITDA margin: {fmt_pct(metrics.get('ebitda_margin',0.0),1)}
- Project IRR: {fmt_pct(metrics.get('irr_project',0.0),2)}
- Min DSCR: {metrics.get('dscr_min',0):.2f} | Avg DSCR: {metrics.get('dscr_avg',0):.2f}
- Payback (year index): {metrics.get('payback_year',None)}
"""
    sec(8, "Revenue & Financial Analysis", s8 + "\n\n" + long_section(
        "Revenue Model & Sensitivities",
        [
            "Revenue streams (protein, starch, fiber, feed)",
            "Price corridors & quality differentials",
            "Mix optimization (domestic vs export)",
            "Credit terms & working capital cycle",
            "Sensitivity to ASP, yields, tariffs, load factor",
            "Debt structure, tenor & moratorium",
            "Covenants & DSRA policies",
            "Tax, depreciation & MAT considerations",
            "Dividends policy & reinvestment",
            "Downside protections & hedging"
        ], ctx, depth))

    # 9 Compliance & Incentives
    sec(9, "Supporting Measures & Compliance", long_section(
        "Regulatory & Incentives",
        [
            "Factory license & building approvals",
            "FSSAI & food safety audits",
            "Pollution control consents (CTE/CTO)",
            "Boiler, electrical & fire NOCs",
            "Labor codes & EHS",
            "DGFT & export registrations",
            "MoFPI/AIF/state incentives process",
            "Certification roadmap (ISO, HACCP, BRC)",
            "Insurance compliance",
            "Audit calendar & evidence retention"
        ], ctx, depth))

    # 10 Risk Mitigation
    sec(10, "Risk Mitigation & Strategy", long_section(
        "Risk Register & Controls",
        [
            "Raw material volatility & contracts",
            "Energy price risk & heat recovery",
            "Quality deviation & recall drills",
            "Market concentration & diversification",
            "FX risk & treasury policy",
            "Regulatory shifts & contingency",
            "Project delays & LDs",
            "Vendor performance & SLAs",
            "Cyber & data integrity",
            "Force majeure & business continuity"
        ], ctx, depth))

    # 11 Execution Plan
    sec(11, "Execution Plan", long_section(
        "Implementation Roadmap",
        [
            "Engineering & design freeze",
            "Procurement & logistics",
            "Civil & utilities sequencing",
            "Erection & commissioning",
            "Performance qualification & ramp-up",
            "SOP development & training",
            "Go-live gate & stabilization",
            "Weekly WAR rooms & KPIs",
            "Change management",
            "Project close-out & handover"
        ], ctx, depth))

    # 12 Annexures header; actual annex content added later
    sec(12, "Annexures", "**Annexures & Schedules** — See detailed tables, charts, registers, and SOPs below.")
    return S

# -----------------------------
# Annexure builders (tables etc.)
# -----------------------------
def build_equipment_list(n=120) -> pd.DataFrame:
    rows = []
    blocks = ["Dry Mill", "Wet Extraction", "Filtration", "Evaporation", "Spray Dryer", "Utilities", "Packaging", "Labs"]
    for i in range(1, n+1):
        block = random.choice(blocks)
        rows.append({
            "No": i, "Area": block, "Equipment": f"{block} Unit {i}",
            "Spec": "As per datasheet", "Qty": 1, "Vendor": "TBD"
        })
    return pd.DataFrame(rows)

def build_sop_steps(n=150) -> pd.DataFrame:
    rows = []
    phases = ["Intake", "Cleaning", "Milling", "Extraction", "Filtration", "Concentration", "Drying", "Packaging", "QC"]
    for i in range(1, n+1):
        ph = random.choice(phases)
        rows.append({"Step": i, "Process Area": ph, "Instruction": f"Standard operating step {i} for {ph}."})
    return pd.DataFrame(rows)

def build_risk_register(n=80) -> pd.DataFrame:
    risks = []
    cats = ["Supply", "Quality", "Energy", "Market", "Regulatory", "Project", "Finance", "Cyber", "EHS"]
    for i in range(1, n+1):
        cat = random.choice(cats)
        risks.append({
            "ID": f"R{i:03d}", "Category": cat, "Risk": f"{cat} risk item {i}",
            "Impact": random.choice(["Low","Medium","High"]),
            "Likelihood": random.choice(["Rare","Possible","Likely"]),
            "Mitigation": "Contracting / SOP / Monitoring", "Owner": "Ops/QA/CFO"
        })
    return pd.DataFrame(risks)

def build_compliance_matrix() -> pd.DataFrame:
    items = [
        ("Factory License", "State Inspectorate", "Before commissioning"),
        ("FSSAI", "FSSAI Authority", "Before commercial production"),
        ("CTE/CTO", "State Pollution Control Board", "CTE pre-civil, CTO pre-production"),
        ("Boiler", "Boiler Inspectorate", "Pre-steam up"),
        ("Electrical", "Electrical Inspector", "Pre-energization"),
        ("Fire & Safety", "Local Authority", "Pre-occupation"),
        ("Export Reg.", "DGFT/Plant Quarantine", "Before first export")
    ]
    return pd.DataFrame([{"Requirement": a, "Authority": b, "Timeline": c, "Status": "Planned"} for a,b,c in items])

def build_incentives_matrix() -> pd.DataFrame:
    items = [
        ("MoFPI", "Credit-linked capex subsidy (eligibility based)", "Application with DPR & approvals"),
        ("AIF", "Interest subvention", "Through eligible banks/NBFCs"),
        ("State Capital Subsidy", "Varies by state", "Post-investment claim"),
        ("Export Benefits", "RoDTEP/Other", "Subject to HS code & policy")
    ]
    return pd.DataFrame([{"Scheme": a, "Benefit": b, "Process": c, "Status": "To be evaluated"} for a,b,c in items])

# -----------------------------
# Markdown & DOCX builders
# -----------------------------
def build_markdown(sections: Dict[str, Dict[str, str]],
                   annex: Dict[str, pd.DataFrame]) -> str:
    lines = ["# Business Plan Report"]
    for i in range(1, 13):
        sid = str(i)
        sec = sections.get(sid, {})
        title = sec.get("title", f"Section {sid}")
        md = sec.get("markdown", "(pending)")
        lines.append(f"\n## {sid}. {title}\n{md}\n")
    # Annex summary
    for k, df in annex.items():
        lines.append(f"\n### Annex — {k}\n")
        lines.append(df.head(10).to_markdown(index=False))  # preview ten rows in MD
    return "\n".join(lines)

def add_chart(doc: Document, df: pd.DataFrame, xcol: str, ycols: List[str], title: str):
    fig, ax = plt.subplots()
    for col in ycols:
        ax.plot(df[xcol], df[col], label=col)
    ax.set_title(title)
    ax.set_xlabel(xcol)
    ax.legend()
    buf = BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    doc.add_picture(buf, width=Inches(6.5))

def df_to_docx_table(doc: Document, df: pd.DataFrame, title: str):
    doc.add_heading(title, level=2)
    table = doc.add_table(rows=1, cols=len(df.columns))
    hdr_cells = table.rows[0].cells
    for j, col in enumerate(df.columns):
        hdr_cells[j].text = str(col)
    for _, row in df.iterrows():
        cells = table.add_row().cells
        for j, col in enumerate(df.columns):
            val = row[col]
            if isinstance(val, (int, float)):
                cells[j].text = fmt_currency(val) if "₹" in title or "Debt" in title or "EBITDA" in title or "Revenue" in title else str(val)
            else:
                cells[j].text = str(val)

def build_docx(sections: Dict[str, Dict[str, str]],
               snap: Snapshot,
               annex: Dict[str, pd.DataFrame]) -> bytes:
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    # Title page
    doc.add_heading("Business Plan Report", level=0)
    p = doc.add_paragraph(f"Generated on {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT

    # Sections
    for i in range(1, 13):
        sid = str(i)
        sec = sections.get(sid, {})
        title = sec.get("title", f"Section {sid}")
        doc.add_heading(f"{sid}. {title}", level=1)
        content = sec.get("markdown", "")
        for para in content.split("\n\n"):
            doc.add_paragraph(para.replace("\n", " "))

        # Insert charts at key sections
        if sid == "8":
            dfp = snap.schedules["projections"].copy()
            dfp["Revenue (₹)"] = dfp["Revenue (₹)"].astype(float)
            dfp["EBITDA (₹)"] = dfp["EBITDA (₹)"].astype(float)
            add_chart(doc, dfp.rename(columns={"Year":"Year #"}),
                      "Year #", ["Revenue (₹)", "EBITDA (₹)"], "Revenue & EBITDA (Projected)")
            dfd = snap.schedules["debt"].copy()
            add_chart(doc, dfd.rename(columns={"Year":"Year #"}),
                      "Year #", ["Debt Service (₹)"], "Debt Service Profile")

    # Annexures: big tables (these add many pages)
    doc.add_heading("Annexures", level=1)
    for name, df in annex.items():
        df_to_docx_table(doc, df, f"{name}")

    bio = BytesIO()
    doc.save(bio); bio.seek(0)
    return bio.read()

# -----------------------------
# Streamlit UI
# -----------------------------
st.set_page_config(page_title="BizPlan Deep Report", layout="wide")
st.title("Business & Financial Planning — One-Prompt Deep Report (Streamlit)")

with st.expander("How it works", expanded=False):
    st.markdown("""
- Enter a **Prompt** describing the project.
- Pick a **Detail level** (Investor Deep-Dive can produce ~60–100 pages).
- The app computes **deterministic financials** and generates **rich narratives** across all 12 sections.
- It assembles **annexures** (large tables, SOPs, risk register, compliance & incentives) and embeds **charts**.
- Download **DOCX** (with tables + charts) and **Markdown**.
    """)

# Sidebar inputs
st.sidebar.header("Assumptions")
capex_total    = st.sidebar.number_input("Total CAPEX (₹)", value=1_200_000_000, min_value=0, step=10_000_000)
years          = st.sidebar.number_input("Projection Years", value=10, min_value=5, max_value=20, step=1)
year1_revenue  = st.sidebar.number_input("Year-1 Revenue (₹)", value=450_000_000, min_value=0, step=100_000)
revenue_growth = st.sidebar.number_input("Revenue Growth (decimal)", value=0.08, min_value=-0.2, max_value=1.0, step=0.01, format="%.2f")
ebitda_margin  = st.sidebar.number_input("EBITDA Margin (decimal)", value=0.22, min_value=0.05, max_value=0.6, step=0.01, format="%.2f")
debt_amount    = st.sidebar.number_input("Debt Amount (₹)", value=int(1_200_000_000*0.6), min_value=0, step=100_000)
interest_rate  = st.sidebar.number_input("Interest Rate (decimal)", value=0.11, min_value=0.03, max_value=0.25, step=0.01, format="%.2f")
tenor_years    = st.sidebar.number_input("Debt Tenor (years)", value=7, min_value=1, max_value=15, step=1)

detail_mode = st.sidebar.selectbox("Detail level", ["Standard", "Extended", "Investor Deep-Dive (60–100 pages)"])

prompt = st.text_area(
    "Project Prompt",
    value="Build a 200 TPD plant protein facility with dry+wet routes in a central agro belt. Target B2B exports for protein and domestic starch. Include MoFPI and AIF schemes. Focus on energy efficiency and ESG.",
    height=140
)

if st.button("Generate Deep Report", type="primary"):
    assumptions = {
        "capex_total": capex_total,
        "years": years,
        "year1_revenue": year1_revenue,
        "revenue_growth": revenue_growth,
        "ebitda_margin": ebitda_margin,
        "debt_amount": debt_amount,
        "interest_rate": interest_rate,
        "tenor_years": tenor_years
    }
    snap = run_calculations(assumptions)

    sections = draft_all_sections(prompt, assumptions, snap.metrics, detail_mode)

    combined_text = "\n".join([s["markdown"] for s in sections.values()])
    issues = verify_snapshot(snap, combined_text)

    # KPIs
    st.subheader("Financial KPIs")
    c1,c2,c3,c4 = st.columns(4)
    c1.metric("IRR (Project)", f"{snap.metrics['irr_project']*100:.2f}%")
    c2.metric("DSCR (Min)", f"{snap.metrics['dscr_min']:.2f}")
    c3.metric("DSCR (Avg)", f"{snap.metrics['dscr_avg']:.2f}")
    c4.metric("Payback (year index)", str(snap.metrics['payback_year']))

    if issues:
        st.warning("Verification Issues:")
        for i in issues: st.write(f"• {i}")
    else:
        st.success("Verification passed.")

    # Show key schedules
    st.subheader("Projection & Debt Schedules (preview)")
    st.dataframe(snap.schedules["projections"])
    st.dataframe(snap.schedules["debt"])

    # Annexures (large tables to drive page count)
    annex = {
        "Equipment List — Indicative (long)": build_equipment_list(150 if "Investor" in detail_mode else 80),
        "Standard Operating Procedures — Steps": build_sop_steps(200 if "Investor" in detail_mode else 120),
        "Risk Register": build_risk_register(120 if "Investor" in detail_mode else 60),
        "Compliance Matrix": build_compliance_matrix(),
        "Incentives & Subsidies Matrix": build_incentives_matrix(),
    }

    st.subheader("Generated Report (Sections 1–12)")
    for i in range(1, 13):
        sid = str(i)
        with st.expander(f"{i}. {sections[sid]['title']}", expanded=(i<=2)):
            st.markdown(sections[sid]["markdown"])

    # Exports
    st.subheader("Download")
    md_text = build_markdown(sections, annex)
    docx_bytes = build_docx(sections, snap, annex)

    st.download_button(
        "⬇️ Download Markdown",
        data=md_text.encode("utf-8"),
        file_name=f"business_plan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md",
        mime="text/markdown"
    )
    st.download_button(
        "⬇️ Download DOCX (tables + charts)",
        data=docx_bytes,
        file_name=f"business_plan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx",
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )

st.caption("Deterministic financials; narratives are template-generated and long-form. Swap in your LLM later for bespoke prose, grounded on the computed snapshot.")
