-- ============================================================
-- Commercial P&C Portfolio — SQL metric layer (DuckDB)
-- Valuation date: 2026-06-30
--
-- Every metric definition lives here, once. Python and Streamlit
-- read these views; they never recompute a loss ratio themselves.
-- ============================================================

-- ---------- STAGING --------------------------------------------------------
CREATE OR REPLACE VIEW stg_policies AS
SELECT
    policy_id, account_id, line_of_business, industry_segment, state,
    producer_code, hazard_grade, term_number, new_or_renewal,
    CAST(effective_date  AS DATE) AS effective_date,
    CAST(expiration_date AS DATE) AS expiration_date,
    written_premium, rate_change_pct,
    earned_fraction, earned_premium, unearned_premium, earned_exposure,
    YEAR(CAST(effective_date AS DATE)) AS policy_year
FROM read_csv_auto('data/policies.csv');

CREATE OR REPLACE VIEW stg_claims AS
SELECT
    claim_id, policy_id, line_of_business, industry_segment, state,
    CAST(loss_date   AS DATE) AS loss_date,
    CAST(report_date AS DATE) AS report_date,
    report_lag_days, cause_of_loss, claim_status,
    paid_loss, case_reserve, alae, incurred_loss, incurred_loss_alae
FROM read_csv_auto('data/claims.csv');

CREATE OR REPLACE VIEW stg_submissions AS
SELECT
    submission_id, policy_id, CAST(submission_date AS DATE) AS submission_date,
    line_of_business, industry_segment, state, producer_code, hazard_grade,
    outcome, decline_reason, quoted_premium
FROM read_csv_auto('data/submissions.csv');

CREATE OR REPLACE VIEW stg_expenses AS
SELECT line_of_business, commission_ratio,
       other_underwriting_expense_ratio, total_expense_ratio
FROM read_csv_auto('data/expense_assumptions.csv');


-- ---------- FACT: one row per policy term, losses attached ----------------
-- LEFT JOIN is deliberate: ~85% of policies never have a claim, and they
-- must stay in the denominator. An INNER JOIN here would silently drop
-- every clean policy and make the whole book look catastrophic.
CREATE OR REPLACE VIEW fct_policy AS
SELECT
    p.*,
    COALESCE(c.claim_count, 0)   AS claim_count,
    COALESCE(c.incurred, 0)      AS incurred_loss_alae,
    COALESCE(c.paid, 0)          AS paid_loss,
    COALESCE(c.reserve, 0)       AS case_reserve,
    COALESCE(c.open_claims, 0)   AS open_claims
FROM stg_policies p
LEFT JOIN (
    SELECT policy_id,
           COUNT(*)                                        AS claim_count,
           SUM(incurred_loss_alae)                         AS incurred,
           SUM(paid_loss)                                  AS paid,
           SUM(case_reserve)                               AS reserve,
           COUNT(*) FILTER (WHERE claim_status = 'Open')   AS open_claims
    FROM stg_claims GROUP BY policy_id
) c USING (policy_id);


-- ---------- MACRO: the metric block, defined once -------------------------
-- Frequency uses earned_exposure (earned policy-years), NOT policy count.
-- Severity and loss ratio use incurred + ALAE.
CREATE OR REPLACE VIEW v_line_performance AS
SELECT
    f.line_of_business,
    COUNT(*)                                              AS policy_terms,
    ROUND(SUM(f.earned_exposure), 1)                      AS earned_exposure,
    ROUND(SUM(f.written_premium), 0)                      AS written_premium,
    ROUND(SUM(f.earned_premium), 0)                       AS earned_premium,
    ROUND(SUM(f.unearned_premium), 0)                     AS unearned_premium,
    SUM(f.claim_count)                                    AS claim_count,
    ROUND(SUM(f.incurred_loss_alae), 0)                   AS incurred_loss_alae,
    ROUND(SUM(f.claim_count) / SUM(f.earned_exposure), 4) AS frequency,
    ROUND(SUM(f.incurred_loss_alae)
          / NULLIF(SUM(f.claim_count), 0), 0)             AS severity,
    ROUND(SUM(f.incurred_loss_alae) / SUM(f.earned_premium), 4) AS loss_ratio,
    e.total_expense_ratio                                 AS expense_ratio,
    ROUND(SUM(f.incurred_loss_alae) / SUM(f.earned_premium)
          + e.total_expense_ratio, 4)                     AS combined_ratio,
    ROUND(SUM(f.earned_premium)
          * (1 - (SUM(f.incurred_loss_alae) / SUM(f.earned_premium)
                  + e.total_expense_ratio)), 0)           AS underwriting_result
FROM fct_policy f
JOIN stg_expenses e USING (line_of_business)
GROUP BY f.line_of_business, e.total_expense_ratio
ORDER BY earned_premium DESC;


-- ---------- SEGMENT PROFITABILITY, with a credibility guard ---------------
-- A 130% loss ratio built on 4 claims is noise. Full credibility in casualty
-- pricing is conventionally ~1,082 claims; we use the square-root rule,
-- capped at 1. Anything under ~30 claims gets flagged "Not credible" so a
-- small, volatile cell never drives a portfolio decision.
CREATE OR REPLACE VIEW v_segment_performance AS
WITH agg AS (
    SELECT
        f.line_of_business, f.industry_segment,
        COUNT(*)                     AS policy_terms,
        SUM(f.earned_exposure)       AS earned_exposure,
        SUM(f.written_premium)       AS written_premium,
        SUM(f.earned_premium)        AS earned_premium,
        SUM(f.claim_count)           AS claim_count,
        SUM(f.incurred_loss_alae)    AS incurred_loss_alae,
        e.total_expense_ratio        AS expense_ratio
    FROM fct_policy f
    JOIN stg_expenses e USING (line_of_business)
    GROUP BY 1, 2, e.total_expense_ratio
)
SELECT
    line_of_business, industry_segment, policy_terms,
    ROUND(earned_exposure, 1)                                   AS earned_exposure,
    ROUND(written_premium, 0)                                   AS written_premium,
    ROUND(earned_premium, 0)                                    AS earned_premium,
    claim_count,
    ROUND(incurred_loss_alae, 0)                                AS incurred_loss_alae,
    ROUND(claim_count / earned_exposure, 4)                     AS frequency,
    ROUND(incurred_loss_alae / NULLIF(claim_count, 0), 0)       AS severity,
    ROUND(incurred_loss_alae / earned_premium, 4)               AS loss_ratio,
    ROUND(incurred_loss_alae / earned_premium + expense_ratio, 4) AS combined_ratio,
    ROUND(LEAST(SQRT(claim_count / 1082.0), 1.0), 3)            AS credibility,
    CASE WHEN claim_count >= 100 THEN 'Credible'
         WHEN claim_count >= 30  THEN 'Partially credible'
         ELSE 'Not credible - too few claims' END               AS credibility_flag,
    ROUND(earned_premium
          * (1 - (incurred_loss_alae / earned_premium + expense_ratio)), 0)
                                                                AS underwriting_result
FROM agg
ORDER BY earned_premium DESC;


-- ---------- POLICY-YEAR TREND ---------------------------------------------
-- Grouped by the policy's effective year. Later years are immature: premium
-- is only partly earned and slow-reporting claims have not shown up yet, so
-- their loss ratios are understated. Never read the latest year as final.
CREATE OR REPLACE VIEW v_policy_year_trend AS
SELECT
    policy_year, line_of_business,
    COUNT(*)                                                    AS policy_terms,
    ROUND(SUM(written_premium), 0)                              AS written_premium,
    ROUND(SUM(earned_premium), 0)                               AS earned_premium,
    ROUND(AVG(earned_fraction), 3)                              AS avg_maturity,
    SUM(claim_count)                                            AS claim_count,
    ROUND(SUM(incurred_loss_alae), 0)                           AS incurred_loss_alae,
    ROUND(SUM(incurred_loss_alae) / SUM(earned_premium), 4)     AS loss_ratio
FROM fct_policy
GROUP BY 1, 2
ORDER BY 1, 2;


-- ---------- QUOTE-TO-BIND FUNNEL ------------------------------------------
-- Two different ratios, often confused:
--   declination rate = declined / all submissions      (appetite discipline)
--   quote-to-bind    = bound / (submissions we quoted) (price competitiveness)
CREATE OR REPLACE VIEW v_funnel AS
SELECT
    line_of_business, industry_segment, hazard_grade,
    COUNT(*)                                                AS submissions,
    COUNT(*) FILTER (WHERE outcome = 'Declined')            AS declined,
    COUNT(*) FILTER (WHERE outcome <> 'Declined')           AS quoted,
    COUNT(*) FILTER (WHERE outcome = 'Bound')               AS bound,
    ROUND(COUNT(*) FILTER (WHERE outcome = 'Declined') * 1.0
          / COUNT(*), 4)                                    AS declination_rate,
    ROUND(COUNT(*) FILTER (WHERE outcome = 'Bound') * 1.0
          / NULLIF(COUNT(*) FILTER (WHERE outcome <> 'Declined'), 0), 4)
                                                            AS quote_to_bind_rate,
    ROUND(SUM(quoted_premium) FILTER (WHERE outcome = 'Bound'), 0) AS bound_premium
FROM stg_submissions
GROUP BY 1, 2, 3
ORDER BY 1, 2, 3;


-- ---------- RETENTION ------------------------------------------------------
-- A term counts as retained if the same account has a following term.
-- Only terms that have actually reached expiration are eligible; policies
-- still in force cannot have renewed yet and would drag retention down.
CREATE OR REPLACE VIEW v_retention AS
WITH eligible AS (
    SELECT
        p.*,
        EXISTS (SELECT 1 FROM stg_policies n
                WHERE n.account_id = p.account_id
                  AND n.term_number = p.term_number + 1) AS retained
    FROM stg_policies p
    WHERE p.expiration_date <= DATE '2026-06-30'
)
SELECT
    line_of_business, industry_segment,
    CASE WHEN term_number = 1 THEN 'Rate change n/a (first term)'
         WHEN rate_change_pct < 0.00 THEN '1. Decrease'
         WHEN rate_change_pct < 0.05 THEN '2. 0-5%'
         WHEN rate_change_pct < 0.10 THEN '3. 5-10%'
         WHEN rate_change_pct < 0.15 THEN '4. 10-15%'
         ELSE '5. 15%+' END                                  AS rate_change_band,
    COUNT(*)                                                 AS terms_expiring,
    COUNT(*) FILTER (WHERE retained)                         AS renewed,
    ROUND(COUNT(*) FILTER (WHERE retained) * 1.0 / COUNT(*), 4) AS policy_retention,
    ROUND(SUM(written_premium) FILTER (WHERE retained)
          / SUM(written_premium), 4)                         AS premium_retention,
    ROUND(AVG(rate_change_pct), 4)                           AS avg_rate_change
FROM eligible
GROUP BY 1, 2, 3
ORDER BY 1, 2, 3;


-- ---------- UNDERWRITING APPETITE MATRIX ----------------------------------
-- The deliverable an underwriting manager actually acts on: for every
-- segment x hazard grade cell, what does it cost us and what should we do.
CREATE OR REPLACE VIEW v_appetite_matrix AS
WITH perf AS (
    SELECT
        f.line_of_business, f.industry_segment, f.hazard_grade,
        SUM(f.earned_premium)     AS earned_premium,
        SUM(f.claim_count)        AS claim_count,
        SUM(f.incurred_loss_alae) AS incurred,
        e.total_expense_ratio     AS expense_ratio
    FROM fct_policy f
    JOIN stg_expenses e USING (line_of_business)
    GROUP BY 1, 2, 3, e.total_expense_ratio
)
SELECT
    p.line_of_business, p.industry_segment, p.hazard_grade,
    ROUND(p.earned_premium, 0)                                   AS earned_premium,
    p.claim_count,
    ROUND(p.incurred / p.earned_premium, 4)                      AS loss_ratio,
    ROUND(p.incurred / p.earned_premium + p.expense_ratio, 4)    AS combined_ratio,
    f.submissions, f.declination_rate, f.quote_to_bind_rate,
    CASE
        WHEN p.claim_count < 30                                        THEN 'Monitor - low credibility'
        WHEN p.incurred / p.earned_premium + p.expense_ratio < 0.92    THEN 'Grow'
        WHEN p.incurred / p.earned_premium + p.expense_ratio < 1.00    THEN 'Maintain'
        WHEN p.incurred / p.earned_premium + p.expense_ratio < 1.10    THEN 'Rate action'
        ELSE 'Restrict / non-renew'
    END                                                          AS recommended_action
FROM perf p
LEFT JOIN (
    SELECT line_of_business, industry_segment, hazard_grade,
           SUM(submissions) AS submissions,
           ROUND(SUM(declined) * 1.0 / SUM(submissions), 4)  AS declination_rate,
           ROUND(SUM(bound) * 1.0 / NULLIF(SUM(quoted), 0), 4) AS quote_to_bind_rate
    FROM v_funnel GROUP BY 1, 2, 3
) f USING (line_of_business, industry_segment, hazard_grade)
ORDER BY p.earned_premium DESC;
