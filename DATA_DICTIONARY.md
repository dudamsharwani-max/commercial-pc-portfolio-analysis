# Commercial P&C Portfolio — Data Dictionary

**Valuation date: 2026-06-30.** All earned figures are pro-rata as of this date.
Claims reported after this date are excluded (they are IBNR — incurred but not reported).

---

## `policies.csv` — 11,193 rows, one row per **policy term**
An account that has renewed twice appears as 3 rows sharing one `account_id`.

| Column | Meaning |
|---|---|
| `policy_id` | Unique key for the policy term |
| `account_id` | The insured business — links renewal chains together |
| `line_of_business` | BOP / General Liability / Professional Liability |
| `industry_segment` | Class of business (Contractors, Restaurants, Healthcare, etc.) |
| `state`, `producer_code` | Territory and the agency that placed the business |
| `hazard_grade` | Underwriter's risk grade, 1 (best) – 5 (worst) |
| `term_number` | 1 = first term with us, 2 = first renewal, etc. |
| `new_or_renewal` | New business vs renewal — used for the retention calc |
| `effective_date`, `expiration_date` | 12-month policy term |
| `written_premium` | Full premium booked at inception |
| `rate_change_pct` | Rate change vs the prior term (0 for new business) |
| `earned_fraction` | Portion of the term elapsed as of the valuation date |
| `earned_premium` | `written_premium × earned_fraction` |
| `unearned_premium` | The remainder — a liability, not revenue yet |
| `earned_exposure` | Earned policy-years. **The correct denominator for frequency.** |

## `claims.csv` — 1,626 rows, one row per reported claim

| Column | Meaning |
|---|---|
| `claim_id`, `policy_id` | Keys; joins back to the policy term that covered the loss |
| `loss_date` | When the event happened |
| `report_date` | When it was reported to us |
| `report_lag_days` | `report_date − loss_date`. Long-tail lines report slowly. |
| `cause_of_loss` | Peril / allegation type |
| `claim_status` | Open / Closed / Closed - No Pay |
| `paid_loss` | Cash paid to date |
| `case_reserve` | Adjuster's estimate of what's still owed (0 once closed) |
| `alae` | Allocated loss adjustment expense — defense costs tied to this claim |
| `incurred_loss` | `paid_loss + case_reserve` |
| `incurred_loss_alae` | `incurred_loss + alae` — **the number used in the loss ratio** |

## `submissions.csv` — 17,680 rows, one row per submission received

| Column | Meaning |
|---|---|
| `submission_id`, `policy_id` | `policy_id` is populated only when `outcome = 'Bound'` |
| `outcome` | Bound / Quoted - Not Bound / Lost to Competitor / Declined |
| `decline_reason` | Why we declined, or why we lost the quote |
| `quoted_premium` | Null for declines — we never priced them |
| `hazard_grade` | Grade assigned at submission — drives the appetite analysis |

## `expense_assumptions.csv` — 3 rows, one per line
Commission + other underwriting expense as a % of earned premium.
Needed for the combined ratio; a real insurer pulls this from finance, not from claims data.

---

### Known limitations (say these out loud in interviews)
1. **No IBNR reserve.** Late-reported claims are excluded, so recent accident periods look
   artificially profitable. Real actuarial work develops losses to ultimate.
2. **No reinsurance.** These are gross losses; a real book would show net of ceded.
3. **Synthetic data** generated from a documented parametric model, not a real carrier's book.
