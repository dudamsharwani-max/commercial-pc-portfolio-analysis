# Commercial P&C Portfolio Performance & Underwriting Analysis

Portfolio profitability analysis of a three-line commercial insurance book —
Business Owner's Policy, General Liability, and Professional Liability —
covering $137.9M written premium across 11,193 policy terms, valued 30 June 2026.

**[▶ Live dashboard](https://YOUR-APP-URL.streamlit.app)** · Python · SQL (DuckDB) · Streamlit · Plotly

---

## The question

Premium is not profit. A line of business can grow quickly and lose money the
whole way, and nothing on a revenue chart will show it. This project breaks a
commercial book into its component segments and asks which parts earn their
capital and which quietly consume it.

## Headline findings

| Line | Earned premium | Loss ratio | Combined ratio | Underwriting result |
|---|---|---|---|---|
| General Liability | $68.6M | 60.2% | 91.7% | +$5.7M |
| BOP | $22.4M | 54.3% | 88.8% | +$2.5M |
| **Professional Liability** | **$21.8M** | **80.8%** | **110.7%** | **−$2.3M** |

**1. The largest dollar loss is not the worst ratio.**
GL Contractors runs a 106.1% combined ratio — mild next to BOP Contractors at
120.9% — but it is 23% of the entire book, so it loses $1.57M against
$636K. Ranking segments by ratio buries the problem that actually costs the most.

**2. Three unprofitable segments, three different diseases.**
Decomposing loss ratio into frequency × severity ÷ average premium separates
them: Contractors has 47% higher claim frequency (a risk-selection problem),
GL Healthcare has normal frequency but 44% higher severity (a limits problem),
and BOP Restaurants is simply priced as a low-hazard class when it isn't.
Identical symptoms, opposite remedies.

**3. Professional Liability's loss ratio is a limits problem, not a pricing problem.**
Ten claims account for 55.5% of PL's incurred losses, against 11.9% for BOP.
Repricing the class would be the wrong response; excess-of-loss reinsurance and
lower per-policy limits would be the right one.

**4. Rate alone cannot fix General Liability Contractors.**
Retention falls from 88% to 58.5% once renewal increases exceed 15%, and the
accounts that leave are the ones with clean loss histories. Modelling the trade-off,
even a +20% increase leaves the combined ratio above 100% while costing 40% of
the premium.

**5. The hazard grading for Contractors is miscalibrated.**
Grade 3 runs a 123.7% combined ratio while grade 4 — nominally the worse risk —
runs 115.8%. Non-renewing grade 3 alone moves the class from 106.0% to 93.7%.
The inversion points at the grading criteria themselves rather than at pricing.

## What's here

```
generate_data.py      synthetic book generator (documented parametric model)
sql/metrics.sql       every metric definition, as DuckDB views
build_db.py           builds the warehouse from CSVs + views
analysis.py           rate indications, retention modelling, loss concentration
app.py                Streamlit dashboard
DATA_DICTIONARY.md    field definitions and stated limitations
```

## Design decisions

**Frequency uses earned exposure, not policy count.** A policy written last
month has been exposed to loss for one month, not a year. Using policy count
understates frequency by ~21% across every line here, and understates it most
where the book is growing fastest — precisely where you least want to be wrong.
The same logic makes earned premium, not written, the loss-ratio denominator.

**Rate indications are credibility-weighted.** The raw indication for GL
Technology is +77.7%, built on ten claims. Weighting each indication by
`min(√(n/1082), 1)` — the square-root rule against the standard casualty full-
credibility threshold — shrinks thin estimates toward zero and reorders the
priority list entirely.

**No metric logic lives in the dashboard.** Every figure the app renders comes
from a view in `sql/metrics.sql`. The app formats and charts; it never redefines
a ratio. One definition, one place to change it.

**Immature policy years are flagged, not smoothed.** Recent years are only
partly earned and slow-reporting claims have not arrived (PL reports at a ~210
day average lag). The trend chart carries a warning rather than presenting an
artificial improvement as real.

## Limitations

Losses are gross of reinsurance and **not developed to ultimate** — no IBNR
provision, so recent periods understate the loss ratio. The data is synthetic,
generated from the documented model in `generate_data.py`, not a carrier's
actual book.

## Running it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python build_db.py      # build the warehouse
python analysis.py      # print the analysis
streamlit run app.py    # launch the dashboard
```
