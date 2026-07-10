# Reconciliation Test Data Generator

## Setup & Run

```bash
pip install -r requirements.txt
python app.py
```
Open **http://localhost:5050**

## What it does

Generates 3 reconciliation test files for the **NFS ATM Issuer** use case:

| File | Format | Rows |
|------|--------|------|
| `EX3198_DDMMYYYY.prt1` | CBS fixed-width 158 chars | Variable |
| `NFS_INTERCHANGE_DDMMYYYY.txt` | NFS fixed-width 407 chars | Variable |
| `t{date}001-_SWITCH_TLF` | BASE24 TLF 574-char records | Variable |

Every run also produces `manifest_{run_id}.json` — a golden truth mapping each transaction to its scenario type and expected recon outcome.

## Reconciliation Scenarios Generated

All 14 scenarios are guaranteed to appear in every run:

1. **Fully matched** — transaction in all 3 files, consistent
2. **Switch only** — TLF has entry, NFS & CBS don't
3. **NFS only** — NFS has entry, switch & CBS don't
4. **CBS only** — CBS debited, no switch or NFS record
5. **Amount mismatch (NFS vs CBS)** — NFS amount differs from CBS debit
6. **Amount mismatch (Switch vs NFS)** — Switch amount differs from NFS
7. **Duplicate in NFS** — same RRN twice in NFS
8. **Duplicate in CBS** — same RRN debited twice in CBS
9. **Reversal complete** — original + reversal in all files, net zero
10. **Reversal partial** — switch has reversal, NFS/CBS show original only
11. **Date mismatch** — NFS settles T+1, CBS/switch show T
12. **Response code mismatch** — NFS approved, switch shows declined
13. **NFS missing, switch & CBS present**
14. **Zero amount in NFS** — data quality issue

## Project Structure

```
recon_testgen/
├── app.py                    ← Flask server (python app.py)
├── requirements.txt
├── use_cases/
│   ├── nfs_atm_issuer.yaml   ← Active use case
│   └── *.yaml                ← Placeholder (Phase 2+)
├── formats/
│   ├── cbs_ex3198.yaml
│   └── nfs_interchange.yaml
├── generators/
│   └── nfs_atm.py            ← Core generation engine
├── static/
│   └── index.html            ← Single-page UI
├── sample_files/             ← Reference sample files
└── output/                   ← Generated files land here
```

## Adding a New Use Case (Phase 2)

1. Add a YAML to `use_cases/` with `status: active`
2. Add format YAMLs to `formats/` if new file types needed
3. Add a generator module to `generators/`
4. Register in `app.py`
