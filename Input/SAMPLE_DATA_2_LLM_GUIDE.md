# Gilead Sample LLM Guide

This file is the business-semantic layer for the LLM. It explains table grain, coded values, identifier handling, safe joins, query planning, and evidence standards for `Input/Gilead_Sample_03_16.xlsx`.

Use this guide as instructions, not as an answer key. Do not assume a specific provider, account, territory, root cause, or recommended response until SQL confirms it from the workbook.

## 1. Current Runtime Behavior

- Frontend: Next.js app in `Gilead-POC-FE-main/Gilead-POC-FE-main`
- Backend: FastAPI app in `backend/src`
- Analytics store: in-memory DuckDB
- Data source: every non-JSON file in `Input/` is loaded automatically into DuckDB
- Every query MUST first check the `business_rules` table to ensure alignment with corporate policies.
- Each Excel sheet becomes a separate DuckDB table
- Sheets with `Unnamed:` column headers are auto-fixed: the first data row is promoted to the header and empty leading columns are dropped

### Request flow

1. Frontend sends `POST /query?question=...&session_id=...` or `POST /api/chats/{chat_id}/messages`.
2. `backend/src/responder.py` handles identifier follow-ups, DCR confirmations, and final answer synthesis.
3. `backend/src/sql_agent.py` plans and executes SQL (heuristic → Azure OpenAI planner → LangChain fallback).
4. The final answer LLM receives only the user question plus SQL execution results.

## 2. Exact Workbook-to-Table Mapping

`Gilead_Sample_03_16.xlsx` becomes these DuckDB tables:

| Excel sheet | DuckDB table | Data rows | Purpose |
| --- | --- | ---: | --- |
| `IQVIA-OneKey` | `iqvia_onekey` | 7 | HCP master data with HCO, affiliation, DNC flags |
| `DCR` | `dcr` | 2 | Data correction / governance event log |
| `IQVIA-DDD` | `iqvia_ddd` | 4 | Non-retail HCO-level volume |
| `Alignment` | `alignment` | 8 | ZIP → territory → region mapping |
| `Xponent` | `xponent` | 5 | Prescriber-level dispensed data (Retail) |
| `867_Shipment` | `867_shipment` | 5 | Shipment records by ship-to location |
| `CRM` | `crm` | 1 | CRM call activity and DNC flag |
| `Marketing Opt` | `marketing_opt` | 1 | Marketing opt-out events |
| `Business_Rules` | `business_rules` | 3 | Business policy rules (DNC, crediting) |

Use these exact table names in SQL. Column names are mixed-case as listed below — use them exactly.

## 3. Core Business Concepts

- `HCP`: healthcare provider / prescriber / physician
- `HCO`: healthcare organization / account / clinic
- `NPI`: provider identifier; an HCP may have multiple OneKey records over time but still share one NPI
- `OneKey` / `IQVIA-OneKey`: the master data system for HCP/HCO records
- `DDD` / `IQVIA-DDD`: non-retail outlet or HCO-level volume
- `Alignment`: ZIP-to-territory-to-region mapping (not HCP-level)
- `Xponent`: prescriber-level dispensed drug data from IQVIA (Xponent)
- `867 Shipment`: shipment data tied to ship-to locations and distributors
- `CRM`: customer relationship management — call logs and contact permissions
- `DNC`: Do Not Contact flag — can be digital-only or in-person
- `DCR`: data correction request / governance event log
- `Sales Credit Flag`: specialty-level crediting eligibility
- `IC Credit`: incentive compensation credit level

## 4. Identifier Discipline And Coded Values

### Identifier rules

- `HCP_ID` is the OneKey HCP record ID (e.g. `HCP_77812`, `HCP_99125`)
- `HCO_ID` is the organization/account ID (e.g. `HCO_9102`, `HCO_4421`)
- `NPI` is numeric and identifies the provider, but is not unique inside `iqvia_onekey` (merged records share the same NPI)
- `Master_ID`: if populated, this HCP record was merged into the specified master record
- `CRM_ID`: CRM activity identifier
- `Ship_To_ID`: ship-to location identifier
- `Territory`: territory code (e.g. `DAL-11`, `NYC-22`, `SFO-09`, `BOS-03`)

### Identifier handling rules

- If the user gives an HCO name, first resolve the matching `HCO_ID` from `iqvia_onekey`
- If the user gives a provider name, first resolve `HCP_ID` and `NPI` from `iqvia_onekey`
- If the user gives a territory string, use the exact `Territory` code
- Do not compare text names to ID columns
- Do not query fact tables with name values inside ID filters

### Coded value dictionary

#### `iqvia_onekey.Status`

- `Active`: current surviving HCP record
- `Retired`: historical or superseded record; may still be relevant for lineage and DCR reasoning

#### `iqvia_onekey.Affiliation_Type`

- `Solo Practice`: owner / sole practitioner relationship
- `Staff Physician`: staff-level physician at the HCO
- `Attending Physician`: attending or treating physician
- `Referring Physician`: referring physician relationship

#### `iqvia_onekey.Retail`

- `Y`: retail-oriented; prescriber-level Xponent may be relevant
- `N`: non-retail; account-level sources (iqvia_DDD) may be more relevant

#### `iqvia_onekey.Digital_DNC_Flag`

- `Y`: digital channels (email, etc.) are blocked for this HCP
- `N`: digital channels are open

#### `iqvia_onekey.Inperson_DNC_Flag`

- `Y`: in-person field visits are restricted
- `N`: in-person field visits are allowed

#### `iqvia_onekey.Specialty`

- `Infectious Disease`: HIV / infectious disease specialty
- `HIV Medicine`: dedicated HIV medicine specialty
- `Internal Medicine`: primary care / internal medicine

#### `xponent.Month`

- Short month text format (e.g. `Nov`, `Jan`, `Feb`, `Mar`) — not `YYYY-MM`

#### `xponent.Territory`

- Territory where the Account activity was recorded (e.g. `DAL-11`, `DAL-19`, `SFO-09`, `BOS-03`)

#### `iqvia_ddd.Period`

- Aggregation period label (e.g. `Last 90 Days`) — not a single month

#### `dcr.Change_Type`

- `Merge`: HCP record merge event
- `DNC Update`: do-not-contact flag change

#### `dcr.Entity`

- `HCP`: event concerns an HCP record
- `CRM Contact Permission`: event concerns CRM contact permissions

#### `alignment.Region`

- `South Central`: southern/central region
- `West`: western region
- `Northeast`: northeastern region

#### `alignment.Area`

- Descriptive area name within a region (e.g. `Dallas North`, `Dallas Medical District`, `San Francisco Central`, `Manhattan North`, `Boston Central`)

#### `867_shipment.Distributor`

- `AmerisourceBergen`: pharmaceutical distributor
- `McKesson`: pharmaceutical distributor
- `Cardinal Health`: pharmaceutical distributor

#### `crm.Call_Frequency`

- `Biweekly`: calls scheduled every two weeks

#### `crm.DNC_Flag`

- `Y`: HCP marked as DNC in CRM system

#### `marketing_opt.Channel`

- `Email`: email communication channel

#### `marketing_opt.Action`

- `Unsubscribe`: HCP opted out / unsubscribed

#### `sales_credit_flag.Creditable`

- `Yes`: specialty is credit-eligible for Gilead HIV products

#### `sales_credit_flag.IC_Credit`

- `Full`: full incentive compensation credit

#### `business_rules.Rule_ID` and `Policy Name`

- `BR-001` / `DNC Policy`: rules governing DNC flag behavior across digital vs. in-person channels
- `BR-004` / `Specialty Crediting Eligibility`: rules governing which specialties receive sales credit

## 5. Table-By-Table Semantic Dictionary

### `iqvia_onekey`

**Grain**: One row per HCP record in OneKey (not one row per unique NPI).

**Columns**: `HCP_ID`, `HCP_Name`, `NPI`, `Master_ID`, `Specialty`, `Status`, `HCP_Address`, `HCP_City`, `HCP_State`, `HCP_ZIP`, `HCO_ID`, `HCO_Name`, `HCO_Address`, `HCO_City`, `HCO_State`, `HCO_ZIP`, `Affiliation_Type`, `Retail`, `Digital_DNC_Flag`, `Inperson_DNC_Flag`

**Key**: `HCP_ID` is unique. `NPI` may appear on multiple records (merged providers share one NPI).

**Query caution**: If joining to DDD on `NPI` without deduping, volume can multiply across historical and active records.

---

### `dcr`

**Grain**: One row per governance event.

**Columns**: `Change_Date`, `Entity`, `Entity_ID`, `Change_Type`, `Details`

**Key**: Use `Entity_ID` to join back to `iqvia_onekey.HCP_ID`.

**Best use**: Root-cause table for "why did this happen?" questions. Always inspect `Details` for the narrative.

---

### `xponent`

**Grain**: One row per NPI + HCP_ID + Product + Month.

**Columns**: `NPI`, `HCP_ID`, `Product`, `Month`, `Units`, `Territory`

**Key**: `HCP_ID` joins to `iqvia_onekey.HCP_ID`. `NPI` joins to `iqvia_onekey.NPI`.

**Best use**: Provider-level prescribing volume (Retail). Check if prescribing continued across merges/territory changes.

**Note**: `Month` is short text (e.g. `Nov`, `Jan`), not `YYYY-MM`. `Territory` is included directly in Xponent, so you can see territory attribution per row.


---

### `iqvia_ddd`

**Grain**: One row per HCO + Product + Period.

**Columns**: `HCO_ID`, `HCO_Name`, `Product`, `Period`, `Units`, `Territory`

**Key**: `HCO_ID` joins to `iqvia_onekey.HCO_ID`.

**Query caution**: HCO grain, not HCP grain. Do not attribute these units to individual physicians.

---

### `alignment`

**Grain**: One row per ZIP code.

**Columns**: `ZIP`, `Territory`, `Region`, `Area`

**Key**: `ZIP` joins to `iqvia_onekey.HCP_ZIP` or `867_shipment.ZIP`.

**Important**: This table maps **ZIPs to territories**, not HCPs to territories. To find an HCP's territory, join through their ZIP or use `xponent.Territory`. To find an HCO's territory, join through `iqvia_ddd.Territory`.

---

### `867_shipment`

**Grain**: One row per shipment event.

**Columns**: `Shipment_Date`, `Product`, `Units`, `Distributor`, `Ship_To_ID`, `Ship_To_Name`, `ZIP`

**Key**: `ZIP` joins to `alignment.ZIP`. `Ship_To_Name` may match `HCO_Name` values.

**Best use**: Validate shipment presence and distribution patterns at ship-to locations.

---

### `crm`

**Grain**: One row per CRM contact record.

**Columns**: `CRM_ID`, `HCP_ID`, `HCP_Name`, `Territory`, `Last_Call_Date`, `Call_Frequency`, `DNC_Flag`

**Key**: `HCP_ID` joins to `iqvia_onekey.HCP_ID`.

**Best use**: Checking CRM contact status, call frequency, and whether DNC applies.

---

### `marketing_opt`

**Grain**: One row per marketing opt-out event.

**Columns**: `Opt_Out_Date`, `HCP_ID`, `HCP_Name`, `Channel`, `Campaign`, `Action`

**Key**: `HCP_ID` joins to `iqvia_onekey.HCP_ID`.

**Best use**: Understanding which channels an HCP has opted out of and when.

---

### `sales_credit_flag` (DEPRECATED)

**Note**: This data is no longer provided in the latest workbook. Check `business_rules` for crediting logic.

---

### `business_rules`

**Grain**: One row per business rule.

**Columns**: `Rule_ID`, `Policy Name`, `Rule Description`

**Best use**: Reference for DNC policy behavior and specialty crediting eligibility rules.

## 6. Canonical Join Paths

### Resolve names to IDs first

```sql
SELECT DISTINCT HCP_ID, HCP_Name, NPI, HCO_ID, HCO_Name
FROM iqvia_onekey
WHERE UPPER(HCP_Name) LIKE UPPER('%{{NAME}}%')
   OR UPPER(HCO_Name) LIKE UPPER('%{{NAME}}%')
LIMIT 100;
```

### Provider to territory (via ZIP)

```sql
iqvia_onekey.HCP_ZIP = alignment.ZIP
```

### Provider to territory (via xponent — more direct)

```sql
iqvia_onekey.HCP_ID = xponent.HCP_ID
-- Territory is directly in xponent.Territory
```

### Provider to DCR events

```sql
iqvia_onekey.HCP_ID = dcr.Entity_ID
```

### Provider to CRM

```sql
iqvia_onekey.HCP_ID = crm.HCP_ID
```

### Provider to marketing opt-outs

```sql
iqvia_onekey.HCP_ID = marketing_opt.HCP_ID
```

### Provider specialty to credit eligibility

```sql
iqvia_onekey.Specialty = sales_credit_flag.Specialty
```

### HCO to non-retail volume

```sql
iqvia_onekey.HCO_ID = iqvia_ddd.HCO_ID
```

### Ship-to ZIP to territory

```sql
"867_shipment".ZIP = alignment.ZIP
```

## 7. Reasoning Patterns

### DNC / Contact Permission

1. Check `iqvia_onekey.Digital_DNC_Flag` and `Inperson_DNC_Flag`
2. Check `crm.DNC_Flag` for CRM-level DNC
3. Check `marketing_opt` for specific channel opt-outs
4. Check `dcr` for DNC Update events
5. Reference `business_rules` for `BR-001` (DNC Policy) to explain the distinction: digital opt-out should NOT block in-person visits unless explicitly requested

### Credit eligibility

1. Check `iqvia_onekey.Specialty` for the provider
2. Join to `sales_credit_flag` to check `Creditable` and `IC_Credit`
3. Reference `business_rules` for `BR-004` (Specialty Crediting Eligibility)
4. Currently creditable: Infectious Disease, Internal Medicine, HIV Medicine

### Merge / lineage

1. Start with `dcr` for Merge events
2. Check `iqvia_onekey` for retired vs active records sharing the same NPI
3. Check `xponent` for prescribing continuity across the merge
4. Check territory changes in xponent rows before and after the merge

### Territory mapping

1. Use `alignment` to map ZIP → Territory → Region → Area
2. Use `xponent.Territory` for prescribing-level territory attribution
3. Use `iqvia_ddd.Territory` for HCO-level territory

## 8. Dangerous Joins And Failure Modes

### Failure mode 1: joining xponent to raw `iqvia_onekey` by NPI without deduping

Providers with merged records share the same NPI. Filter to `Status = 'Active'` or select by specific `HCP_ID`.

### Failure mode 2: comparing names to ID columns

The user may give an HCO name, but fact tables use `HCO_ID`. Always resolve first.

### Failure mode 3: assuming DNC applies to all channels

The new data separates `Digital_DNC_Flag` and `Inperson_DNC_Flag`. Per business rule `BR-001`, a digital opt-out does NOT block in-person visits.

### Failure mode 4: treating shipments as prescriptions

`867_shipment.Units` is shipment volume, not prescriber-level scripts. Use xponent for prescriptions.

### Failure mode 5: quoting table names starting with a digit

The table `867_shipment` starts with a digit. In SQL it must be quoted: `"867_shipment"`.

## 9. Recommended SQL Patterns

### Pattern A: HCP profile with credit eligibility

```sql
SELECT
  o.HCP_ID, o.HCP_Name, o.NPI, o.Specialty, o.Status,
  o.HCO_ID, o.HCO_Name, o.Affiliation_Type,
  o.Retail, o.Digital_DNC_Flag, o.Inperson_DNC_Flag,
  s.Creditable, s.IC_Credit
FROM iqvia_onekey o
LEFT JOIN sales_credit_flag s
  ON o.Specialty = s.Specialty
WHERE o.HCP_ID = '{{HCP_ID}}'
LIMIT 100;
```

### Pattern B: Xponent prescribing with territory

```sql
SELECT
  d.NPI, d.HCP_ID, d.Product, d.Month, d.Units, d.Territory,
  o.HCP_Name, o.Specialty, o.Status
FROM xponent d
JOIN iqvia_onekey o ON d.HCP_ID = o.HCP_ID
WHERE d.NPI = {{NPI}}
LIMIT 100;
```

### Pattern C: DNC investigation

```sql
SELECT
  o.HCP_ID, o.HCP_Name, o.Digital_DNC_Flag, o.Inperson_DNC_Flag,
  c.DNC_Flag AS crm_dnc, c.Last_Call_Date, c.Call_Frequency,
  m.Opt_Out_Date, m.Channel, m.Campaign, m.Action
FROM iqvia_onekey o
LEFT JOIN crm c ON o.HCP_ID = c.HCP_ID
LEFT JOIN marketing_opt m ON o.HCP_ID = m.HCP_ID
WHERE o.HCP_ID = '{{HCP_ID}}'
LIMIT 100;
```

### Pattern D: Merge / DCR lineage

```sql
SELECT
  d.Change_Date, d.Entity, d.Entity_ID, d.Change_Type, d.Details,
  o.HCP_Name, o.Status, o.Master_ID, o.HCO_ID, o.HCO_Name
FROM dcr d
LEFT JOIN iqvia_onekey o ON d.Entity_ID = o.HCP_ID
WHERE d.Entity_ID = '{{HCP_ID}}'
   OR o.Master_ID = '{{HCP_ID}}'
LIMIT 100;
```

### Pattern E: Territory mapping from ZIP

```sql
SELECT ZIP, Territory, Region, Area
FROM alignment
WHERE ZIP = '{{ZIP}}'
LIMIT 100;
```

### Pattern F: Shipment data by product

```sql
SELECT Shipment_Date, Product, Units, Distributor, Ship_To_ID, Ship_To_Name, ZIP
FROM "867_shipment"
WHERE Product = '{{PRODUCT}}'
LIMIT 100;
```

### Pattern G: Business Rules Check (MANDATORY)

```sql
SELECT Rule_ID, "Policy Name", "Rule Description"
FROM business_rules
LIMIT 100;
```

## 10. Best-Practice Answer Framing

Separate these ideas in answers:

- **Directly proven by SQL**: where volume exists, what territory maps show, whether a merge/DNC event occurred, whether the specialty is creditable
- **Strongly implied**: whether the issue is attribution, contact permission, or classification
- **Not directly proven**: exact dashboard payout behavior, whether an exception was approved, any internal narrative not in SQL

## 11. Short Rules The LLM Should Always Remember

- Use exact table and column names (mixed-case, as stored).
- MANDATORY: Check `business_rules` for every query to verify policy logic (DNC, crediting, etc.). This is a priority table.
- Quote `"867_shipment"` and `"business_rules"` if needed (though `business_rules` is safe in DuckDB).
- Prefer 2-4 focused queries for root-cause questions.
- Resolve names and addresses to IDs before querying fact tables.
- Deduplicate `iqvia_onekey` by NPI when joining to xponent (filter `Status = 'Active'` or use specific `HCP_ID`).
- `Digital_DNC_Flag` and `Inperson_DNC_Flag` are separate — digital opt-out does NOT block in-person (per BR-001).
- `xponent` (HCP level) uses `Month` (e.g., `Nov`, `Jan`).
- `iqvia_ddd` (HCO level) uses `Period` (e.g., `Nov`, `Jan`).
- Use `dcr` first for root-cause questions.
- Use `business_rules` as the primary source of truth for explaining DNC and crediting policy distinctions.
- Use this guide as reasoning support, not as pre-fetched evidence.

## 5. Data Investigation Framework (SOPs)

When investigating field data inquiries, follow these diagnostic patterns to identify root causes. Do not rely on a single table; always cross-reference master data with facts.

### SOP 1: Volume Disappearance / Unexpected Drops

A volume drop for a specific provider (HCP) is usually caused by a change in master data or alignment, not just a stop in prescribing.

1.  **Check Record Status**: Query `iqvia_onekey` for all records under the NPI. Look for `Status = 'Retired'` and check the `Master_ID`.
2.  **Investigate Merges/Moves**:
    *   **Merge**: If `Master_ID` is populated on a retired record, identify the surviving `Master_ID`.
    *   **Move**: Compare `HCP_Address` and `HCP_ZIP` across historical and active records.
3.  **Cross-Reference Alignment**: Use the current `ZIP` from the surviving record to check the `alignment` table. If the `Territory` has changed, the volume has moved to a different owner.
4.  **Confirm Governance**: Search `dcr` for any `Change_Type` related to 'Merge', 'Realignment', or 'Address Update' for this NPI/ID in the last 30 days.
5.  **Verify Fact Continuity**: Pull `xponent` for the NPI (old and new IDs). If volume exists on the new ID but in a different territory, the "disappearance" is an attribution change.

### SOP 2: Shipment vs. Dispense (867 vs. xponent)

Reps often confuse "Shipment" (sell-in) with "Dispense" (sell-out).

1.  **Verify Inbound Shipment**: Check `867_shipment` for product, units, and date. Note the `Ship_To_ID`.
2.  **Verify Ship-To Mapping**: Cross-reference `Ship_To_ID` (or `ZIP`) against the `alignment` or 867 mapping context to ensure it belongs to the rep's territory.
3.  **Identify Prescriber Context**: Use `iqvia_onekey` to find HCPs affiliated with the `HCO_ID` or address of the ship-to location.
4.  **Check Credit Eligibility**: Confirm the `Specialty` and `Status` of affiliated HCPs. Only creditable specialties (e.g., ID, HIV, Internal Medicine) generate IC credit.
5.  **Explain the Lag**: xponent (retail) dispensed data has a typical **4–6 week reporting lag** from the date of shipment/dispense. If the shipment is recent, the dispense will not appear yet.

### SOP 3: Account Grain (Retail vs. Non-Retail)

If a rep cannot find prescriber-level volume for an account, the account is likely classified as Non-Retail.

1.  **Check Classification**: Query `iqvia_onekey.Retail` for the HCO. 
    *   `Retail = 'Y'`: Volume is at the HCP level (look in `xponent`).
    *   `Retail = 'N'`: Volume is at the Outlet/HCO level (look in `iqvia_ddd`).
2.  **Match Fact Table to Grain**:
    *   If Non-Retail, ignore `xponent` and pull volume from `iqvia_ddd` using `HCO_ID`.
    *   If Retail, pull volume from `xponent` using `NPI`.
3.  **Verify Territory Link**: If volume is found but not on the dashboard, check the `Territory` field in the fact table. If it doesn't match the rep's territory, check `alignment` for the HCO's ZIP.
