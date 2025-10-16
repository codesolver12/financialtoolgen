# app.py
# Business & Financial Planning — One-Prompt Streamlit Generator
# - Deterministic calculations (IRR, DSCR, Payback, EBITDA)
# - Templated drafting for Sections 1–12 (swap with real LLM later if desired)
# - Verification gates (consistency + glossary)
# - Exports: Markdown + DOCX (download buttons)

import math
from dataclasses import dataclass
from typing import Dict, Any, List
from jinja2 import Template
from io import BytesIO
from datetime import datetime

import streamlit as st
from docx import Document
from docx.shared import Pt

# ==============
# Utils & Models
# ==============
GLOSSARY = {
    "EBITDA": "Earnings before interest, taxes, depreciation, and amortization.",
    "DSCR": "Debt Service Coverage Ratio = Cash Available for Debt Service / (Interest + Principal).",
    "IRR": "Internal Rate of Return on project cashflows."
}

@dataclass
class Snapshot:
    id: str
    metrics: Dict[str, Any]
    assumptions_used: Dict[str, Any]

# ------------- helpers -------------
def fmt_currency(n) -> str:
    """Format a number as integer with thousand separators."""
    try:
        return f"{int(round(float(n))):,}"
    except Exception:
        return "0"

# ==============
# IRR Calculation
# ==============
def npv(rate: float, cashflows: List[float]) -> float:
    total = 0.0
    for i, cf in enumerate(cashflows):
        total += cf / ((1.0 + rate) ** i)
    return total

def irr(cashflows: List[float], low=-0.9999, high=1.0, tol=1e-7, max_iter=200) -> float:
    """Bisection IRR. Returns decimal rate (0.18 ⇒ 18%)."""
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

# ======================
# Deterministic Financials
# ======================
def run_calculations(ass: Dict[str, Any]) -> Snapshot:
    capex_total     = float(ass.get("capex_total",     1_200_000_000))  # ₹
    years           = int(  ass.get("years",           10))
    year1_revenue   = float(ass.get("year1_revenue",   450_000_000))
    revenue_growth  = float(ass.get("revenue_growth",  0.08))
    ebitda_margin   = float(ass.get("ebitda_margin",   0.22))
    debt_amount     = float(ass.get("debt_amount",     capex_total*0.6))
    interest_rate   = float(ass.get("interest_rate",   0.11))
    tenor_years     = int(  ass.get("tenor_years",     7))

    revenues = [year1_revenue * ((1.0 + revenue_growth) ** i) for i in range(years)]
    ebitdas  = [r * ebitda_margin for r in revenues]

    # simple flat principal loan
    principal_annual = debt_amount / tenor_years if tenor_years else 0.0
    outstanding = debt_amount
    dscr_list: List[float] = []

    for y in range(years):
        interest_y   = outstanding * interest_rate if outstanding > 0 else 0.0
        principal_y  = principal_annual if y < tenor_years else 0.0
        debt_service = interest_y + principal_y
        cash_avail   = ebitdas[y]
        dscr_list.append((cash_avail / debt_service) if debt_service > 0 else float("inf"))
        if y < tenor_years:
            outstanding -= principal_annual

    # Project cashflows: -CAPEX at t0, then EBITDA each year
    cashflows   = [-capex_total] + ebitdas
    project_irr = irr(cashflows)

    # Payback year index
    cum, payback_year = 0.0, None
    for i, cf in enumerate(cashflows):
        cum += cf
        if cum >= 0 and payback_year is None:
            payback_year = i

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
        assumptions_used=ass
    )

# =====================
# Verification Gateways
# =====================
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
    if dscr_min != float('inf') and dscr_min < 0.8:
        issues.append(f"DSCR minimum too low: {dscr_min:.2f}")

    for k in ["EBITDA", "DSCR", "IRR"]:
        if k not in combined_text:
            issues.append(f"Term '{k}' not present where expected in narrative.")

    return issues

# =================
# Section Drafting
# =================
def render(tpl: str, ctx: Dict[str, Any]) -> str:
    return Template(tpl).render(**ctx)

def draft_all_sections(prompt: str, assumptions: Dict[str, Any], metrics: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    # pass a safe formatter into Jinja context
    ctx = {"prompt": prompt, "ass": assumptions, "m": metrics, "fmt": fmt_currency}
    S: Dict[str, Dict[str, str]] = {}

    def sec(i: int, title: str, body_tpl: str):
        S[str(i)] = {"title": title, "markdown": render(body_tpl, ctx)}

    sec(1, "Executive Summary", """
**Objective:** {{ prompt or 'N/A' }}

**Products & Co-products:** Protein concentrate, starch, fiber, animal feed.

**Market Overview (Domestic & Export):** Demand growth ~ {{ (ass.get('revenue_growth',0.08)*100)|round(1) }}% CAGR; export opportunities strengthening with clean-label trends.

**Business Case & Incentives:** Eligible for relevant central/state schemes subject to criteria; ESG and energy-efficiency reinforced.

**Key Vendors & Technology Partners:** Shortlisted for dry + wet process lines; spray dryer OEMs under evaluation.

**Financial Highlights (IRR, Payback, DSCR, EBITDA):**
- IRR (Project): ~ **{{ (m.get('irr_project',0.0)*100)|round(2) }}%**
- Min DSCR: **{{ m.get('dscr_min')|round(2) }}**, Avg DSCR: **{{ m.get('dscr_avg')|round(2) }}**
- Payback (year index): **{{ m.get('payback_year') }}**
- EBITDA margin: **{{ (m.get('ebitda_margin',0.0)*100)|round(1) }}%**
""")

    sec(2, "Promoter Profile", """
**2.1 Company Background:** Experienced operator in agro-processing and ingredients.

**2.2 Leadership & Team Expertise:** Cross-functional leadership (engineering, ops, finance, exports).

**2.3 Track Record & Allied Capabilities:** Prior execution in processing lines and supply chain integration.
""")

    sec(3, "Industry & Market Analysis", """
**3.1 Global Trends in Plant Proteins:** Rising plant-based adoption; functional proteins across F&B.

**3.2 Indian Demand Drivers:** Health & nutrition, convenience foods, institutional B2B buyers.

**3.3 Export Drivers:** Competitive sourcing/processing, tariff access (jurisdiction-dependent).

**3.4 Customer Segments (B2B, B2C, Export):** Ingredients (B2B), health foods (B2C), export-grade concentrates.
""")

    sec(4, "Technical Feasibility", """
**4.1 Process Overview (Dry + Wet Route):** Integrated dry milling + wet extraction; spray drying for final product.

**4.2 Process Flow Steps:** Intake → Cleaning → Dry milling → Separation/Extraction → Filtration → Concentration → Spray drying → Packaging.

**4.3 Product Attributes & Specifications:** Protein %, moisture, microbiology, solubility, color; batchwise QC/COAs.
""")

    sec(5, "Location & Infrastructure", """
**5.1 Preferred Locations & Agro Belt Advantages:** Proximity to raw material belts; multimodal logistics.

**5.2 Land & Area Requirements:** Sized for current capacity and expansion.

**5.3 Utility Requirements (Power, Steam, Water, Refrigeration):** Rightsized for throughputs; energy recovery where feasible.

**5.4 Compliance & Approvals:** Factory license, FSSAI, PCB consents, boiler, electrical, fire & safety.
""")

    sec(6, "Project Costs (CAPEX)", """
**6.1 Land & Site Development:** (TBD)

**6.2 Civil & Building Works:** (TBD)

**6.3 Plant & Machinery (Dry End, Wet End, Spray Dryer):** Major share of CAPEX.

**6.4 Utilities & Ancillary Systems:** Boiler/steam, refrigeration, water systems.

**6.5 Electrical & Automation:** MCCs, drives, PLC/SCADA.

**6.6 Pre-operative Expenses & Contingencies:** Engineering, commissioning, contingency.

**6.7 Total CAPEX:** **₹{{ fmt(ass.get('capex_total',0)) }}**
""")

    sec(7, "Operating Costs (OPEX)", """
**7.1 Raw Material Procurement:** Multi-sourcing, forward contracts.

**7.2 Power & Energy Costs:** High-efficiency motors; heat recovery.

**7.3 Steam & Boiler Fuels:** Optimize boiler efficiency, fuel mix.

**7.4 Chemicals, Consumables & Packaging:** Sourced per batch specs.

**7.5 Labor & Wages:** Skilled operators, QC, maintenance.

**7.6 Repairs & Maintenance:** Planned preventive maintenance.

**7.7 Overheads & Total OPEX:** Admin, insurance, audits. (Detailed split from quotes/tariffs.)
""")

    sec(8, "Revenue & Financial Analysis", """
**8.1 Revenue Streams (Protein, Starch, Fiber, Feed):** Mix balanced for domestic + export.

**8.2 Annual Projections & EBITDA:** EBITDA margin ~ **{{ (m.get('ebitda_margin',0.0)*100)|round(1) }}%**; Yr-1 EBITDA ≈ **₹{{ fmt( (m.get('ebitda_yearly',[0])[0] or 0) ) }}**.

**8.3 IRR, DSCR, Payback Analysis:** IRR ~ **{{ (m.get('irr_project',0.0)*100)|round(2) }}%**, Min DSCR **{{ m.get('dscr_min')|round(2) }}**, Payback index **{{ m.get('payback_year') }}**.

**8.1 Financing Plan:** Assumed debt–equity with tenor **{{ ass.get('tenor_years',7) }}** years.

**8.2 Sensitivity Analysis (Indicative):** ±10–20% on yields, ASP, energy tariffs, load factor.
""")

    sec(9, "Supporting Measures & Compliance", """
**9.1 Statutory & Regulatory Approvals:** Factory, FSSAI, PCB, Boiler, Electrical, Fire & Safety.

**9.2 Incentives & Subsidies (MoFPI, AIF, State Schemes, Export Benefits):** Subject to eligibility and timelines; capture snapshot and attach to annexures.
""")

    sec(10, "Risk Mitigation & Strategy", """
**10.1 Raw Material Supply Risks:** Multi-sourcing, buffer stocks.

**10.2 Energy Efficiency Measures:** Heat recovery, boiler optimization, high-efficiency drives.

**10.3 Quality & Recall Systems:** HACCP, traceability, recall SOPs.

**10.4 Market Diversification:** Domestic B2B + export mixes; variants for volatility hedging.
""")

    sec(11, "Execution Plan", """
**11.1 High-Level Implementation Schedule:** Engineering → Procurement → Civil & utilities → Erection & commissioning → PQ & ramp-up.

**11.2 Deliverables by Execution Partner:** PFD/P&IDs, vendor onboarding, commissioning SOPs, QC protocols, operator training.
""")

    sec(12, "Annexures (Summaries)", """
**12.1 Process Flow Description:** Detailed steps and unit operations.

**12.2 Equipment List – Indicative:** Mills, separators, extractors, filters, evaporators, spray dryer, utilities.

**12.3 Quality Specifications & Testing Protocols:** Protein %, moisture, micro, solubility; COAs.

**12.4 Environmental Management Plan:** Effluent handling, waste valorization.

**12.5 USD Presentation & Investor Readiness:** Export docs, certifications, buyer onboarding.
""")

    return S

# =============
# Report Export
# =============
def build_markdown(sections: Dict[str, Dict[str, str]]) -> str:
    lines = ["# Business Plan Report"]
    for i in range(1, 13):
        sid = str(i)
        sec = sections.get(sid, {})
        title = sec.get("title", f"Section {sid}")
        md = sec.get("markdown", "(pending)")
        lines.append(f"\n## {sid}. {title}\n{md}\n")
    return "\n".join(lines)

def build_docx(sections: Dict[str, Dict[str, str]]) -> bytes:
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)
    doc.add_heading("Business Plan Report", level=0)
    for i in range(1, 13):
        sid = str(i)
        sec = sections.get(sid, {})
        title = sec.get("title", f"Section {sid}")
        doc.add_heading(f"{sid}. {title}", level=1)
        content = sec.get("markdown", "")
        for para in content.split("\n\n"):
            doc.add_paragraph(para.replace("\n", " "))
    bio = BytesIO()
    doc.save(bio)
    bio.seek(0)
    return bio.read()

# =========
# Streamlit
# =========
st.set_page_config(page_title="BizPlan — One-Prompt Generator", layout="wide")
st.title("Business & Financial Planning — One-Prompt Generator (Streamlit)")

with st.expander("How it works", expanded=False):
    st.markdown("""
- Enter a plain-English **Prompt** describing the project.
- Adjust **Assumptions** (left sidebar) if needed.
- Click **Generate Plan** → the app computes financials, drafts Sections 1–12, verifies terms, and lets you download **Markdown** and **DOCX**.
- All financial numbers are computed deterministically (no LLM). Narratives are templated (you can later plug a real LLM).
    """)

# Sidebar assumptions
st.sidebar.header("Assumptions")
capex_total    = st.sidebar.number_input("Total CAPEX (₹)", value=1_200_000_000, min_value=0, step=10_000_000)
years          = st.sidebar.number_input("Projection Years", value=10, min_value=1, max_value=30, step=1)
year1_revenue  = st.sidebar.number_input("Year-1 Revenue (₹)", value=450_000_000, min_value=0, step=100_000)
revenue_growth = st.sidebar.number_input("Revenue Growth (decimal)", value=0.08, min_value=-0.5, max_value=1.0, step=0.01, format="%.2f")
ebitda_margin  = st.sidebar.number_input("EBITDA Margin (decimal)", value=0.22, min_value=0.0, max_value=1.0, step=0.01, format="%.2f")
debt_amount    = st.sidebar.number_input("Debt Amount (₹)", value=int(1_200_000_000*0.6), min_value=0, step=100_000)
interest_rate  = st.sidebar.number_input("Interest Rate (decimal)", value=0.11, min_value=0.0, max_value=1.0, step=0.01, format="%.2f")
tenor_years    = st.sidebar.number_input("Debt Tenor (years)", value=7, min_value=0, max_value=30, step=1)

prompt = st.text_area(
    "Project Prompt",
    value="Build a 200 TPD plant protein facility with dry+wet routes in a central agro belt. Target B2B exports for protein and domestic starch. Include MoFPI and AIF schemes. Focus on energy efficiency and ESG.",
    height=160
)

if st.button("Generate Plan", type="primary"):
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

    # 1) Calculations
    snap = run_calculations(assumptions)

    # 2) Draft all sections
    sections = draft_all_sections(prompt, assumptions, snap.metrics)

    # 3) Verification
    combined_text = "\n".join([s["markdown"] for s in sections.values()])
    issues = verify_snapshot(snap, combined_text)

    # KPIs
    st.subheader("Financial KPIs")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("IRR (Project)", f"{snap.metrics['irr_project']*100:.2f}%")
    c2.metric("DSCR (Min)", f"{snap.metrics['dscr_min']:.2f}")
    c3.metric("DSCR (Avg)", f"{snap.metrics['dscr_avg']:.2f}")
    c4.metric("Payback (year index)", str(snap.metrics['payback_year']))

    if issues:
        st.warning("Verification Issues:")
        for i in issues:
            st.write(f"• {i}")
    else:
        st.success("Verification passed: financial consistency & glossary terms present.")

    # Show sections
    st.subheader("Generated Report (Sections 1–12)")
    for i in range(1, 13):
        sid = str(i)
        with st.expander(f"{i}. {sections[sid]['title']}", expanded=(i <= 2)):
            st.markdown(sections[sid]["markdown"])

    # 4) Exports
    md = build_markdown(sections)
    docx_bytes = build_docx(sections)

    st.subheader("Download")
    st.download_button(
        label="⬇️ Download Markdown",
        data=md.encode("utf-8"),
        file_name=f"business_plan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md",
        mime="text/markdown"
    )
    st.download_button(
        label="⬇️ Download DOCX",
        data=docx_bytes,
        file_name=f"business_plan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx",
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )

st.caption("Deterministic financials; narratives templated. Swap drafting with your LLM when ready (grounded on calc snapshot).")
