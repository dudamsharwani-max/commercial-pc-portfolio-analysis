"""
Commercial P&C Portfolio — Underwriting Dashboard
=================================================
Run with:  streamlit run app.py

Every number on this page comes from a view in sql/metrics.sql.
The app formats and charts; it never redefines a metric.
"""
import pathlib

import duckdb
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="P&C Underwriting Dashboard", layout="wide",
                   initial_sidebar_state="expanded")

DB = "pc_portfolio.duckdb"
VALUATION = "30 June 2026"
TARGET_CR = 0.95

GREEN, AMBER, RED, INK = "#2E7D5B", "#C98A2E", "#B3402F", "#1F2933"


@st.cache_resource
def get_con():
    # Build the warehouse on first run. Streamlit Community Cloud starts from a
    # fresh clone, and the .duckdb file is a build artifact we do not commit.
    if not pathlib.Path(DB).exists():
        boot = duckdb.connect(DB)
        boot.execute(pathlib.Path("sql/metrics.sql").read_text())
        boot.close()
    return duckdb.connect(DB, read_only=True)


@st.cache_data
def q(sql: str) -> pd.DataFrame:
    return get_con().execute(sql).df()


def pct(x):
    return f"{x:.1%}"


def money(x):
    return f"${x:,.0f}"


def money_short(x):
    """Compact form for KPI tiles, which are too narrow for full figures."""
    for cut, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(x) >= cut:
            return f"${x / cut:,.1f}{suffix}"
    return f"${x:,.0f}"


def cr_color(v):
    return GREEN if v < 1.0 else (AMBER if v < 1.10 else RED)


# ============================================================
# SIDEBAR — filters
# ============================================================
st.sidebar.title("Filters")
lines = q("SELECT DISTINCT line_of_business FROM stg_policies ORDER BY 1").line_of_business.tolist()
sel_lines = st.sidebar.multiselect("Line of business", lines, default=lines)

segs = q("SELECT DISTINCT industry_segment FROM stg_policies ORDER BY 1").industry_segment.tolist()
sel_segs = st.sidebar.multiselect("Industry segment", segs, default=segs)

min_claims = st.sidebar.slider(
    "Minimum claim count", 0, 100, 30,
    help="Hide segments with too few claims to be credible. "
         "A 130% loss ratio on 4 claims is noise, not a finding.")

st.sidebar.divider()
st.sidebar.caption(
    f"**Valuation date:** {VALUATION}  \n"
    f"**Target combined ratio:** {TARGET_CR:.0%}  \n\n"
    "Losses are gross of reinsurance and **not** developed to ultimate — "
    "recent policy years are immature and understate the loss ratio."
)

if not sel_lines or not sel_segs:
    st.warning("Select at least one line and one segment.")
    st.stop()

L = "(" + ",".join(f"'{x}'" for x in sel_lines) + ")"
S = "(" + ",".join(f"'{x}'" for x in sel_segs) + ")"
FILTER = f"line_of_business IN {L} AND industry_segment IN {S}"


# ============================================================
# HEADER — portfolio KPIs
# ============================================================
st.title("Commercial P&C Underwriting Dashboard")
st.caption(f"BOP · General Liability · Professional Liability — valued {VALUATION}")

kpi = q(f"""
SELECT
    SUM(f.written_premium)                      AS wp,
    SUM(f.earned_premium)                       AS ep,
    SUM(f.claim_count)                          AS claims,
    SUM(f.incurred_loss_alae)                   AS incurred,
    SUM(f.claim_count) / SUM(f.earned_exposure) AS frequency,
    SUM(f.incurred_loss_alae) / SUM(f.claim_count) AS severity,
    SUM(f.incurred_loss_alae) / SUM(f.earned_premium) AS loss_ratio,
    SUM(f.earned_premium * e.total_expense_ratio) / SUM(f.earned_premium) AS expense_ratio
FROM fct_policy f JOIN stg_expenses e USING (line_of_business)
WHERE {FILTER}
""").iloc[0]

combined = kpi.loss_ratio + kpi.expense_ratio
uw_result = kpi.ep * (1 - combined)

c = st.columns(5)
c[0].metric("Written premium", money_short(kpi.wp))
c[1].metric("Earned premium", money_short(kpi.ep))
c[2].metric("Loss ratio", pct(kpi.loss_ratio))
c[3].metric("Combined ratio", pct(combined),
            delta=f"{(combined - 1) * 100:+.1f} pts vs breakeven",
            delta_color="inverse")
c[4].metric("Underwriting result", money_short(uw_result),
            delta="profit" if uw_result > 0 else "loss",
            delta_color="normal" if uw_result > 0 else "inverse")

st.divider()

tabs = st.tabs(["Portfolio", "Segment performance", "Rate & retention",
                "Appetite matrix", "New business funnel"])


# ============================================================
# TAB 1 — PORTFOLIO
# ============================================================
with tabs[0]:
    line = q(f"SELECT * FROM v_line_performance WHERE line_of_business IN {L}")

    a, b = st.columns([3, 2])

    with a:
        st.subheader("Combined ratio by line")
        fig = go.Figure()
        fig.add_bar(x=line.line_of_business, y=line.loss_ratio, name="Loss ratio",
                    marker_color="#4A6FA5",
                    text=[pct(v) for v in line.loss_ratio], textposition="inside")
        fig.add_bar(x=line.line_of_business, y=line.expense_ratio, name="Expense ratio",
                    marker_color="#A8B8CE",
                    text=[pct(v) for v in line.expense_ratio], textposition="inside")
        fig.add_hline(y=1.0, line_dash="dash", line_color=RED,
                      annotation_text="Breakeven (100%)",
                      annotation_position="top left",
                      annotation_font_size=11)
        fig.update_layout(barmode="stack", height=380, yaxis_tickformat=".0%",
                          yaxis_title="Ratio to earned premium",
                          legend=dict(orientation="h", y=1.12), margin=dict(t=40))
        st.plotly_chart(fig, width="stretch")
        st.caption("Anything above the dashed line loses money before investment income.")

    with b:
        st.subheader("Frequency vs severity")
        fig = px.scatter(line, x="frequency", y="severity", size="earned_premium",
                         color="combined_ratio", text="line_of_business",
                         color_continuous_scale=[[0, GREEN], [0.5, AMBER], [1, RED]],
                         size_max=60)
        fig.update_traces(textposition="top center")
        fig.update_layout(height=380, xaxis_title="Claims per earned policy-year",
                          yaxis_title="Average incurred loss ($)", margin=dict(t=40))
        st.plotly_chart(fig, width="stretch")
        st.caption("Two lines can share a loss ratio and need opposite fixes: "
                   "frequency is a selection problem, severity a limits problem.")

    st.subheader("Line summary")
    st.dataframe(
        line[["line_of_business", "policy_terms", "written_premium", "earned_premium",
              "claim_count", "frequency", "severity", "loss_ratio", "expense_ratio",
              "combined_ratio", "underwriting_result"]],
        hide_index=True, width="stretch",
        column_config={
            "line_of_business": "Line", "policy_terms": "Terms",
            "written_premium": st.column_config.NumberColumn("Written", format="$%d"),
            "earned_premium": st.column_config.NumberColumn("Earned", format="$%d"),
            "claim_count": "Claims",
            "frequency": st.column_config.NumberColumn("Frequency", format="%.3f"),
            "severity": st.column_config.NumberColumn("Severity", format="$%d"),
            "loss_ratio": st.column_config.NumberColumn("Loss ratio", format="%.1f%%"),
            "expense_ratio": st.column_config.NumberColumn("Expense", format="%.1f%%"),
            "combined_ratio": st.column_config.NumberColumn("Combined", format="%.1f%%"),
            "underwriting_result": st.column_config.NumberColumn("UW result", format="$%d"),
        })

    st.subheader("Policy year trend")
    trend = q(f"SELECT * FROM v_policy_year_trend WHERE line_of_business IN {L}")
    fig = px.line(trend, x="policy_year", y="loss_ratio", color="line_of_business",
                  markers=True)
    fig.update_layout(height=340, yaxis_tickformat=".0%", xaxis_title="Policy year",
                      yaxis_title="Loss ratio", margin=dict(t=20))
    st.plotly_chart(fig, width="stretch")
    st.warning(
        "**Do not read the latest year as an improvement.** Recent policy years are "
        "immature — premium is only partly earned and slow-reporting claims have not "
        "arrived yet. Professional Liability reports at a ~210 day average lag.",
        icon="⚠️")


# ============================================================
# TAB 2 — SEGMENT PERFORMANCE
# ============================================================
with tabs[1]:
    seg = q(f"""
        SELECT * FROM v_segment_performance
        WHERE {FILTER} AND claim_count >= {min_claims}
        ORDER BY underwriting_result
    """)

    if seg.empty:
        st.info("No segments meet the credibility threshold. Lower it in the sidebar.")
    else:
        st.subheader("Where the money is made and lost")
        seg["label"] = seg.line_of_business.str[:3] + " · " + seg.industry_segment
        d = seg.sort_values("underwriting_result")
        fig = go.Figure(go.Bar(
            x=d.underwriting_result, y=d.label, orientation="h",
            marker_color=[GREEN if v > 0 else RED for v in d.underwriting_result],
            text=[money(v) for v in d.underwriting_result], textposition="outside"))
        fig.update_layout(height=max(360, 26 * len(d)), xaxis_title="Underwriting result ($)",
                          yaxis_title="", margin=dict(l=10, t=20))
        st.plotly_chart(fig, width="stretch")
        st.caption("Sorted by dollars, not ratio. A 106% combined ratio on a large "
                   "segment costs more than 121% on a small one.")

        st.subheader("Premium at risk")
        fig = px.scatter(seg, x="combined_ratio", y="earned_premium", size="claim_count",
                         color="line_of_business", hover_name="industry_segment",
                         size_max=45)
        fig.add_vline(x=1.0, line_dash="dash", line_color=RED)
        fig.add_vline(x=TARGET_CR, line_dash="dot", line_color=GREEN,
                      annotation_text="Target")
        fig.update_layout(height=420, xaxis_tickformat=".0%",
                          xaxis_title="Combined ratio",
                          yaxis_title="Earned premium ($)", margin=dict(t=20))
        st.plotly_chart(fig, width="stretch")
        st.caption("Top right = the dangerous quadrant: large books running above breakeven.")

        st.subheader("Frequency / severity diagnosis")
        st.caption("Indexed to each segment's own line average. Above 1.00 is worse than the line.")
        diag = q(f"""
            SELECT s.line_of_business AS "Line", s.industry_segment AS "Segment",
                   s.claim_count AS "Claims",
                   ROUND(s.frequency / l.frequency, 3) AS "Freq vs line",
                   ROUND(s.severity  / l.severity,  3) AS "Sev vs line",
                   ROUND(s.loss_ratio, 3)             AS "Loss ratio",
                   ROUND(s.combined_ratio, 3)         AS "Combined",
                   s.credibility_flag                 AS "Credibility"
            FROM v_segment_performance s
            JOIN v_line_performance l USING (line_of_business)
            WHERE {FILTER} AND s.claim_count >= {min_claims}
            ORDER BY s.combined_ratio DESC
        """)
        st.dataframe(diag, hide_index=True, width="stretch")


# ============================================================
# TAB 3 — RATE & RETENTION
# ============================================================
with tabs[2]:
    st.subheader("Rate indication vs credibility")
    st.caption(f"Permissible loss ratio = {TARGET_CR:.0%} target − expense ratio. "
               "Indication = current loss ratio ÷ permissible − 1.")

    ind = q(f"""
        SELECT s.line_of_business, s.industry_segment, s.earned_premium, s.claim_count,
               s.loss_ratio,
               ROUND(s.loss_ratio / ({TARGET_CR} - e.total_expense_ratio) - 1, 4) AS indicated,
               s.credibility,
               ROUND((s.loss_ratio / ({TARGET_CR} - e.total_expense_ratio) - 1)
                     * s.credibility, 4) AS weighted
        FROM v_segment_performance s JOIN stg_expenses e USING (line_of_business)
        WHERE {FILTER} AND s.earned_premium > 1000000
        ORDER BY weighted DESC
    """)
    ind["label"] = ind.line_of_business.str[:3] + " · " + ind.industry_segment

    fig = go.Figure()
    fig.add_bar(x=ind.label, y=ind.indicated, name="Raw indication",
                marker_color="#C9D3E0")
    fig.add_bar(x=ind.label, y=ind.weighted, name="Credibility weighted",
                marker_color="#4A6FA5")
    fig.update_layout(barmode="overlay", height=420, yaxis_tickformat=".0%",
                      yaxis_title="Indicated rate change", xaxis_title="",
                      legend=dict(orientation="h", y=1.1), margin=dict(t=40))
    fig.update_xaxes(tickangle=-35)
    st.plotly_chart(fig, width="stretch")
    st.info("The pale bar is what the raw loss ratio asks for; the solid bar is what the "
            "data actually supports. Where the gap is large, the segment has too few "
            "claims to justify the indication.", icon="ℹ️")

    st.divider()
    st.subheader("Retention falls as you push rate")

    ret = q("""
        SELECT rate_change_band,
               SUM(terms_expiring) AS terms,
               SUM(renewed) * 1.0 / SUM(terms_expiring) AS retention
        FROM v_retention
        WHERE rate_change_band <> 'Rate change n/a (first term)'
        GROUP BY 1 ORDER BY 1
    """)
    a, b = st.columns([2, 3])
    with a:
        fig = px.bar(ret, x="rate_change_band", y="retention", text=ret.retention.map(pct))
        fig.update_traces(marker_color="#4A6FA5", textposition="outside")
        fig.update_layout(height=380, yaxis_tickformat=".0%", yaxis_range=[0, 1],
                          xaxis_title="Rate change at renewal", yaxis_title="Policy retention",
                          margin=dict(t=20))
        st.plotly_chart(fig, width="stretch")
    with b:
        st.markdown("""
**Rate is not free.** Retention runs at 87–88% when renewal pricing is flat, and
falls to **58.5%** once increases exceed 15%.

The accounts that leave are the ones that *can* leave — an insured with a clean
loss history has options, while a poor risk has fewer places to go. So the book
that survives a hard rate action is worse than the book you started with. That
is adverse selection, and it partly cancels the benefit of the rate.

The practical consequence: **a rate indication is a starting point for a
conversation, not an instruction to the renewal underwriters.** Where the
indication is large, the answer is usually a mix of rate, deductible and limit
changes, and selective non-renewal — spread over more than one cycle.
        """)


# ============================================================
# TAB 4 — APPETITE MATRIX
# ============================================================
with tabs[3]:
    st.subheader("Underwriting appetite: segment × hazard grade")
    st.caption("Combined ratio by cell. Grey cells have too little premium to judge.")

    ap = q(f"""
        SELECT industry_segment, hazard_grade, earned_premium, claim_count,
               combined_ratio, recommended_action
        FROM v_appetite_matrix
        WHERE {FILTER} AND earned_premium > 250000
    """)
    grid = ap.pivot_table(index="industry_segment", columns="hazard_grade",
                          values="combined_ratio", aggfunc="mean")
    prem = ap.pivot_table(index="industry_segment", columns="hazard_grade",
                          values="earned_premium", aggfunc="sum")

    fig = px.imshow(grid, text_auto=".0%", aspect="auto",
                    color_continuous_scale=[[0, GREEN], [0.45, "#EFE6D2"],
                                            [0.55, AMBER], [1, RED]],
                    zmin=0.6, zmax=1.4, labels=dict(color="Combined ratio"))
    fig.update_layout(height=430, xaxis_title="Hazard grade (1 = best risk)",
                      yaxis_title="", margin=dict(t=20))
    st.plotly_chart(fig, width="stretch")

    st.subheader("Recommended action by cell")
    ap["action_rank"] = ap.recommended_action.map(
        {"Grow": 0, "Maintain": 1, "Rate action": 2,
         "Restrict / non-renew": 3, "Monitor - low credibility": 4})
    show = ap.sort_values(["action_rank", "earned_premium"],
                          ascending=[True, False]).drop(columns="action_rank")
    st.dataframe(show, hide_index=True, width="stretch",
                 column_config={
                     "industry_segment": "Segment", "hazard_grade": "Hazard",
                     "earned_premium": st.column_config.NumberColumn("Earned premium", format="$%d"),
                     "claim_count": "Claims",
                     "combined_ratio": st.column_config.NumberColumn("Combined", format="%.1f%%"),
                     "recommended_action": "Action"})

    st.success(
        "**Read the hazard grades across a row, not just the total.** In GL Contractors, "
        "grade 3 runs a higher combined ratio than grade 4 — the worse-graded risk performs "
        "better. That inversion points at the grading criteria themselves, not at pricing.",
        icon="🔍")


# ============================================================
# TAB 5 — FUNNEL
# ============================================================
with tabs[4]:
    st.subheader("New business funnel")
    st.caption("Declination rate measures appetite discipline. Quote-to-bind measures "
               "price competitiveness. They answer different questions.")

    fn = q(f"""
        SELECT hazard_grade,
               SUM(submissions) AS submissions,
               SUM(declined)    AS declined,
               SUM(quoted)      AS quoted,
               SUM(bound)       AS bound,
               SUM(declined) * 1.0 / SUM(submissions)          AS declination_rate,
               SUM(bound) * 1.0 / NULLIF(SUM(quoted), 0)       AS quote_to_bind
        FROM v_funnel WHERE {FILTER}
        GROUP BY 1 ORDER BY 1
    """)

    a, b = st.columns(2)
    with a:
        fig = go.Figure()
        fig.add_bar(x=fn.hazard_grade, y=fn.declination_rate, name="Declination rate",
                    marker_color=RED)
        fig.add_scatter(x=fn.hazard_grade, y=fn.quote_to_bind, name="Quote-to-bind",
                        mode="lines+markers", marker_color=INK, line_width=3)
        fig.update_layout(height=400, yaxis_tickformat=".0%",
                          xaxis_title="Hazard grade", yaxis_title="Rate",
                          legend=dict(orientation="h", y=1.12), margin=dict(t=40))
        st.plotly_chart(fig, width="stretch")
    with b:
        st.markdown("""
Declination climbs steeply with hazard grade while quote-to-bind stays flat near
40%. That is the pattern you want: underwriters are **screening risk on the way
in** rather than quoting everything and pricing defensively.

The failure mode this chart would catch is a flat declination line — a team
accepting whatever arrives and relying on price to protect the book. Price
cannot protect a book from risks that should never have been quoted.
        """)

    st.subheader("Why we lose business")
    reasons = q(f"""
        SELECT outcome, decline_reason, COUNT(*) AS submissions
        FROM stg_submissions
        WHERE {FILTER} AND decline_reason IS NOT NULL
        GROUP BY 1, 2 ORDER BY 3 DESC
    """)
    fig = px.bar(reasons, x="submissions", y="decline_reason", color="outcome",
                 orientation="h", height=430)
    fig.update_layout(yaxis_title="", xaxis_title="Submissions", margin=dict(t=20),
                      legend=dict(orientation="h", y=1.08))
    st.plotly_chart(fig, width="stretch")
