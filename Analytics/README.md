# Power BI Analytics

Power BI reads the curated Parquet tables that the pipeline writes to the data lake. The report is embedded in the web app's **Insights** page. Until it is published, that page shows built-in charts calculated from Cosmos DB.

> This folder contains the data model definition: Power Query sources, DAX measures and the report layout. The `.pbix` file is built in Power BI Desktop from these assets (about 1–2 hours), because a binary `.pbix` cannot be generated or meaningfully reviewed from source control. Save it as `Analytics/CallCenterAnalysis.pbix` once it is created.

## 1. Data source

| Table | Path in the storage account | Grain |
|---|---|---|
| `calls` | `insights/curated/calls/YYYY/MM/DD/*.parquet` | one row per call |
| `qa_scores` | `insights/curated/qa_scores/...` | one row per QA criterion per call |
| `compliance` | `insights/curated/compliance/...` | one row per compliance check per call |
| `utterances` | `insights/curated/utterances/...` | one row per utterance (redacted text) |

The column definitions are in [Documents/technical-specification.md §6](../Documents/technical-specification.md#6-storage-outputs).

## 2. Build the model in Power BI Desktop

1. **Get data → Blank query** for each file in [`powerquery/`](powerquery). Paste the M code and name the query after the file:
   - `StorageDfsUrl` is the parameter. Set it to the `storageDfsEndpoint` deployment output.
   - Then create `calls`, `qa_scores`, `compliance` and `utterances`.
2. **Sign in** with *Organizational account*. The user (and later the service principal) needs **Storage Blob Data Reader** on the storage account.
3. **Date table:** `Date = CALENDARAUTO()`, marked as a date table, with a relationship from `Date[Date]` to `calls[call_date]`.
4. **Relationships:** `calls[call_id]` → `qa_scores[call_id]`, `compliance[call_id]` and `utterances[call_id]`. All are one-to-many, with a single filter direction.
5. **Measures:** paste [`measures.dax`](measures.dax) into a new measure table.

## 3. Report pages (agreed KPIs and filters)

| Page | Visuals | Slicers |
|---|---|---|
| Overview | KPI cards (Calls, Avg QA %, Compliance Pass Rate, Negative Sentiment %, Unresolved %, AHT); calls and sentiment by day; resolution donut | Date, team, agent, call reason |
| Call reasons & root causes | Calls by reason with Unresolved %; root cause bar chart; decomposition tree (reason → root cause → resolution) | Date, team |
| Quality | Criterion Avg % by criterion; QA % by agent (matrix with conditional formatting); objective vs subjective | Date, team, agent, criterion category |
| Compliance | Check Pass Rate by check; failures by agent; trend of Check Failures | Date, team, check |
| Customer experience | Emotion start → end (Sankey or matrix); Flagged Calls table with summary; Repeat Contact % trend | Date, team, flag |

Use a drill-through from any call ID to the web app call page. Add a column such as `Link = "https://<web app>/calls/" & calls[call_id] & "/"` with the data category *Web URL*.

## 4. Publish and refresh

1. Publish to a workspace, e.g. `Call Center Analysis – <env>`.
2. Under *Semantic model → Settings → Data source credentials*, use OAuth2 or a service principal with Storage Blob Data Reader.
3. Schedule refresh. Pro allows up to 8 times a day and Premium/Fabric up to 48. For near real time, set the refresh interval to match the reporting needs.

## 5. Embed in the web app

The web app uses the **app owns data** model: users never need a Power BI licence, but the workspace must be on a capacity.

1. Put the workspace on a **Fabric (F SKU) or Power BI Embedded (A SKU)** capacity. This is a cost item that is not in the TSD estimate. F2/A1 is the smallest; size it to the number of concurrent users.
2. In the Power BI admin portal, enable *Service principals can use Fabric APIs*, restricted to a security group that contains the web app's app registration.
3. Add the app registration (created by `setup_entra.sh`) to the workspace as a **Viewer** (or Member).
4. Set `powerBiWorkspaceId` and `powerBiReportId` in `Deployment/ARM/parameters/<env>.parameters.json` and redeploy, or set the `POWERBI_WORKSPACE_ID` and `POWERBI_REPORT_ID` app settings. The web app generates view-only embed tokens using the same client ID and secret as sign-in.

**Alternative without a capacity.** Every viewer has a Power BI Pro licence and opens the report in Power BI directly, or the report is embedded with "user owns data". The built-in Insights page also remains available at `/insights/?view=builtin`.

### Row-level security (optional)

To limit agents to their own data in Power BI:

1. Add an RLS role on `calls` with the filter `[agent_email] = USERPRINCIPALNAME()`. To make this possible, add `agent_email` to the Parquet `calls` schema in `callcenter/parquet.py`.
2. Pass an effective identity in `GenerateToken` (`portal/powerbi.py`).

Supervisors and QA staff see everything without RLS.
