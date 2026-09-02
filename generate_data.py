"""
Commercial P&C Portfolio - Synthetic Data Generator
---------------------------------------------------
Builds a realistic 3-line commercial insurance book (BOP, GL, PL)
as of valuation date 2026-06-30, covering policies effective 2023-01-01 onward.

Outputs 4 CSVs: policies, claims, submissions, expense_assumptions
"""

import numpy as np
import pandas as pd
from datetime import date, timedelta

rng = np.random.default_rng(42)

VALUATION_DATE = pd.Timestamp("2026-06-30")
START_DATE = pd.Timestamp("2023-01-01")

LOBS = ["BOP", "General Liability", "Professional Liability"]

SEGMENTS = [
    "Contractors", "Restaurants & Food Service", "Retail", "Professional Services",
    "Healthcare Services", "Technology", "Real Estate", "Light Manufacturing",
]

STATES = ["IL", "TX", "CA", "FL", "NY", "GA", "AZ", "OH", "NC", "WA"]
STATE_W = [.16, .14, .12, .11, .09, .08, .07, .08, .08, .07]

# ---- underwriting profile: (avg premium, base freq per policy-yr, severity mu/sigma, report-lag days)
LOB_PROFILE = {
    "BOP":                    dict(prem=4800,  sigma=0.55, freq=0.20, sev_mu=9.00, sev_sd=1.05, lag=30,  exp_ratio=0.345),
    "General Liability":      dict(prem=13500, sigma=0.70, freq=0.18, sev_mu=10.06, sev_sd=1.25, lag=95,  exp_ratio=0.315),
    "Professional Liability": dict(prem=7000,  sigma=0.75, freq=0.10, sev_mu=10.42, sev_sd=1.40, lag=210, exp_ratio=0.300),
}

# segment risk multipliers: (premium mult, frequency mult, severity mult)
SEG_RISK = {
    "Contractors":                (1.45, 1.75, 1.30),
    "Restaurants & Food Service": (0.80, 1.35, 0.85),
    "Retail":                     (0.85, 1.00, 0.85),
    "Professional Services":      (0.95, 0.60, 1.05),
    "Healthcare Services":        (1.30, 1.10, 1.45),
    "Technology":                 (1.10, 0.55, 1.20),
    "Real Estate":                (1.00, 0.85, 1.00),
    "Light Manufacturing":        (1.25, 1.05, 1.15),
}

# which segments each line actually writes (with weights)
LOB_SEG_W = {
    "BOP":                    dict(zip(SEGMENTS, [.10, .22, .24, .08, .04, .04, .16, .12])),
    "General Liability":      dict(zip(SEGMENTS, [.30, .14, .12, .06, .06, .04, .12, .16])),
    "Professional Liability": dict(zip(SEGMENTS, [.02, .01, .02, .36, .29, .23, .04, .03])),
}

CAUSES = {
    "BOP": ["Water Damage", "Fire", "Theft/Burglary", "Wind/Hail", "Slip & Fall", "Business Interruption"],
    "General Liability": ["Slip & Fall", "Property Damage - 3rd Party", "Products Liability",
                          "Completed Operations", "Personal & Advertising Injury"],
    "Professional Liability": ["Negligent Act", "Failure to Deliver", "Breach of Duty",
                               "Misrepresentation", "Regulatory Proceeding"],
}

N_ACCOUNTS = 5200


# ============================================================
# 1. ACCOUNTS + POLICY TERMS (with renewal chains)
# ============================================================
def build_policies():
    rows = []
    pol_seq = 1

    for acct in range(1, N_ACCOUNTS + 1):
        acct_id = f"ACCT-{acct:05d}"
        lob = rng.choice(LOBS, p=[0.42, 0.35, 0.23])
        segs = list(LOB_SEG_W[lob].keys())
        w = np.array(list(LOB_SEG_W[lob].values()), dtype=float)
        seg = rng.choice(segs, p=w / w.sum())
        state = rng.choice(STATES, p=STATE_W)
        producer = f"AGY-{rng.integers(1, 61):03d}"

        prof = LOB_PROFILE[lob]
        pmult, _, _ = SEG_RISK[seg]

        # first term effective date, skewed so the book builds up over time
        offset = int(rng.integers(0, 1096))          # 2023-01-01 .. 2025-12-31
        eff = START_DATE + timedelta(days=offset)

        base_prem = float(rng.lognormal(np.log(prof["prem"] * pmult), prof["sigma"]))
        base_prem = float(np.clip(base_prem, 750, 400_000))

        hazard = int(np.clip(rng.normal(3 if SEG_RISK[seg][1] > 1.2 else 2.2, 0.9), 1, 5).round())

        term_no = 0
        alive = True
        while alive and eff <= VALUATION_DATE:
            exp = eff + pd.DateOffset(years=1)

            # rate change on renewal: harder market on loss-heavy segments
            if term_no == 0:
                rate_change = 0.0
                prem = base_prem
            else:
                rate_change = float(rng.normal(0.075 if SEG_RISK[seg][1] > 1.2 else 0.045, 0.06))
                prem = prem * (1 + rate_change)

            rows.append(dict(
                policy_id=f"POL-{pol_seq:06d}",
                account_id=acct_id,
                line_of_business=lob,
                industry_segment=seg,
                state=state,
                producer_code=producer,
                hazard_grade=hazard,
                term_number=term_no + 1,
                new_or_renewal="New" if term_no == 0 else "Renewal",
                effective_date=eff,
                expiration_date=exp,
                written_premium=round(prem, 2),
                rate_change_pct=round(rate_change, 4),
            ))
            pol_seq += 1
            term_no += 1

            # retention: worse when we push rate hard or the segment is troubled
            base_ret = 0.855
            base_ret -= max(0.0, rate_change - 0.05) * 1.8      # price-driven churn
            base_ret += 0.03 if lob == "Professional Liability" else 0.0
            base_ret -= 0.04 if SEG_RISK[seg][1] > 1.5 else 0.0  # we non-renew bad risk
            alive = rng.random() < np.clip(base_ret, 0.55, 0.94)
            eff = exp

    return pd.DataFrame(rows)


# ============================================================
# 2. EARNED PREMIUM (pro-rata as of valuation date)
# ============================================================
def add_earned_premium(pol):
    term_days = (pol.expiration_date - pol.effective_date).dt.days
    elapsed = (VALUATION_DATE - pol.effective_date).dt.days.clip(lower=0)
    earned_frac = (elapsed / term_days).clip(0, 1)
    pol["earned_fraction"] = earned_frac.round(4)
    pol["earned_premium"] = (pol.written_premium * earned_frac).round(2)
    pol["unearned_premium"] = (pol.written_premium - pol.earned_premium).round(2)
    # exposure in earned policy-years - the correct denominator for frequency
    pol["earned_exposure"] = earned_frac.round(4)
    return pol


# ============================================================
# 3. CLAIMS
# ============================================================
def build_claims(pol):
    rows = []
    seq = 1
    for r in pol.itertuples():
        prof = LOB_PROFILE[r.line_of_business]
        _, fmult, smult = SEG_RISK[r.industry_segment]

        hazard_load = 1 + (r.hazard_grade - 2.5) * 0.14
        lam = prof["freq"] * fmult * hazard_load * r.earned_exposure
        n = rng.poisson(max(lam, 0))

        for _ in range(int(n)):
            term_days = (r.expiration_date - r.effective_date).days
            loss_date = r.effective_date + timedelta(days=int(rng.uniform(0, term_days * r.earned_fraction)))
            lag = int(rng.exponential(prof["lag"]))
            report_date = loss_date + timedelta(days=lag)
            if report_date > VALUATION_DATE:
                continue  # still IBNR - not in our data yet

            sev = float(rng.lognormal(prof["sev_mu"] + np.log(smult), prof["sev_sd"]))
            sev = float(np.clip(sev, 250, 2_500_000))

            age = (VALUATION_DATE - report_date).days
            close_speed = 240 if r.line_of_business != "Professional Liability" else 620
            closed = rng.random() < (age / (age + close_speed))

            if closed:
                status, paid, reserve = "Closed", sev, 0.0
                if rng.random() < 0.12:          # closed without payment
                    status, paid, sev = "Closed - No Pay", 0.0, 0.0
            else:
                status = "Open"
                paid = sev * float(rng.uniform(0.05, 0.55))
                reserve = sev - paid

            alae = sev * float(rng.uniform(0.04, 0.22))

            rows.append(dict(
                claim_id=f"CLM-{seq:06d}",
                policy_id=r.policy_id,
                line_of_business=r.line_of_business,
                industry_segment=r.industry_segment,
                state=r.state,
                loss_date=pd.Timestamp(loss_date),
                report_date=pd.Timestamp(report_date),
                report_lag_days=lag,
                cause_of_loss=rng.choice(CAUSES[r.line_of_business]),
                claim_status=status,
                paid_loss=round(paid, 2),
                case_reserve=round(reserve, 2),
                alae=round(alae, 2),
            ))
            seq += 1

    df = pd.DataFrame(rows)
    df["incurred_loss"] = (df.paid_loss + df.case_reserve).round(2)
    df["incurred_loss_alae"] = (df.incurred_loss + df.alae).round(2)
    return df


# ============================================================
# 4. SUBMISSIONS / QUOTE FUNNEL
# ============================================================
def build_submissions(pol):
    """Every New policy came from a bound submission; add the ones we lost/declined."""
    new_biz = pol[pol.new_or_renewal == "New"]
    rows = []
    seq = 1
    for r in new_biz.itertuples():
        rows.append(dict(
            submission_id=f"SUB-{seq:06d}", policy_id=r.policy_id,
            submission_date=r.effective_date - timedelta(days=int(rng.integers(5, 40))),
            line_of_business=r.line_of_business, industry_segment=r.industry_segment,
            state=r.state, producer_code=r.producer_code, hazard_grade=r.hazard_grade,
            outcome="Bound", decline_reason=None,
            quoted_premium=r.written_premium,
        ))
        seq += 1

    # unbound submissions: ~3.4 submissions per bound policy overall
    n_lost = int(len(new_biz) * 2.4)
    for _ in range(n_lost):
        lob = rng.choice(LOBS, p=[0.42, 0.35, 0.23])
        segs = list(LOB_SEG_W[lob].keys())
        w = np.array(list(LOB_SEG_W[lob].values()), dtype=float)
        seg = rng.choice(segs, p=w / w.sum())
        hazard = int(np.clip(rng.normal(3.2 if SEG_RISK[seg][1] > 1.2 else 2.4, 1.0), 1, 5).round())

        # high-hazard risk is far likelier to be declined outright (appetite)
        p_decline = np.clip(0.10 + 0.16 * (hazard - 1), 0.05, 0.80)
        if rng.random() < p_decline:
            outcome = "Declined"
            reason = rng.choice(["Outside Appetite", "Loss History", "Class Ineligible",
                                 "Insufficient Controls", "Capacity"], p=[.34, .26, .18, .12, .10])
            quoted = np.nan
        else:
            outcome = rng.choice(["Quoted - Not Bound", "Lost to Competitor"], p=[0.42, 0.58])
            reason = rng.choice(["Price", "Coverage Terms", "Incumbent Retained", "No Response"],
                                p=[.48, .17, .21, .14])
            prof = LOB_PROFILE[lob]
            quoted = round(float(np.clip(rng.lognormal(np.log(prof["prem"] * SEG_RISK[seg][0]),
                                                       prof["sigma"]), 750, 400_000)), 2)

        rows.append(dict(
            submission_id=f"SUB-{seq:06d}", policy_id=None,
            submission_date=START_DATE + timedelta(days=int(rng.integers(0, 1275))),
            line_of_business=lob, industry_segment=seg,
            state=rng.choice(STATES, p=STATE_W), producer_code=f"AGY-{rng.integers(1, 61):03d}",
            hazard_grade=hazard, outcome=outcome, decline_reason=reason, quoted_premium=quoted,
        ))
        seq += 1

    return pd.DataFrame(rows).sort_values("submission_date").reset_index(drop=True)


# ============================================================
if __name__ == "__main__":
    pol = add_earned_premium(build_policies())
    clm = build_claims(pol)
    sub = build_submissions(pol)

    exp = pd.DataFrame([
        dict(line_of_business=k,
             commission_ratio=round(v["exp_ratio"] * 0.52, 4),
             other_underwriting_expense_ratio=round(v["exp_ratio"] * 0.48, 4),
             total_expense_ratio=v["exp_ratio"])
        for k, v in LOB_PROFILE.items()
    ])

    out = "/home/claude/pc_portfolio/data/"
    pol.to_csv(out + "policies.csv", index=False)
    clm.to_csv(out + "claims.csv", index=False)
    sub.to_csv(out + "submissions.csv", index=False)
    exp.to_csv(out + "expense_assumptions.csv", index=False)

    print(f"policies    : {len(pol):>7,}  written ${pol.written_premium.sum():,.0f}  earned ${pol.earned_premium.sum():,.0f}")
    print(f"claims      : {len(clm):>7,}  incurred ${clm.incurred_loss_alae.sum():,.0f}")
    print(f"submissions : {len(sub):>7,}")
    print(f"\nPortfolio loss ratio: {clm.incurred_loss_alae.sum() / pol.earned_premium.sum():.1%}")
