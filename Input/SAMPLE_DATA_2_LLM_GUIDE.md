# Sample Data 2 LLM Guide

This file is meant to be passed to an LLM that must answer questions about `Input/Sample_Data 2.xlsx`, generate SQL against the local DuckDB tables, and explain why specific business outcomes happened.

Use this document as the business-semantic layer. The auto-generated metadata in `backend/metadata_catalog.json` is useful for schema discovery, but most descriptions there are placeholders and do not capture the real business meaning.

Treat this guide as instructions, not as an answer key. Use it to understand table grain, coded values, identifier handling, safe joins, query planning, and evidence standards. Do not assume a specific provider, account, territory, root cause, or recommended response until SQL confirms it from the workbook.

## 1. Current Runtime Behavior

This repo is a small full-stack chatbot for Gilead field users.

- Frontend: Next.js app in `Gilead-POC-FE-main/Gilead-POC-FE-main`
- Backend: FastAPI app in `backend/src`
- Analytics store: in-memory DuckDB
- Data source: every non-JSON file in `Input/` is loaded automatically into DuckDB
- Current LLM usage:
  - guide-driven SQL planning against DuckDB tables
  - multi-query execution for root-cause and evidence-gathering questions
  - final answer synthesis from only the user question plus SQL results

### Request flow

1. Frontend sends `POST /query?question=...&session_id=...`.
2. Backend entrypoint is `backend/src/main.py`.
3. `backend/src/responder.py` handles identifier follow-ups, DCR confirmations, and final answer synthesis.
4. `backend/src/query_engine.py` calls the SQL planner and executor.
5. `backend/src/database.py` loads workbook sheets into DuckDB tables.
6. `backend/src/sql_agent.py` plans and executes SQL in this order:
   - heuristic multi-query plans for common root-cause cases
   - Azure OpenAI planner for 1-4 focused SQL queries
   - lazy LangChain fallback only if needed
7. `backend/src/sql_validator.py` enforces read-only guardrails.
8. The final answer LLM receives only:
   - the user question
   - SQL execution results

### Important code behavior

- Sheet names are converted into lowercase snake-case table names.
- JSON files in `Input/` are not loaded as tables.
- The static guide in `Input/SAMPLE_DATA_2_LLM_GUIDE.md` is injected into the SQL planner context.
- `Input/GILEAD_Field_Inquiry_Playbook.json` may still exist in the repo, but its narrative content is not injected into runtime prompts anymore.
- Metadata relationships are inferred by value overlap, not curated by business rules.
- Session memory can carry IDs from previous answers into later SQL.
- Session-memory reference detection was tightened to avoid false positives on ordinary words like `that`.
- The frontend is thin. The real business logic is in the backend.

### SQL guardrails

The SQL agent is constrained by `backend/src/sql_validator.py`:

- explicit column list only
- `LIMIT` is mandatory
- maximum `LIMIT` is 100
- read-only queries only
- use only known tables and columns
- multi-query execution is allowed, but each individual SQL statement must satisfy the same guardrails

## 2. Exact Workbook-to-Table Mapping

`Sample_Data 2.xlsx` becomes these DuckDB tables:

| Excel sheet | DuckDB table | Data rows | Notes |
| --- | --- | ---: | --- |
| `OneKey HCP` | `onekey_hcp` | 5 | Main HCP master data |
| `Alignement_File` | `alignement_file` | 4 | Misspelled "alignment" in source; keep exact table name |
| `SHip_to_867` | `ship_to_867` | 4 | Ship-to shipment and territory mapping data |
| `Speciality` | `speciality` | 7 | Misspelled "specialty" in source; keep exact table name |
| `Exponent_NonRetail` | `exponent_nonretail` | 4 | Non-retail outlet-level volume |
| `DDD_REtial` | `ddd_retial` | 5 | Misspelled "retail" in source; keep exact table name |
| `Affiliation_OneKey` | `affiliation_onekey` | 5 | HCP-to-HCO affiliation bridge |
| `DCR Logs` | `dcr_logs` | 3 | Data correction and territory event log |

These exact misspellings matter:

- `alignement_file`
- `speciality`
- `ddd_retial`

Do not "correct" them in SQL.

## 3. Core Business Concepts

Use these meanings consistently:

- `HCP`: healthcare provider / prescriber / physician
- `HCO`: healthcare organization / account / clinic / institution
- `NPI`: provider identifier; an HCP may have multiple OneKey records over time but still share one NPI
- `OneKey`: the master data system for HCP/HCO records
- `Alignment File`: current assignment of HCPs to territories
- `DDD retail`: retail prescriber-level dispensed prescription data, keyed by `NPI`
- `Exponent non-retail`: outlet-level or account-level volume, keyed by `HCO_ID`
- `867`: shipment data tied to ship-to and territory mapping
- `DCR`: data correction request / governance event log
- `Credit flag`: whether a record is eligible for incentive credit
- `Creditable specialty`: specialty eligible for incentive credit
- `Retail vs non-retail`:
  - retail usually appears at prescriber or NPI grain in `ddd_retial`
  - non-retail usually appears at outlet or HCO grain in `exponent_nonretail`

## 4. Identifier Discipline And Coded Values

This section is critical. Many bad answers come from mixing names with IDs or treating short codes as self-explanatory.

### Identifier rules

- `RECORD_ID` and `HCP_ID` are OneKey HCP record IDs. In this workbook they look like `HCP-####`.
- `HCO_ID` is the organization or account identifier. In this workbook it looks like `HCO-####`.
- `TERRITORY_ID` is a territory code such as `NYC-22` or `DAL-19`.
- `SHIP_TO_ID` is a ship-to location ID such as `SP-####`.
- `NPI` is numeric and identifies the provider, but it is not unique inside `onekey_hcp`.
- `HCP_NAME`, `HCO_NAME`, `ADDRESS`, `CITY`, and similar text fields are descriptive attributes, not stable join keys.

### Identifier handling rules

- If the user gives an HCO name, street, city, or clinic description, first resolve the matching `HCO_ID` from `onekey_hcp`.
- If the user gives a provider name, first resolve `RECORD_ID` and `NPI` from `onekey_hcp`.
- If the user gives a territory string, use the exact `TERRITORY_ID` code when querying alignment or ship-to data.
- Do not compare text names to ID columns.
- Do not query fact tables with `HCO_NAME` values inside `HCO_ID` filters.

Example:

```sql
-- Wrong: compares an account name to an ID column
SELECT
  e.HCO_ID,
  e.PRODUCT,
  e.MONTH,
  e.TRX_VOLUME
FROM exponent_nonretail e
WHERE e.HCO_ID = '{{ACCOUNT_NAME}}'
LIMIT 100;
```

```sql
-- Right: resolve HCO_ID first, then query the fact table
WITH hco_lookup AS (
  SELECT DISTINCT
    HCO_ID,
    HCO_NAME,
    ADDRESS,
    CITY,
    STATE
  FROM onekey_hcp
  WHERE UPPER(HCO_NAME) LIKE UPPER('%{{ACCOUNT_NAME}}%')
     OR UPPER(ADDRESS) LIKE UPPER('%{{ADDRESS_FRAGMENT}}%')
)
SELECT
  e.HCO_ID,
  e.PRODUCT,
  e.MONTH,
  e.TRX_VOLUME
FROM exponent_nonretail e
WHERE e.HCO_ID IN (SELECT HCO_ID FROM hco_lookup)
LIMIT 100;
```

### Coded value dictionary

Use these meanings when translating the data into business language.

#### `onekey_hcp.RECORD_STATUS`

- `Active`: current surviving or active HCP record
- `Retired`: historical or superseded record; may still be relevant for lineage and DCR reasoning

#### `onekey_hcp.CREDIT_FLAG`

- `Y`: record is marked credit-eligible
- `N`: record is not marked credit-eligible
- Use this as a record-level eligibility flag, not as proof that credit was actually paid or assigned in a dashboard.

#### `onekey_hcp.RETAIL_FLAG`

- `Y`: retail-oriented hint; prescriber-level DDD may be relevant
- `N`: non-retail or not primarily retail-oriented hint; account-level sources may be more relevant
- Treat this as a hint, not as the sole source of truth.

#### `onekey_hcp.REPORTING_STATUS`

- `Reporting`: included in reporting scope
- The current workbook only contains `Reporting`, but if another value appears in future data, treat it as potentially out of reporting scope until proven otherwise.

#### `speciality.CREDITABLE_FLAG`

- `Y`: specialty is credit-eligible
- `N`: specialty is not credit-eligible
- This is specialty-level logic and should be combined with `onekey_hcp.CREDIT_FLAG` for provider-level credit reasoning.

#### `onekey_hcp.ACCOUNT_TYPE`

These are descriptive business categories, not guaranteed fact-table labels.

- `Specialty HIV Clinic`: specialty-focused HIV clinic or account type
- `Hospital Outpatient`: hospital-affiliated outpatient setting
- `Private Clinic`: office or clinic-style practice setting

Use `ACCOUNT_TYPE` as context only. Final retail or non-retail reasoning must still come from the actual volume tables.

#### `affiliation_onekey.AFFILIATION_TYPE`

These values describe the relationship of the HCP to the HCO.

- `Owner Physician`: owner or owning physician relationship
- `Attending Physician`: attending or treating physician relationship
- `Staff Physician`: staff-level physician relationship

This field describes relationship role. It does not allocate HCO-level volume and does not override the provider's current primary HCO in `onekey_hcp`.

#### `dcr_logs.REQUEST_TYPE`

- `HCP Merge`: HCP record merge event
- `Territory Realignment`: territory mapping changed
- `Alignment Exception`: exception or correction request against a territory assignment

#### `dcr_logs.ENTITY`

- `HCP`: the governance event is about an HCP record or provider lineage

#### Time and metric fields

- `MONTH` in `ship_to_867`, `exponent_nonretail`, and `ddd_retial` is stored as `YYYY-MM` text, not a full date.
- `NBRX`: new-to-brand prescriptions
- `TRX`: total retail prescriptions
- `TRX_VOLUME`: non-retail transaction volume at outlet or HCO grain
- `UNITS_SHIPPED`: shipment units, not the same thing as prescriptions

## 5. Table-By-Table Semantic Dictionary

## `onekey_hcp`

### Grain

One row per HCP record in OneKey, not one row per unique NPI.

### Purpose

Primary HCP dimension for provider identity, record status, specialty, HCO association, location, and crediting flags.

### Safe primary key

- `RECORD_ID`

### Important alternate key

- `NPI`

Do not assume `NPI` is unique inside `onekey_hcp`.

### Columns

- `RECORD_ID`: unique OneKey HCP record ID
- `NPI`: provider NPI; can appear on multiple OneKey records over time
- `HCP_NAME`: provider display name
- `SPECIALTY`: specialty description stored on the HCP record
- `SPECIALTY_CODE`: safest join key into `speciality`
- `RECORD_STATUS`: operational status of this OneKey record
- `MASTER_HCP_ID`: if populated, this row was merged into the surviving master HCP record
- `CREDIT_FLAG`: whether the HCP record is marked credit-eligible
- `HCO_ID`: current primary organization or account on this HCP record
- `HCO_NAME`: current HCO name on this HCP record
- `ADDRESS`, `CITY`, `STATE`, `ZIP`: current location on this record
- `ACCOUNT_TYPE`: descriptive account category attached to the HCP's primary HCO
- `RETAIL_FLAG`: retail-oriented hint
- `REPORTING_STATUS`: whether the record is in reporting scope
- `RECORD_CREATION_DATE`: when this OneKey record version was created
- `RECORD_EXPIRATION_DATE`: when this record stopped being the valid record

### Workbook-relevant pattern

- The workbook contains at least one provider lineage where multiple `RECORD_ID` values share the same `NPI`.
- In that lineage, one row is retired and points to a surviving master record through `MASTER_HCP_ID`.
- This makes `onekey_hcp` the starting point for merge-aware reasoning.

### Query caution

If you join `ddd_retial` to raw `onekey_hcp` on `NPI` without deduping or filtering to the current record, retail scripts for a duplicated NPI can join to both historical and surviving records and get double-counted.

## `alignement_file`

### Grain

One row per aligned HCP record or NPI or effective-date snapshot.

### Purpose

Maps HCPs into territories.

### Safe join keys

- `HCP_ID` -> `onekey_hcp.RECORD_ID`
- `NPI` -> `onekey_hcp.NPI` only after careful dedupe

### Columns

- `HCP_ID`: aligned HCP record ID
- `NPI`: provider NPI
- `TERRITORY_ID`: assigned territory
- `ZIP`: intended ZIP reference, but corrupted in this workbook
- `EFFECTIVE_DATE`: date the territory assignment became effective

### Query caution

- Do not use `alignement_file.ZIP` as a real ZIP code in this workbook.
- Excel converted ZIP-like values into dates because the column name contains `ZIP` but the loader also parses `date` columns elsewhere.
- Use `onekey_hcp.ZIP` for real ZIP context in this workbook.
- Use `alignement_file.TERRITORY_ID` and `EFFECTIVE_DATE` for alignment status.
- This table may show the current alignment state without preserving the prior territory assignment the user expects.

## `ship_to_867`

### Grain

One row per `SHIP_TO_ID` + `HCO_ID` + `PRODUCT` + `MONTH`.

### Purpose

Shipment or outlet territory mapping and shipment confirmation.

### Columns

- `SHIP_TO_ID`: ship-to location ID
- `HCO_ID`: account or organization ID
- `PRODUCT`: product name
- `MONTH`: monthly period in `YYYY-MM` text format
- `UNITS_SHIPPED`: shipped units
- `TERRITORY_ID`: territory receiving the ship-to mapping
- `DISTRICT`: district rollup
- `REGION`: regional rollup

### Best use

- validate shipment presence
- validate HCO-level territory mapping
- support non-retail account reasoning

### Query caution

`ship_to_867` reflects shipment flow, not prescriber attribution. Use it to validate ship-to mapping and supply presence, not to replace DDD or Exponent.

## `speciality`

### Grain

One row per specialty code.

### Purpose

Lookup table for specialty meaning and creditability.

### Columns

- `SPECIALTY_CODE`: specialty code
- `SPECIALTY_NAME`: readable specialty name
- `THERAPY_AREA`: therapy area
- `CREDITABLE_FLAG`: specialty-level credit eligibility flag

### Best use

- decode specialty codes into business labels
- determine whether the specialty is credit-eligible
- support provider-level credit reasoning together with `onekey_hcp.CREDIT_FLAG`

## `exponent_nonretail`

### Grain

One row per `OUTLET_ID` + `HCO_ID` + `PRODUCT` + `MONTH`.

### Purpose

Non-retail outlet-level volume.

### Columns

- `OUTLET_ID`: non-retail outlet identifier
- `HCO_ID`: account or organization
- `PRODUCT`: product name
- `MONTH`: monthly period in `YYYY-MM`
- `TRX_VOLUME`: outlet-level transaction volume

### Best use

- answer non-retail account questions
- measure HCO-level volume over time
- compare against `ship_to_867` at account and month grain

### Query caution

This is HCO or outlet grain, not HCP grain. Do not attribute these units to each affiliated physician unless you have a valid allocation rule. The sample workbook does not provide such an allocation rule.

## `ddd_retial`

### Grain

One row per `NPI` + `PRODUCT` + `MONTH`.

### Purpose

Retail prescriber-level prescription volume.

### Columns

- `NPI`: provider NPI
- `PRODUCT`: product name
- `MONTH`: monthly period in `YYYY-MM`
- `NBRX`: new-to-brand prescriptions
- `TRX`: total prescriptions

### Best use

- answer provider-level retail questions
- show whether prescribing continued across a change window
- compare activity before and after a merge or alignment event

### Query caution

- `ddd_retial` is prescriber grain, not HCO grain.
- Do not infer absence of HCO-level volume from absence of DDD rows.
- Continued DDD activity after a record-state change can indicate an attribution problem rather than a demand drop.

## `affiliation_onekey`

### Grain

One row per HCP-to-HCO affiliation.

### Purpose

Bridge table for affiliated prescribers at each account.

### Columns

- `AFFILIATION_ID`: affiliation row ID
- `HCP_ID`: HCP record ID
- `HCO_ID`: affiliated HCO
- `AFFILIATION_TYPE`: relationship type

### Best use

- list affiliated HCPs for an HCO
- find which providers are tied to an account
- support a retail versus non-retail investigation by enumerating related providers

### Query caution

Affiliation records can disagree with the current primary HCO in `onekey_hcp`. Do not assume all cross-table HCO links are perfectly current.

## `dcr_logs`

### Grain

One row per DCR or governance event.

### Purpose

Explains merges, realignments, and exception requests.

### Columns

- `DCR_ID`: event identifier
- `REQUEST_TYPE`: event type
- `ENTITY`: entity domain
- `OLD_HCP_ID`: affected HCP or source record
- `MASTER_HCP_ID`: surviving master record for merges
- `DESCRIPTION`: free-text explanation
- `DATE`: event date

### Best use

This is the strongest root-cause table for "why did this happen?" questions.

### Query caution

`DESCRIPTION` often contains the business explanation that does not exist anywhere else in structured columns. Always inspect it for root-cause questions.

## 6. Canonical Join Paths And Resolution Flow

Prefer these join paths.

### Resolve names to IDs first

If the user gives free-text names or addresses, resolve IDs before touching fact tables.

```sql
SELECT DISTINCT
  HCO_ID,
  HCO_NAME,
  ADDRESS,
  CITY,
  STATE
FROM onekey_hcp
WHERE UPPER(HCO_NAME) LIKE UPPER('%{{ACCOUNT_NAME}}%')
   OR UPPER(ADDRESS) LIKE UPPER('%{{ADDRESS_FRAGMENT}}%')
LIMIT 100;
```

### Provider identity and specialty

```sql
onekey_hcp.SPECIALTY_CODE = speciality.SPECIALTY_CODE
```

### Current territory by HCP record

```sql
onekey_hcp.RECORD_ID = alignement_file.HCP_ID
```

This is safer than joining on `NPI` when duplicate OneKey records exist.

### Retail scripts by provider

```sql
current_hcp.NPI = ddd_retial.NPI
```

Where `current_hcp` is a deduped HCP dimension, not raw `onekey_hcp`.

### Affiliated prescribers at an HCO

```sql
affiliation_onekey.HCP_ID = onekey_hcp.RECORD_ID
```

### Non-retail outlet volume to shipment validation

```sql
exponent_nonretail.HCO_ID = ship_to_867.HCO_ID
AND exponent_nonretail.PRODUCT = ship_to_867.PRODUCT
AND exponent_nonretail.MONTH = ship_to_867.MONTH
```

### HCO identity from non-retail volume

If you need an HCO name while staying at HCO grain, dedupe the HCO dimension first.

```sql
SELECT DISTINCT
  HCO_ID,
  HCO_NAME
FROM onekey_hcp
```

Then join that deduped set to `exponent_nonretail`.

Do not directly join `exponent_nonretail` to all HCP rows and then sum, or you will multiply volume by the number of HCP records under the HCO.

## 7. Reasoning Patterns To Test

These are analytical patterns, not pre-approved answers. Confirm them with SQL before using them.

### Retail versus non-retail classification

Use this sequence:

1. resolve the account or HCO from free-text name or address
2. query `exponent_nonretail` by `HCO_ID`
3. query `ship_to_867` by `HCO_ID`, `PRODUCT`, and `MONTH`
4. enumerate affiliated HCPs from `affiliation_onekey`
5. map affiliated HCPs to current NPIs in `onekey_hcp`
6. query `ddd_retial` by those NPIs
7. interpret results:
   - outlet-level volume present and no provider-level DDD usually suggests non-retail behavior
   - provider-level DDD present at NPI grain suggests retail behavior
   - `RETAIL_FLAG` and `ACCOUNT_TYPE` are supporting hints, not proof by themselves

### Credit questions

Use this sequence:

1. determine whether the question is about provider-level credit or account or territory-level credit visibility
2. check `onekey_hcp.CREDIT_FLAG`
3. check `speciality.CREDITABLE_FLAG`
4. check the relevant territory mapping:
   - HCP-driven: `alignement_file`
   - ship-to or account-driven: `ship_to_867`
5. explain the difference between:
   - eligibility for credit
   - where the volume exists
   - where the territory mapping points
   - whether the workbook proves actual dashboard payout

### Merge or territory-change reasoning

Use this sequence:

1. start with `dcr_logs`
2. inspect current and retired provider state in `onekey_hcp`
3. inspect current alignment in `alignement_file`
4. inspect DDD continuity in `ddd_retial`
5. explain whether prescribing behavior changed or only attribution changed

### Cross-table consistency checks

If affiliation, primary HCO, and territory disagree for the same HCP, say so explicitly. Do not force a clean narrative that the SQL does not support.

## 8. Dangerous Joins And Failure Modes

These are the main ways an LLM can generate wrong answers.

### Failure mode 1: joining DDD to raw `onekey_hcp` by NPI

Problem:

- some providers have multiple OneKey rows with the same NPI

Bad effect:

- retail `TRX` and `NBRX` gets duplicated
- one row looks retired and another active
- the same scripts can appear tied to multiple HCO contexts

Fix:

- filter to `RECORD_STATUS = 'Active'`
- or select the latest or current row per `NPI`

### Failure mode 2: joining HCO-level volume to HCP-level rows and summing

Problem:

- `exponent_nonretail` is HCO grain
- `onekey_hcp` and `affiliation_onekey` are HCP grain

Bad effect:

- monthly outlet volume can double or triple if joined to multiple HCP rows and then summed

Fix:

- aggregate at HCO grain before joining to HCP details
- or use a deduped HCO dimension

### Failure mode 3: comparing names to ID columns

Problem:

- the user may give an HCO name or address, but fact tables store `HCO_ID`

Bad effect:

- valid data appears missing because the filter is pointed at the wrong key

Fix:

- resolve the name or address to `HCO_ID` first
- resolve provider names to `RECORD_ID` and `NPI` first

### Failure mode 4: trusting `alignement_file.ZIP`

Problem:

- it is a timestamp, not a usable ZIP code, in this workbook

Fix:

- use `onekey_hcp.ZIP` for actual ZIP context
- use `alignement_file.TERRITORY_ID` and `EFFECTIVE_DATE` for alignment status

### Failure mode 5: assuming affiliation and primary HCO always match

Problem:

- some HCP-to-HCO relationships conflict across tables

Fix:

- call out the inconsistency explicitly
- if needed, treat `onekey_hcp` as current primary HCO and `affiliation_onekey` as relationship context or history

### Failure mode 6: treating shipments as scripts

Problem:

- `UNITS_SHIPPED` and `TRX_VOLUME` can line up for some account and month combinations, but that should not be assumed as a rule

Fix:

- use 867 to confirm shipment and territory
- use Exponent for non-retail volume
- use DDD for retail prescriber-level scripts

## 9. Recommended SQL Patterns

Replace placeholders such as `{{NPI}}`, `{{HCO_ID}}`, `{{TERRITORY_ID}}`, `{{ACCOUNT_NAME}}`, and `{{ADDRESS_FRAGMENT}}` with values supplied by the user or discovered in earlier queries.

## Pattern A: resolve account text to HCO identifiers

```sql
SELECT DISTINCT
  HCO_ID,
  HCO_NAME,
  ADDRESS,
  CITY,
  STATE,
  ACCOUNT_TYPE,
  RETAIL_FLAG
FROM onekey_hcp
WHERE UPPER(HCO_NAME) LIKE UPPER('%{{ACCOUNT_NAME}}%')
   OR UPPER(ADDRESS) LIKE UPPER('%{{ADDRESS_FRAGMENT}}%')
LIMIT 100;
```

## Pattern B: build a current HCP dimension before joining DDD

```sql
WITH current_hcp AS (
  SELECT
    RECORD_ID,
    NPI,
    HCP_NAME,
    SPECIALTY,
    SPECIALTY_CODE,
    RECORD_STATUS,
    MASTER_HCP_ID,
    CREDIT_FLAG,
    HCO_ID,
    HCO_NAME,
    ADDRESS,
    CITY,
    STATE,
    ZIP,
    ACCOUNT_TYPE,
    RETAIL_FLAG,
    REPORTING_STATUS,
    RECORD_CREATION_DATE,
    RECORD_EXPIRATION_DATE,
    ROW_NUMBER() OVER (
      PARTITION BY NPI
      ORDER BY
        CASE WHEN RECORD_STATUS = 'Active' THEN 0 ELSE 1 END,
        RECORD_CREATION_DATE DESC
    ) AS rn
  FROM onekey_hcp
)
SELECT
  c.RECORD_ID,
  c.HCP_NAME,
  c.HCO_ID,
  c.HCO_NAME,
  d.MONTH,
  d.PRODUCT,
  d.NBRX,
  d.TRX
FROM current_hcp c
JOIN ddd_retial d
  ON c.NPI = d.NPI
WHERE c.rn = 1
LIMIT 100;
```

## Pattern C: non-retail account volume without multiplying rows

```sql
WITH hco_dim AS (
  SELECT DISTINCT
    HCO_ID,
    HCO_NAME
  FROM onekey_hcp
)
SELECT
  e.HCO_ID,
  h.HCO_NAME,
  e.MONTH,
  e.PRODUCT,
  e.TRX_VOLUME,
  s.SHIP_TO_ID,
  s.UNITS_SHIPPED,
  s.TERRITORY_ID,
  s.DISTRICT,
  s.REGION
FROM exponent_nonretail e
LEFT JOIN hco_dim h
  ON e.HCO_ID = h.HCO_ID
LEFT JOIN ship_to_867 s
  ON e.HCO_ID = s.HCO_ID
 AND e.PRODUCT = s.PRODUCT
 AND e.MONTH = s.MONTH
WHERE e.HCO_ID = '{{HCO_ID}}'
LIMIT 100;
```

## Pattern D: explain provider lineage or merge or realignment

```sql
WITH provider_records AS (
  SELECT
    RECORD_ID,
    NPI,
    HCP_NAME,
    RECORD_STATUS,
    MASTER_HCP_ID,
    HCO_ID,
    ADDRESS,
    ZIP
  FROM onekey_hcp
  WHERE NPI = {{NPI}}
)
SELECT
  d.DCR_ID,
  d.REQUEST_TYPE,
  d.OLD_HCP_ID,
  d.MASTER_HCP_ID,
  d.DESCRIPTION,
  d.DATE,
  old.HCP_NAME AS old_hcp_name,
  old.RECORD_STATUS AS old_status,
  old.HCO_ID AS old_hco_id,
  old.ADDRESS AS old_address,
  old.ZIP AS old_zip,
  master.HCP_NAME AS master_hcp_name,
  master.RECORD_STATUS AS master_status,
  master.HCO_ID AS master_hco_id,
  master.ADDRESS AS master_address,
  master.ZIP AS master_zip,
  a.TERRITORY_ID,
  a.EFFECTIVE_DATE
FROM dcr_logs d
LEFT JOIN provider_records old
  ON d.OLD_HCP_ID = old.RECORD_ID
LEFT JOIN provider_records master
  ON d.MASTER_HCP_ID = master.RECORD_ID
LEFT JOIN alignement_file a
  ON d.MASTER_HCP_ID = a.HCP_ID
WHERE d.OLD_HCP_ID IN (SELECT RECORD_ID FROM provider_records)
   OR d.MASTER_HCP_ID IN (SELECT RECORD_ID FROM provider_records)
ORDER BY d.DATE
LIMIT 100;
```

## Pattern E: show retail volume continuity for a provider

```sql
SELECT
  d.NPI,
  d.MONTH,
  d.PRODUCT,
  d.NBRX,
  d.TRX
FROM ddd_retial d
WHERE d.NPI = {{NPI}}
ORDER BY d.MONTH
LIMIT 100;
```

## Pattern F: enumerate affiliated providers for an account before searching DDD

```sql
SELECT
  a.HCO_ID,
  a.HCP_ID,
  h.NPI,
  h.HCP_NAME,
  h.SPECIALTY_CODE,
  h.CREDIT_FLAG,
  h.RETAIL_FLAG,
  h.ACCOUNT_TYPE
FROM affiliation_onekey a
JOIN onekey_hcp h
  ON a.HCP_ID = h.RECORD_ID
WHERE a.HCO_ID = '{{HCO_ID}}'
LIMIT 100;
```

## 10. Best-Practice Answer Framing For The LLM

When answering users, separate these ideas:

- what the SQL results prove directly
- what is strongly implied by combining those result sets
- what remains unresolved or not directly provable from the data

Example structure:

- Directly proven by SQL:
  - where the volume exists
  - what grain the volume is at
  - what the current territory mapping is
  - whether a merge or realignment event exists
  - whether the relevant provider or specialty is marked credit-eligible
- Strongly implied:
  - whether the issue is likely attribution, alignment, or classification rather than lack of demand
- Not directly proven:
  - exact dashboard payout behavior
  - whether an exception request was later approved
  - any internal narrative not present in SQL evidence

When the user asks "am I supposed to be getting credit for it?", be precise:

- `CREDIT_FLAG` and `CREDITABLE_FLAG` prove eligibility signals
- territory mappings prove where the record or ship-to currently points
- the workbook may not prove final compensation payout logic
- if the data only proves eligibility and mapping, say that clearly instead of overstating payout certainty

The final answer prompt in the current code should rely only on the user question plus SQL results. If evidence is incomplete, say so explicitly instead of filling gaps with unsupported narrative.

## 11. Short Rules The LLM Should Always Remember

- Use exact table names, including misspellings.
- Prefer 2-4 focused queries for root-cause questions instead of one oversized query.
- Resolve names and addresses to IDs before querying fact tables.
- Do not compare `HCO_NAME` to `HCO_ID` or provider names to `NPI` without resolution.
- Deduplicate `onekey_hcp` before joining by `NPI`.
- Do not sum HCO-level volume after joining to multiple HCPs.
- Ignore `alignement_file.ZIP` as a real ZIP.
- Use `dcr_logs` first for root-cause questions.
- Use `ddd_retial` for retail prescriber-level volume.
- Use `exponent_nonretail` for non-retail outlet-level volume.
- Use `ship_to_867` to validate ship-to and territory mapping.
- Use `speciality` plus `CREDIT_FLAG` to determine provider-level credit eligibility.
- Treat any cross-table HCP or HCO conflict as a data-quality anomaly unless SQL clearly establishes a current-versus-historical distinction.
- Use this guide as reasoning support, not as pre-fetched evidence.
