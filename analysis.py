"""
Step 3 — Portfolio Analysis
============================
Three questions an underwriting manager would actually ask:
  A. WHY is Professional Liability losing money — frequency or severity?
  B. HOW MUCH rate does GL Contractors need to reach breakeven?
  C. WHAT DOES that rate cost us in lost accounts?
"""
import duckdb
import pandas as pd

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 40)
con = duckdb.connect("pc_portfolio.duckdb")

TARGET_CR = 0.95          # target combined ratio — 5 pts of underwriting profit
BAR = "=" * 78


# =============================================================
# A. FREQUENCY / SEVERITY DECOMPOSITION
# =============================================================
# Loss ratio = (frequency x severity) / average premium.
# Three levers. Comparing each segment to its own line's average tells us
# which lever is broken — and the three problems have three different fixes.
print(BAR); print("A.  WHY IS EACH PROBLEM SEGMENT LOSING MONEY?"); print(BAR)

decomp = con.execute("""
WITH seg AS (
    SELECT s.*, s.earned_premium / s.earned_exposure AS avg_premium
    FROM v_segment_performance s
),
line AS (
    SELECT line_of_business,
           frequency  AS line_freq,
           severity   AS line_sev,
           earned_premium / earned_exposure AS line_avg_premium,
           loss_ratio AS line_loss_ratio
    FROM v_line_performance
)
SELECT
    seg.line_of_business, seg.industry_segment,
    ROUND(seg.earned_premium, 0)                      AS earned_premium,
    seg.claim_count,
    ROUND(seg.frequency / line.line_freq, 3)          AS freq_vs_line,
    ROUND(seg.severity  / line.line_sev,  3)          AS sev_vs_line,
    ROUND(seg.avg_premium / line.line_avg_premium, 3) AS premium_vs_line,
    ROUND(seg.loss_ratio, 3)                          AS loss_ratio,
    ROUND(seg.combined_ratio, 3)                      AS combined_ratio,
    ROUND(seg.underwriting_result, 0)                 AS uw_result,
    seg.credibility_flag
FROM seg JOIN line USING (line_of_business)
WHERE seg.combined_ratio > 1.00 AND seg.claim_count >= 30
ORDER BY seg.underwriting_result
""").df()
print(decomp.to_string(index=False))

print("""
READ THIS AS: 1.000 = exactly the line average. 1.500 = 50% worse than the line.
  freq_vs_line    > 1  -> we are writing risks that have accidents more often
  sev_vs_line     > 1  -> when they do, the claims are bigger
  premium_vs_line > 1  -> we already charge more for this class
A segment can be unprofitable on frequency, on severity, or simply because
the premium loading does not keep pace with either. The fix differs each time.
""")


# =============================================================
# B. RATE ADEQUACY
# =============================================================
# Permissible loss ratio = the most we can pay in losses and still hit target.
#     PLR = target CR - expense ratio
# Indicated rate change = current loss ratio / PLR - 1
print(BAR); print(f"B.  RATE INDICATION  (target combined ratio {TARGET_CR:.0%})"); print(BAR)

rate = con.execute(f"""
SELECT
    s.line_of_business, s.industry_segment,
    ROUND(s.earned_premium, 0)                            AS earned_premium,
    s.claim_count,
    ROUND(s.loss_ratio, 3)                                AS current_loss_ratio,
    e.total_expense_ratio                                 AS expense_ratio,
    ROUND({TARGET_CR} - e.total_expense_ratio, 3)         AS permissible_loss_ratio,
    ROUND(s.loss_ratio / ({TARGET_CR} - e.total_expense_ratio) - 1, 3)
                                                          AS indicated_rate_change,
    s.credibility,
    ROUND((s.loss_ratio / ({TARGET_CR} - e.total_expense_ratio) - 1) * s.credibility, 3)
                                                          AS credibility_weighted_rate
FROM v_segment_performance s
JOIN stg_expenses e USING (line_of_business)
WHERE s.earned_premium > 2000000
ORDER BY indicated_rate_change DESC
""").df()
print(rate.to_string(index=False))

print("""
The last column is the one to act on. A raw 40% indication built on 20 claims
is not a 40% indication — it is a small sample. Credibility weighting shrinks
every indication toward zero in proportion to how thin the data behind it is,
which is how pricing actuaries avoid overreacting to noise.
""")


# =============================================================
# C. THE COST OF TAKING RATE
# =============================================================
print(BAR); print("C.  RATE vs RETENTION TRADE-OFF — GL CONTRACTORS"); print(BAR)

curve = con.execute("""
SELECT rate_change_band,
       SUM(terms_expiring)                                   AS terms,
       ROUND(SUM(renewed) * 1.0 / SUM(terms_expiring), 3)    AS retention
FROM v_retention
WHERE rate_change_band <> 'Rate change n/a (first term)'
GROUP BY 1 ORDER BY 1
""").df()
print("Observed retention curve (whole book):")
print(curve.to_string(index=False), "\n")

base = con.execute("""
SELECT SUM(written_premium) AS renewal_premium,
       SUM(incurred_loss_alae) / SUM(earned_premium) AS loss_ratio
FROM fct_policy
WHERE line_of_business = 'General Liability'
  AND industry_segment = 'Contractors'
""").df().iloc[0]

PREMIUM, LOSS_RATIO, EXPENSE = float(base.renewal_premium), float(base.loss_ratio), 0.315

def retention(rate):
    """Fitted from the observed curve above."""
    return max(0.30, 0.885 - 1.95 * max(0.0, rate - 0.02))

# Adverse selection: the accounts that leave when you push price are the ones
# who can shop elsewhere — disproportionately the GOOD risks. Everyone who
# stays is worse than average, so the surviving book's loss ratio drifts up
# even as the rate increase spreads losses over more premium.
ADVERSE_SELECTION = 0.50

rows = []
for rate in [0.0, 0.05, 0.075, 0.10, 0.125, 0.15, 0.20]:
    ret = retention(rate)
    lost = 0.885 - ret
    retained_premium = PREMIUM * ret * (1 + rate)
    lr = LOSS_RATIO / (1 + rate) * (1 + ADVERSE_SELECTION * lost)
    cr = lr + EXPENSE
    rows.append(dict(rate_change=f"{rate:+.1%}", retention=f"{ret:.1%}",
                     retained_premium=round(retained_premium),
                     loss_ratio=round(lr, 3), combined_ratio=round(cr, 3)))

print("Modelled next renewal cycle:")
print(pd.DataFrame(rows).to_string(index=False))

print("""
There is no clean optimum here, and that is the finding. Rate alone cannot fix
this class in one cycle: even +20% leaves the combined ratio above 100% while
costing roughly half the premium. Chasing the section B indication with price
shrinks the book faster than it repairs it.

Note also that "maximise underwriting result" is a trap on a losing book — the
maths would tell you to write nothing at all. Volume has strategic value that
this model does not capture.
""")


# ---- The alternative: act on WHO we write, not just what we charge ----------
print("Same class, broken out by the underwriter's own hazard grade:\n")

hz = con.execute("""
SELECT hazard_grade,
       ROUND(earned_premium, 0) AS earned_premium, claim_count,
       ROUND(loss_ratio, 3)     AS loss_ratio,
       ROUND(combined_ratio, 3) AS combined_ratio,
       quote_to_bind_rate, recommended_action
FROM v_appetite_matrix
WHERE line_of_business = 'General Liability' AND industry_segment = 'Contractors'
ORDER BY hazard_grade
""").df()
print(hz.to_string(index=False))

g = con.execute("""
SELECT hazard_grade, SUM(earned_premium) AS ep, SUM(incurred_loss_alae) AS inc
FROM fct_policy
WHERE line_of_business = 'General Liability' AND industry_segment = 'Contractors'
GROUP BY 1
""").df().set_index("hazard_grade")

SCENARIOS = {
    "Write all grades (today)": [1, 2, 3, 4, 5],
    "Exit grade 5":             [1, 2, 3, 4],
    "Exit grades 4-5":          [1, 2, 3],
    "Exit grade 3 only":        [1, 2, 4, 5],
    "Keep grades 1-2 only":     [1, 2],
}
scen = []
for name, keep in SCENARIOS.items():
    sub = g.loc[keep]
    lr = sub.inc.sum() / sub.ep.sum()
    scen.append(dict(scenario=name,
                     premium_retained=round(sub.ep.sum()),
                     premium_given_up=round(g.ep.sum() - sub.ep.sum()),
                     loss_ratio=round(lr, 3),
                     combined_ratio=round(lr + 0.315, 3)))
print("\nPro forma scenarios (loss experience held constant):")
print(pd.DataFrame(scen).to_string(index=False))

print("""
Look at the hazard grade table again: grade 3 runs a 123.7% combined ratio and
grade 4 runs 115.8%. Grade 4 is supposed to be the WORSE risk and it performs
better. That inversion is itself a finding — either the grading criteria are
miscalibrated for this class, or grade 4 accounts are priced with enough of a
loading to compensate while grade 3 accounts are not. Either way, the fix is
not more rate across the class; it is a review of how contractors are graded.
""")


# =============================================================
# D. IS PL A SYSTEMIC PROBLEM OR A FEW BIG CLAIMS?
# =============================================================
print(BAR); print("D.  LARGE LOSS CONCENTRATION"); print(BAR)

conc = con.execute("""
WITH ranked AS (
    SELECT line_of_business, incurred_loss_alae,
           ROW_NUMBER() OVER (PARTITION BY line_of_business
                              ORDER BY incurred_loss_alae DESC) AS rn,
           SUM(incurred_loss_alae) OVER (PARTITION BY line_of_business) AS line_total,
           COUNT(*) OVER (PARTITION BY line_of_business) AS line_claims
    FROM stg_claims
)
SELECT line_of_business,
       line_claims                                              AS claims,
       ROUND(line_total, 0)                                     AS total_incurred,
       ROUND(SUM(incurred_loss_alae) FILTER (WHERE rn <= 10), 0) AS top_10_incurred,
       ROUND(SUM(incurred_loss_alae) FILTER (WHERE rn <= 10) / MAX(line_total), 3)
                                                                AS top_10_share,
       ROUND(MAX(incurred_loss_alae) FILTER (WHERE rn = 1), 0)  AS largest_claim
FROM ranked
GROUP BY line_of_business, line_claims, line_total
ORDER BY total_incurred DESC
""").df()
print(conc.to_string(index=False))

print("""
If a handful of claims drives the loss ratio, the answer is limits management
and reinsurance, not repricing the whole class. If losses are spread evenly,
the class itself is mispriced. These lead to completely different actions.
""")

con.close()
