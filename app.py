import os
import io
import json
import math
from datetime import datetime
from typing import Dict, Any

import pandas as pd
import numpy as np

# ----------------------------
# Optional deps (guarded)
# ----------------------------
has_numpy_fin = True
try:
    import numpy_financial as npf
except Exception:
    has_numpy_fin = False

has_plotly = True
try:
    import plotly.express as px
except Exception:
    has_plotly = False

has_docx = True
try:
    from docx import Document
    from docx.shared import Pt
except Exception:
    has_docx = False

has_markdown2 = True
try:
    import markdown2
except Exception:
    has_markdown2 = False

has_pdf = True
pdf_err = None
try:
    # xhtml2pdf depends on reportlab; both are pinned in requirements
    from xhtml2pdf import pisa
except Exception as e:
    has_pdf = False
    pdf_err = str(e)

has_requests = True
try:
    import requests
except Exception:
    has_requests = False

has_streamlit = True
try:
    import streamlit as st
except Exception:
    has_streamlit = False
    raise  # Streamlit is mandatory


# =========================
# ----- HELPER LOGIC -----
# =========================
def annuity_payment(principal: float, annual_rate: float, years: int) -> float:
    if years <= 0:
        return 0.0
    if annual_rate == 0:
        return principal / years
    r = annual_rate
    n = years
    return principal * (r * (1 + r) ** n) / ((1 + r) ** n - 1)


def build_debt_schedule(principal: float, annual_rate: float, years: int) -> pd.DataFrame:
    pay = annuity_payment(principal, annual_rate, years)
    rows = []
    bal = principal
    for yr in range(1, years + 1):
        interest = bal * annual_rate
        principal_pay = max(0.0, pay - interest)
        bal = max(0.0, bal - principal_pay)
        rows.append(
            {
                "year": yr,
                "debt_opening": round(bal + principal_pay, 2),
                "debt_payment_total": round(pay, 2),
                "interest": round(interest, 2),
                "principal": round(principal_pay, 2),
                "debt_closing": round(bal, 2),
            }
        )
    return pd.DataFrame(rows)


def _irr_fallback(cashflows):
    """Simple fallback IRR (bisection) if numpy_financial isn't available."""
    # Find IRR in [-0.9, 1.0] with bisection
    def npv(rate):
        return sum(cf / ((1 + rate) ** i) for i, cf in enumerate(cashflows))
    lo, hi = -0.9, 1.0
    for _ in range(80):
        mid = (lo + hi) / 2
        val = npv(mid)
        if val > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def compute_financials(assump: Dict[str, Any]) -> Dict[str, Any]:
    years = int(assump.get("years", 10))
    y1_rev = float(assump.get("year1_revenue", 450_000_000))
    growth = float(assump.get("revenue_growth", 0.08))
    ebitda_margin = float(assump.get("ebitda_margin", 0.22))
    capex_total = float(assump.get("capex_total", 1_200_000_000))
    interest_rate = float(assump.get("interest_rate", 0.11))
    tenor_years = int(assump.get("tenor_years", 7))
    debt_ratio = float(assump.get("debt_ratio", 0.7))
    tax_rate = float(assump.get("tax_rate", 0.25))

    revenue = [y1_rev * ((1 + growth) ** i) for i in range(years)]
    ebitda = [r * ebitda_margin for r in revenue]
    # Straight-line D&A proxy: 5% of capex for every model year
    da = [capex_total * 0.05] * years

    debt_amt = capex_total * debt_ratio
    debt = build_debt_schedule(debt_amt, interest_rate, tenor_years)
    if tenor_years < years:
        tail = pd.DataFrame(
            {
                "year": list(range(tenor_years + 1, years + 1)),
                "debt_opening": 0.0,
                "debt_payment_total": 0.0,
                "interest": 0.0,
                "principal": 0.0,
                "debt_closing": 0.0,
            }
        )
        debt = pd.concat([debt, tail], ignore_index=True)

    ebit = [max(0.0, ebitda[i] - da[i]) for i in range(years)]
    taxes = [ebit[i] * tax_rate for i in range(years)]
    ocf = [ebitda[i] - taxes[i] for i in range(years)]

    ds = debt["debt_payment_total"].to_list()
    dscr = [(ocf[i] / ds[i] if ds[i] > 0 else np.nan) for i in range(years)]

    equity = capex_total * (1 - debt_ratio)
    eq_cf = [-equity] + [ocf[i] - ds[i] for i in range(years)]
    if has_numpy_fin:
        try:
            irr_equity = float(npf.irr(eq_cf))
        except Exception:
            irr_equity = float("nan")
    else:
        irr_equity = _irr_fallback(eq_cf)

    cum = 0.0
    payback_year = None
    for idx, cf in enumerate(eq_cf[1:], start=1):
        cum += cf
        if cum >= 0 and payback_year is None:
            payback_year = idx

    df = pd.DataFrame(
        {
            "Year": list(range(1, years + 1)),
            "Revenue": revenue,
            "EBITDA": ebitda,
            "D&A": da,
            "EBIT": ebit,
            "Tax": taxes,
            "OCF_before_DebtSvc": ocf,
            "DebtService": ds[:years],
            "DSCR": dscr[:years],
            "DebtClosing": debt["debt_closing"][:years].to_list(),
        }
    )

    metrics = {
        "revenue_yearly": revenue,
        "ebitda_yearly": ebitda,
        "ebitda_margin": ebitda_margin,
        "irr_equity": irr_equity,
        "dscr_min": float(np.nanmin(dscr)),
        "dscr_avg": float(
            np.nanmean([x for x in dscr if not (isinstance(x, float) and math.isnan(x))])
        ) if any([not (isinstance(x, float) and math.isnan(x)) for x in dscr]) else float("nan"),
        "payback_year": payback_year,
        "debt_ratio": debt_ratio,
        "tax_rate": tax_rate,
    }
    return {"df": df, "debt": debt, "metrics": metrics}


def default_prompt() -> str:
    return (
        "Generate a detailed investor-grade business plan for a 200 TPD plant protein "
        "facility in India. Use the provided metrics and assumptions. Write 60–100 pages worth of "
        "sections when expanded to Word/PDF, but return concise, well-structured Markdown with rich headings, "
        "tables and annexures I can later export. Include Executive Summary, Market & Competitive Landscape, "
        "Technical Feasibility, CAPEX/OPEX, Financials, Risks, Execution Roadmap, and Appendices."
    )


def build_markdown_report(assump: Dict[str, Any], metrics: Dict[str, Any]) -> str:
    lines = []
    lines.append("# Business Plan Report\n")
    lines.append("## 1. Executive Summary")
    lines.append(f"- Total CAPEX (input): ₹{assump['capex_total']:,}")
    lines.append(f"- EBITDA margin: {metrics['ebitda_margin']:.2%}")
    lines.append(f"- Equity IRR (simple): {metrics['irr_equity']:.2%}")
    lines.append(f"- DSCR: min {metrics['dscr_min']:.2f} | avg {metrics['dscr_avg']:.2f}")
    lines.append(f"- Payback (years, simple): {metrics['payback_year']}\n")
    lines.append("## 2. Assumptions\n```json")
    lines.append(json.dumps(assump, indent=2))
    lines.append("```\n")
    lines.append("## 3. Financial KPI Highlights\n| KPI | Value |\n|---|---|")
    lines.append(f"| EBITDA margin | {metrics['ebitda_margin']:.2%} |")
    lines.append(f"| Equity IRR | {metrics['irr_equity']:.2%} |")
    lines.append(f"| DSCR (min) | {metrics['dscr_min']:.2f} |")
    lines.append(f"| DSCR (avg) | {metrics['dscr_avg']:.2f} |")
    lines.append(f"| Payback (years) | {metrics['payback_year']} |\n")
    lines.append("## 4. Sections (placeholders)")
    for sec in [
        "Promoter Profile", "Industry & Market Analysis", "Technical Feasibility",
        "Location & Infrastructure", "Project Costs (CAPEX)", "Operating Costs (OPEX)",
        "Revenue & Financial Analysis", "Compliance & Incentives",
        "Risk Register & Mitigation", "Execution Plan", "Annexures"
    ]:
        lines.append(f"### {sec}\n_TBD—use **Generate with GPT** to auto-write this section._\n")
    return "\n".join(lines)


def markdown_to_pdf_bytes(md_text: str) -> bytes:
    if not (has_markdown2 and has_pdf):
        raise RuntimeError("PDF engine not available.")
    html = markdown2.markdown(md_text, extras=["tables", "fenced-code-blocks", "footnotes"])
    style = """
    <style>
    body { font-family: DejaVu Sans, Helvetica, Arial, sans-serif; font-size: 12px; }
    h1,h2,h3 { color: #222; }
    table { border-collapse: collapse; width: 100%; }
    th, td { border: 1px solid #999; padding: 6px; }
    code { background: #f2f2f2; padding: 2px 4px; }
    </style>
    """
    html = f"<!doctype html><html><head><meta charset='utf-8'>{style}</head><body>{html}</body></html>"
    out = io.BytesIO()
    result = pisa.CreatePDF(src=io.StringIO(html), dest=out)
    if result.err:
        raise RuntimeError("PDF generation failed (xhtml2pdf).")
    return out.getvalue()


def markdown_to_docx_bytes(md_text: str) -> bytes:
    if not has_docx:
        raise RuntimeError("python-docx not available.")
    doc = Document()
    styles = doc.styles["Normal"]
    styles.font.name = "Calibri"
    styles.font.size = Pt(11)
    for line in md_text.splitlines():
        if line.startswith("# "):
            doc.add_heading(line[2:].strip(), level=1)
        elif line.startswith("## "):
            doc.add_heading(line[3:].strip(), level=2)
        elif line.startswith("### "):
            doc.add_heading(line[4:].strip(), level=3)
        else:
            doc.add_paragraph(line)
    bio = io.BytesIO()
    doc.save(bio)
    return bio.getvalue()


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8")


def call_local_api(base_url: str, payload: Dict[str, Any], timeout: int = 120) -> Dict[str, Any]:
    if not has_requests:
        raise RuntimeError("requests not installed.")
    url = base_url.rstrip("/") + "/generate"
    r = requests.post(url, json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json()


def call_openai_gpt(api_key: str, model: str, sys_prompt: str, user_prompt: str) -> str:
    # Import lazily to keep base app running without it
    try:
        from openai import OpenAI
    except Exception as e:
        raise RuntimeError(f"OpenAI SDK not installed: {e}")
    client = OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,
    )
    return resp.choices[0].message.content


# =========================
# ----- STREAMLIT UI ------
# =========================
st.set_page_config(
    page_title="BizPlan Generator",
    page_icon="📈",
    layout="wide",
)

st.title("📈 BizPlan — Plant Protein Facility (200 TPD)")
st.caption("Generate a plan, visualize, export PDF/DOCX/CSV/Markdown, and draft with GPT.")

# Sidebar inputs
st.sidebar.header("Assumptions")
with st.sidebar.form("assumptions_form"):
    capex_total = st.number_input("Total CAPEX (₹)", 0, step=10_000_000, value=1_200_000_000)
    years = st.number_input("Model Years", 1, 50, 10)
    y1_rev = st.number_input("Year 1 Revenue (₹)", 0, step=10_000_000, value=450_000_000)
    growth = st.number_input("Revenue Growth (YoY, %)", 0.0, 1.0, 0.08, step=0.01, format="%.2f")
    ebitda_margin = st.number_input("EBITDA Margin (%)", 0.0, 1.0, 0.22, step=0.01, format="%.2f")
    interest_rate = st.number_input("Interest Rate (%)", 0.0, 1.0, 0.11, step=0.005, format="%.3f")
    tenor_years = st.number_input("Debt Tenor (yrs)", 1, 50, 7)
    debt_ratio = st.number_input("Debt Ratio", 0.0, 1.0, 0.70, step=0.05)
    tax_rate = st.number_input("Tax Rate", 0.0, 1.0, 0.25, step=0.01)
    st.form_submit_button("Recalculate")

assump = {
    "capex_total": capex_total,
    "years": years,
    "year1_revenue": y1_rev,
    "revenue_growth": growth,
    "ebitda_margin": ebitda_margin,
    "interest_rate": interest_rate,
    "tenor_years": tenor_years,
    "debt_ratio": debt_ratio,
    "tax_rate": tax_rate,
}

calc = compute_financials(assump)
df, debt_df, metrics = calc["df"], calc["debt"], calc["metrics"]

# Session markdown
if "report_md" not in st.session_state:
    st.session_state["report_md"] = build_markdown_report(assump, metrics)

# Tabs
tab1, tab2, tab3, tab4, tab5 = st.tabs(
    ["📊 Dashboard", "🧠 GPT", "🧾 Report (Markdown)", "⚙️ Integrations & Export", "🛠 Diagnostics"]
)

# --- Dashboard
with tab1:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Equity IRR (simple)", f"{metrics['irr_equity']*100:,.2f}%")
    c2.metric("DSCR (min)", f"{metrics['dscr_min']:.2f}")
    c3.metric("DSCR (avg)", f"{metrics['dscr_avg']:.2f}")
    c4.metric("Payback (yrs)", metrics["payback_year"] if metrics["payback_year"] else "n/a")

    if has_plotly:
        l, r = st.columns(2)
        with l:
            st.subheader("Revenue & EBITDA")
            df_long = df.melt(id_vars="Year", value_vars=["Revenue", "EBITDA"], var_name="Item", value_name="Amount")
            fig1 = px.line(df_long, x="Year", y="Amount", color="Item")
            st.plotly_chart(fig1, use_container_width=True)
        with r:
            st.subheader("DSCR by Year")
            st.plotly_chart(px.bar(df, x="Year", y="DSCR"), use_container_width=True)

        st.subheader("Sensitivity — EBITDA Margin vs Revenue Growth")
        g_vals = np.linspace(max(0, growth - 0.05), growth + 0.05, 5)
        m_vals = np.linspace(max(0, ebitda_margin - 0.05), min(0.6, ebitda_margin + 0.05), 5)
        grid = []
        for g in g_vals:
            for m in m_vals:
                tmp = compute_financials({**assump, "revenue_growth": float(g), "ebitda_margin": float(m)})
                grid.append({"Growth": round(g, 3), "Margin": round(m, 3), "Eq_IRR": tmp["metrics"]["irr_equity"]})
        heat = pd.DataFrame(grid)
        st.dataframe(heat.pivot(index="Margin", columns="Growth", values="Eq_IRR").round(4), use_container_width=True)
    else:
        st.info("Plotly not installed; showing tables only.")

    st.subheader("Debt Amortization Schedule")
    st.dataframe(debt_df, use_container_width=True)
    st.subheader("Financial Table")
    st.dataframe(df, use_container_width=True)

# --- GPT
with tab2:
    st.markdown("Use GPT to auto-draft or refine the report based on your assumptions & metrics.")
    api_key = st.text_input("OpenAI API Key", value=os.getenv("OPENAI_API_KEY", ""), type="password")
    model = st.selectbox("Model", ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1"])
    system_prompt = st.text_area(
        "System Prompt",
        "You are an expert banker and process engineer creating investor-grade business plans in concise Markdown.",
        height=80
    )
    user_prompt = st.text_area("User Prompt", default_prompt(), height=140)
    if st.button("🚀 Generate with GPT", type="primary", disabled=(api_key.strip() == "")):
        seed = {"assumptions": assump, "metrics": metrics, "now": datetime.utcnow().isoformat()}
        enriched = (
            f"{user_prompt}\n\nHere are the numeric inputs as JSON:\n```json\n{json.dumps(seed, indent=2)}\n```"
        )
        try:
            md = call_openai_gpt(api_key, model, system_prompt, enriched)
            st.session_state["report_md"] = md
            st.success("Report generated.")
        except Exception as e:
            st.error(f"OpenAI call failed: {e}")

# --- Report (Markdown)
with tab3:
    st.session_state["report_md"] = st.text_area(
        "Business Plan (Markdown)", value=st.session_state["report_md"], height=520
    )
    st.download_button(
        "⬇️ Download Markdown",
        data=st.session_state["report_md"].encode("utf-8"),
        file_name="bizplan.md",
        mime="text/markdown",
        use_container_width=True
    )

# --- Integrations & Export
with tab4:
    st.subheader("Local API Integration (optional)")
    base_url = st.text_input("Base URL", value="http://127.0.0.1:8000")
    if st.button("Call /generate"):
        payload = {
            "prompt": user_prompt or default_prompt(),
            "assumptions": assump,
            "detail": "Investor Deep-Dive (60–100 pages)",
            "pages": 90
        }
        try:
            resp = call_local_api(base_url, payload)
            if isinstance(resp, dict):
                if resp.get("markdown"):
                    st.session_state["report_md"] = resp["markdown"]
                    st.success("Report merged from API.")
                if resp.get("metrics"):
                    st.info("Metrics from API (view only):")
                    st.json(resp["metrics"])
        except Exception as e:
            st.error(f"Local API call failed: {e}")

    st.divider()
    st.subheader("Export")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.download_button("⬇️ Financials (CSV)", data=to_csv_bytes(df), file_name="financials.csv", mime="text/csv")
    with c2:
        st.download_button("⬇️ Debt (CSV)", data=to_csv_bytes(debt_df), file_name="debt_schedule.csv", mime="text/csv")
    with c3:
        if has_markdown2 and has_pdf:
            try:
                pdf_bytes = markdown_to_pdf_bytes(st.session_state["report_md"])
                st.download_button("⬇️ PDF", data=pdf_bytes, file_name="bizplan.pdf", mime="application/pdf")
            except Exception as e:
                st.error(f"PDF error: {e}")
        else:
            st.warning("PDF export unavailable (xhtml2pdf/markdown2 not installed).")
    with c4:
        if has_docx:
            try:
                docx_bytes = markdown_to_docx_bytes(st.session_state["report_md"])
                st.download_button(
                    "⬇️ DOCX",
                    data=docx_bytes,
                    file_name="bizplan.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            except Exception as e:
                st.error(f"DOCX error: {e}")
        else:
            st.warning("DOCX export unavailable (python-docx not installed).")

# --- Diagnostics
with tab5:
    st.subheader("Environment Diagnostics")
    st.write({
        "python_version": os.sys.version.split()[0],
        "has_numpy_financial": has_numpy_fin,
        "has_plotly": has_plotly,
        "has_markdown2": has_markdown2,
        "has_xhtml2pdf(reportlab)": has_pdf,
        "has_python_docx": has_docx,
        "has_requests": has_requests,
        "pdf_import_error": pdf_err,
    })
    st.caption("If something shows False, check your requirements or pin compatible versions.")
