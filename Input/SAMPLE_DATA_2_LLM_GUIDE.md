# Gilead Sample LLM Guide

This file is the business-semantic layer for the LLM. It explains table grain, identifier handling, safe joins, and the root-cause logic for `Input/Gilead_Sample_03_16.xlsx`.

Use this guide as instructions, not as an answer key. Do not assume a root cause until SQL confirms it from the workbook.

## 1. Current Runtime Behavior

- Frontend: Next.js app in `Gilead-POC-FE-main/Gilead-POC-FE-main`
- Backend: FastAPI app in `backend/src`
- Analytics store: in-memory DuckDB
- Data source: every non-JSON file in `Input/` is loaded automatically into DuckDB
- Every query MUST first check the `business_rules` table to ensure alignment with corporate policies
- Each Excel sheet becomes a separate DuckDB table

## 2. Exact Workbook-to-Table Mapping

`Gilead_Sample_03_16.xlsx` becomes these DuckDB tables:

| Excel sheet | DuckDB table | Data rows | Purpose |
| --- | --- | ---: | --- |
| `IQVIA-OneKey` | `iqvia_onekey` | 15 | HCP/HCO master data with DNC flags, affiliation, retail flag |
| `Xponent` | `xponent` | 59 | Monthly prescriber-level dispensed volume |
| `Alignment` | `alignment` | 15 | ZIP to territory to region mapping |
| `IQVIA-DDD` | `iqvia_ddd` | 5 | Non-retail HCO-level volume |
| `DCR` | `dcr` | 3 | Data correction / governance events |
| `867_Shipment` | `867_shipment` | 5 | Shipment records by ship-to location |
| `CRM` | `crm` | 9 | Current CRM contact roster by HCP |
| `CRM_Call_History` | `crm_call_history` | 264 | Call-level field activity history |
| `HCP_Targeting` | `hcp_targeting` | 9 | Latest claims-based HCP potential and deciles |
| `Target_List_Refresh` | `target_list_refresh` | 9 | Field target list version vs claims refresh alignment |
| `Access_Barriers` | `access_barriers` | 9 | Access and channel barriers by HCP |
| `Activity_Performance_Summary` | `activity_performance_summary` | 18 | Quarter-level HCP summary for activity vs TRx analysis |
| `Marketing Opt` | `marketing_opt` | 1 | Marketing opt-out events |
| `Business_Rules` | `business_rules` | 5 | Business policy rules |

Use these exact table names in SQL.

## 3. Core Business Concepts

- `HCP`: healthcare provider / prescriber / physician
- `HCO`: healthcare organization / account / clinic
- `NPI`: provider identifier
- `Retail = 'Y'`: use prescriber-level `xponent` for outcome analysis
- `Retail = 'N'`: use HCO-level `iqvia_ddd` where needed
- `Decile`: claims-based HCP opportunity ranking, where higher = higher potential
- `Claims target`: the latest recommended field target flag based on current claims potential
- `Field target`: the target flag currently used by the field force
- `TRx`: in this sample, the closest direct metric is dispensed prescription volume from `xponent`
- `Execution quality`: represented by `Message_Depth`, `Avg_Message_Depth_Score`, and `Avg_Engagement_Score`

## 4. Identifier Discipline

- `HCP_ID` is the OneKey provider record ID
- `HCO_ID` is the organization/account ID
- `NPI` identifies the provider and should align to `xponent.NPI`
- `Territory` is a territory code like `BOS-03` or `NYC-22`
- `Region` is a rollup like `Northeast`

Rules:

- If the user gives a provider name, resolve `HCP_ID` first from `iqvia_onekey`
- If the user gives an HCO name, resolve `HCO_ID` first from `iqvia_onekey`
- If the user gives a territory, use the exact code
- Do not compare names to ID columns

## 5. Important Coded Values

### `alignment.Region`

- `Northeast`
- `South Central`
- `West`

### `iqvia_onekey.Status`

- `Active`
- `Retired`

### `iqvia_onekey.Retail`

- `Y`: use `xponent`
- `N`: use `iqvia_ddd`

### `crm_call_history.Message_Depth`

- `Deep Dive`: higher-depth clinical call
- `Core`: standard priority detail
- `Reminder`: lower-depth reminder / maintenance call

### `crm_call_history.Call_Outcome`

- `Positive Engagement`
- `Neutral`
- `No Change`
- `Low Engagement`

### `hcp_targeting.Opportunity_Segment`

- `High`
- `Mid`
- `Low`

### `target_list_refresh.Alignment_Status`

- `Aligned`
- `Misaligned`

### `access_barriers.Barrier_Type`

- `None`
- `Digital Opt-Out`
- `Prior Authorization`

## 6. Table-By-Table Semantic Dictionary

### `iqvia_onekey`

**Grain**: One row per HCP record.

**Key columns**: `HCP_ID`, `HCP_Name`, `NPI`, `Status`, `HCO_ID`, `HCO_Name`, `Retail`, `Digital_DNC_Flag`, `Inperson_DNC_Flag`

**Best use**: master data, provider lookup, retail vs non-retail classification.

---

### `xponent`

**Grain**: One row per `NPI + HCP_ID + Product + Month`.

**Key columns**: `NPI`, `HCP_ID`, `Product`, `Month`, `Units`, `Territory`

**Important note**: `Month` is stored as `YYYY-MM` text in this enriched sample, for example `2025-10` and `2026-03`.

**Best use**: monthly provider-level outcome / TRx proxy analysis.

---

### `crm`

**Grain**: One row per current CRM contact roster record.

**Key columns**: `CRM_ID`, `HCP_ID`, `Territory`, `Last_Call_Date`, `Call_Frequency`, `DNC_Flag`

**Best use**: confirm an HCP is rostered and see current contact status.

---

### `crm_call_history`

**Grain**: One row per field call.

**Key columns**: `Call_ID`, `Call_Date`, `HCP_ID`, `Territory`, `Region`, `Rep_ID`, `Channel`, `Message_Theme`, `Message_Depth`, `Call_Outcome`

**Best use**: call volume trends, monthly frequency, execution mix, and activity distribution across HCP segments.

**Important note**: use this table, not `crm`, for QoQ activity analysis.

---

### `hcp_targeting`

**Grain**: One row per HCP using the latest claims-based potential logic.

**Key columns**: `HCP_ID`, `Territory`, `Region`, `Decile`, `Opportunity_Segment`, `Claims_Based_Potential_TRx`, `Claims_Target_Flag`, `Field_Target_Flag`

**Best use**: determine whether field effort is aimed at the right HCPs.

---

### `target_list_refresh`

**Grain**: One row per HCP target assignment comparison.

**Key columns**: `HCP_ID`, `Field_Target_List_Version`, `Field_Target_Refresh_Date`, `Claims_Potential_Version`, `Claims_Refresh_Date`, `Field_Target_Flag`, `Claims_Target_Flag`, `Alignment_Status`

**Best use**: prove whether the field target list is stale relative to updated claims potential.

---

### `access_barriers`

**Grain**: One row per HCP and product barrier status.

**Key columns**: `HCP_ID`, `Product`, `Barrier_Type`, `Barrier_Status`, `Barrier_Severity`, `Effective_Date`, `Notes`

**Best use**: test whether access or channel barriers explain flat performance.

---

### `activity_performance_summary`

**Grain**: One row per `HCP + Quarter`.

**Key columns**: `Quarter`, `Region`, `Territory`, `HCP_ID`, `Decile`, `Opportunity_Segment`, `Field_Target_Flag`, `Claims_Target_Flag`, `Call_Count`, `Avg_Calls_Per_Month`, `TRx`, `Prior_Quarter_TRx`, `TRx_Growth_Pct`, `Avg_Message_Depth_Score`, `Avg_Engagement_Score`, `Access_Barrier_Flag`

**Best use**: fastest entry point for the “activity vs performance disconnect” scenario.

---

### `iqvia_ddd`

**Grain**: One row per `HCO + Product + Period`.

**Best use**: non-retail outlet-level volume only.

**Critical rule**: if `Retail = 'Y'`, do NOT use `iqvia_ddd` for prescriber outcome reasoning.

---

### `dcr`

**Grain**: One row per governance event.

**Best use**: merger, DNC, and stale-target operational context.

---

### `marketing_opt`

**Grain**: One row per marketing opt-out event.

**Best use**: digital-only opt-out context. Do not assume this blocks in-person calls.

---

### `business_rules`

**Grain**: One row per policy rule.

**Best use**: policy-backed explanations for DNC, target refresh cadence, and call frequency optimization.

## 7. Canonical Join Paths

### Provider to CRM roster

```sql
iqvia_onekey.HCP_ID = crm.HCP_ID
```

### Provider to call history

```sql
iqvia_onekey.HCP_ID = crm_call_history.HCP_ID
```

### Provider to targeting

```sql
iqvia_onekey.HCP_ID = hcp_targeting.HCP_ID
```

### Provider to target-list alignment

```sql
iqvia_onekey.HCP_ID = target_list_refresh.HCP_ID
```

### Provider to access barriers

```sql
iqvia_onekey.HCP_ID = access_barriers.HCP_ID
```

### Provider to monthly TRx proxy

```sql
iqvia_onekey.HCP_ID = xponent.HCP_ID
```

### Provider ZIP to territory / region

```sql
iqvia_onekey.HCP_ZIP = alignment.ZIP
```

## 8. Investigation Logic For Activity vs Performance Disconnect

When the user asks why activity increased but TRx is flat, follow this order:

1. Query `business_rules`
2. Start with `activity_performance_summary` for the broad QoQ picture
3. Segment the results by `Opportunity_Segment` or `Decile`
4. Check `crm_call_history` for monthly call frequency and whether any HCP-month exceeds 8 calls
5. Check `hcp_targeting` and `target_list_refresh` for field-vs-claims misalignment
6. Check `access_barriers` to see if barriers are broad enough to explain the pattern
7. Use `xponent` as the detailed monthly outcome source when the user asks for proof

## 9. Strong Signals For This Scenario

These patterns should be treated as meaningful if SQL confirms them:

- QoQ call count up while QoQ TRx is flat or near-flat
- High-decile HCPs show positive TRx growth with only modest call change
- Mid- and low-decile HCPs absorb most incremental calls with weak or negative TRx response
- `Field_Target_Flag = 'Y'` but `Claims_Target_Flag = 'N'`
- `Alignment_Status = 'Misaligned'`
- `Avg_Calls_Per_Month > 8` with low or no TRx response
- Access barriers present only on a small minority of HCPs

If those signals appear together, the likely issue is targeting allocation and execution mix, not overall effort volume.

## 10. Dangerous Failure Modes

- Using `crm` instead of `crm_call_history` for call trend analysis
- Using `iqvia_ddd` for retail HCP outcome analysis
- Treating digital opt-out as an in-person call block
- Ignoring `target_list_refresh` and assuming the active field list is current
- Summing mixed-grain HCP and HCO facts without calling out the grain difference

## 11. Recommended SQL Patterns

### Pattern A: Quarter-level activity vs outcome by segment

```sql
SELECT
  Quarter,
  Opportunity_Segment,
  SUM(Call_Count) AS calls,
  SUM(TRx) AS trx
FROM activity_performance_summary
WHERE Region = 'Northeast'
GROUP BY 1, 2
ORDER BY 1, 2;
```

### Pattern B: Targeting misalignment

```sql
SELECT
  HCP_ID, HCP_Name, Territory,
  Field_Target_Flag, Claims_Target_Flag, Alignment_Status
FROM target_list_refresh
WHERE Region = 'Northeast'
LIMIT 100;
```

### Pattern C: Monthly frequency vs response

```sql
SELECT
  c.HCP_ID,
  c.Territory,
  strftime(c.Call_Date, '%Y-%m') AS call_month,
  COUNT(*) AS calls_in_month
FROM crm_call_history c
WHERE c.Region = 'Northeast'
GROUP BY 1, 2, 3
ORDER BY 1, 3;
```

### Pattern D: Mandatory business rules check

```sql
SELECT Rule_ID, "Policy Name", "Rule Description"
FROM business_rules
LIMIT 100;
```

## 12. Best-Practice Answer Framing

Separate these ideas in answers:

- **Directly proven by SQL**: activity growth, TRx change, segment mix, stale target flags, call frequency, barriers
- **Strongly implied**: misallocation of effort, diminishing returns, shallow execution on priority HCPs
- **Not directly proven**: the internal intent of reps or exact promotional quality beyond the recorded execution fields

## 13. Short Rules The LLM Should Always Remember

- Always query `business_rules` first
- For activity trend questions, prefer `activity_performance_summary` and `crm_call_history`
- For targeting questions, prefer `hcp_targeting` and `target_list_refresh`
- For retail TRx questions, use `xponent`
- For non-retail HCO questions, use `iqvia_ddd`
- `Digital_DNC_Flag = 'Y'` does not block in-person calls unless explicitly stated
- Be explicit about whether the evidence points to targeting, execution quality, or access barriers
