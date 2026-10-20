# Catalyst fixtures (Phase 05) — ALL FICTIONAL

Every file in this directory is **synthetic test data** written for APEX. The issuers, people,
funds, politicians, outlets, headlines, amounts and dates are invented. Nothing here is copied from a
real filing, article, disclosure or data release, and nothing here may be shown as a real event.

Conventions:

- Demo tickers use the `DM...` prefix (`DMSEMI1`, `DMBANK2`, `DMLOG3`, `DMBIO4`, `DMNEWNM`, `DMRX`).
- Demo CIKs are in the `00099900xx` / `00099901xx` / `00099902xx` ranges, which are not assigned to the
  companies named here.
- News outlets and IR sites use the reserved `.example` TLD and never resolve.
- Congressional rows carry obviously fictional member names. Their `source_url`s use the official
  hosts (`efdsearch.senate.gov`, `disclosures-clerk.house.gov`) only so the source-classification logic
  can be tested; the paths are invented and do not point at real reports.
- Macro release names are fictional; dates are illustrative only, not the official schedule. The
  `source_url`s point at the public schedule pages an operator would check.

The structures follow the real public formats so the parsers are tested against the right shape:

| File | Real-world format mirrored |
|---|---|
| `edgar_submissions_CIK0009990001.json`, `edgar_submissions_CIK0009990010.json` | `https://data.sec.gov/submissions/CIK##########.json` (`filings.recent` column arrays) |
| `edgar_8k_items.json` | 8-K rows with item codes, as listed in submissions JSON |
| `company_tickers.json` | `https://www.sec.gov/files/company_tickers.json` |
| `ticker_aliases.json` | APEX alias table: ticker → CIK over date ranges (ticker change + ticker reuse) |
| `form4_*.xml` | SEC Form 4 `ownershipDocument` XML (open-market buy, 10b5-1 sale, award A, exercise M + withholding F, multi-transaction) |
| `quarantine_weird_html_in_xml.xml` | malformed HTML interstitial served instead of XML (must be quarantined) |
| `news_rss_syndicated.xml` | RSS 2.0 (one story syndicated by 5 outlets, an ambiguous-entity headline, unsafe links) |
| `news_atom.xml` | Atom 1.0 |
| `news_allowlist.json`, `entity_aliases.json` | APEX feed allowlist and entity dictionary |
| `congress_manual.csv` | APEX manual-import CSV for periodic transaction reports |
| `alfred_vintages_DMCPI.json`, `fred_current_only_DMCPI.json` | FRED/ALFRED `series/observations` JSON with and without vintages |
| `macro_release_calendar.json` | APEX curated release calendar |
| `earnings_manual.json` | APEX curated earnings entries (confirmed / expected / reported) |
| `labels.json` | hand-labelled sample used to measure classifier precision |

EDGAR note: the submissions API prints `acceptanceDateTime` with a trailing `Z`, but the wall-clock
value is US Eastern; the fixtures follow that convention, including timestamps on both sides of the
2025-11-02 and 2026-03-08 DST changes.
