# User Guide

How to use the Call Center Analysis web app (*Call Insights*): what each page shows, what the scores mean,
and how to walk a customer through the demo.

- [How the system works](#how-the-system-works)
- [Signing in and roles](#signing-in-and-roles)
- [Common controls](#common-controls)
- [Overview](#overview)
- [Calls](#calls)
- [Call detail](#call-detail)
- [How a call is scored](#how-a-call-is-scored)
- [Agents and agent scorecards](#agents-and-agent-scorecards)
- [Insights](#insights)
- [Pipeline: processing and uploading recordings](#pipeline-processing-and-uploading-recordings)
- [Settings: QA rubric, compliance checks, call reasons](#settings-qa-rubric-compliance-checks-call-reasons)
- [CRM API and Power BI data](#crm-api-and-power-bi-data)
- [Suggested demo walkthrough (15–20 minutes)](#suggested-demo-walkthrough-1520-minutes)
- [Questions customers often ask](#questions-customers-often-ask)
- [Troubleshooting](#troubleshooting)

## How the system works

Every recorded call goes through the same steps automatically, usually within 1–3 minutes of the recording
arriving:

| Step | Azure service | Result |
|---|---|---|
| 1. Recording arrives | Storage account, `recordings` container | The telephony system (or you, from the **Pipeline** page) drops a `.wav` file plus its call details: agent, team, customer, direction |
| 2. Transcription | Azure AI Speech | Text of the call with timestamps. Each line is labelled **AGENT** or **CUSTOMER**: by channel for stereo recordings, by voice for mono ones |
| 3. Privacy and sentiment | Azure AI Language | Personal data (names, phone numbers, card numbers, dates of birth, emails, spoken digits) is **removed before anything else sees the text**. Every line gets a positive / neutral / negative score |
| 4. Speech measurements | Built in | Agent speaking pace (words per minute), share of talk time, silences of 10 seconds or more (dead air) and talk-overs |
| 5. Call analysis | Azure OpenAI (`gpt-5-mini`) | Summary, call reason, root cause, outcome, customer emotion, the **20-parameter QA evaluation**, **compliance checks**, coaching recommendations and follow-up actions |
| 6. Storage | Cosmos DB + data lake | The web app and the CRM API read Cosmos DB; Power BI reads the Parquet files in the data lake |

The web app only shows results. It never changes a recording. If processing fails, the system retries up to
5 times on its own, and you can retry from the web app.

## Signing in and roles

**Demo environment:** open the web app, type the demo password, then pick a demo user. Each user has one role.
**Production:** users sign in with their Microsoft work account (Entra ID), and an administrator assigns the roles.

| Role | Demo user | Sees | Pages |
|---|---|---|---|
| **Admin** | Asha Rao | Everything | All, including **Settings** |
| **Supervisor** | Kabir Khan | All calls and agents | Overview, Calls, Agents, Insights, Pipeline |
| **QA** (quality analyst) | Leela Thomas | All calls and agents | Overview, Calls, Agents, Insights |
| **Agent** | One per agent in the processed calls, e.g. Priya Sharma | **Only their own calls** | Overview, Calls, **My scorecard** |

The agent demo users appear in the list once calls for that agent have been processed. Sign out with the
arrow icon next to your name, at the bottom left.

If someone opens a page their role does not allow, they see *Access denied*. An agent who opens another agent's
call link also gets *Access denied*.

## Common controls

- **Date range buttons** (top right of most pages): **7d**, **30d** (default), **90d** and **All**. Counts,
  averages and charts cover only calls in that period. The demo calls carry the date they were uploaded.
- **Team selector** (Overview): shown when there is more than one team.
- **Clickable rows:** any table row with a hover highlight opens the call, agent or filtered call list behind it.
- **Grey labels** such as `NAME`, `PHONE`, `CARD`, `DATE` or `EMAIL` mark personal data removed from the
  transcript. The original text is not stored anywhere the web app can read.
- **Colours:** green is good (positive, passed, resolved), amber is borderline (partial, follow-up), red needs
  attention (negative, failed, escalated or unresolved).

## Overview

The landing page: the state of the contact centre for the selected period.

**KPI tiles**

| Tile | Meaning |
|---|---|
| Calls analysed | Calls fully processed; below it, how many failed or are still in progress |
| Average QA score | Mean QA % across calls; below it, the share of calls at or above the pass mark (75 % by default) |
| Compliance pass rate | Share of calls in which **every** applicable mandatory check was met |
| Negative sentiment | Share of calls where the customer's overall sentiment was negative |
| Unresolved / escalated | Share of calls not resolved on the call; below it, the share of repeat contacts |
| Avg handle time | Average call duration (minutes:seconds) |

**Charts and lists**

- **Daily calls by customer sentiment:** one bar per day, split into positive, neutral and negative customers (negative at the bottom). Hover a bar for the counts, percentages and the day's total.
- **Customer sentiment** and **Resolution** doughnuts: the mix of positive / neutral / negative calls, and of
  resolved / follow-up / escalated / unresolved calls.
- **Top call reasons:** the most common reasons, with the share unresolved, the share negative and the average QA
  for each. Click a reason to see its calls.
- **Needs attention:** flagged calls, most serious first. The flags are:

  | Flag | Raised when |
  |---|---|
  | Compliance breach | Any mandatory compliance check failed |
  | Unhappy customer | Customer sentiment clearly negative, or the customer ended the call anxious, frustrated, angry, disappointed or confused |
  | Repeat contact | The customer says they have called about this before |
  | Unresolved | The outcome was unresolved or escalated |
  | Low QA score | QA % below the pass mark |

- **Root causes:** what is driving calls, such as "billing system error" or "card compromised". Use it to fix
  processes, not just coach agents.
- **Agent leaderboard** (Supervisor, QA, Admin): calls, average QA and compliance per agent. Agents see
  **% met by QA parameter** for their own calls instead.

## Calls

Every call you are allowed to see, newest first, 25 per page.

- **Search** by call ID, agent name or words in the summary (for example `fraud`). The list updates as you type.
- **Filters:** agent, team, call reason, customer sentiment, resolution, flag, and a QA range (min / max %).
  **Clear** removes them all. The filters are kept in the page address, so you can bookmark or share a filtered list.
- **Sort** by clicking the Date, Agent, Sentiment, QA or Duration column header. Click again to reverse.
- Calls still being processed show their stage (for example *Transcribing*) instead of a summary.

## Call detail

Open a call from any list. The header shows the call reason, call ID, agent and team, start time, duration and
customer ID, plus the outcome and customer sentiment. **Re-analyse** (Supervisor, Admin) runs the analysis again,
for example after changing the rubric.

**Left side: listen and read**

- **Audio player** for the original recording.
- **Sentiment strip** under the player: one segment per line of the conversation, coloured by sentiment.
  Click a segment to jump to that moment.
- **Transcript** with timestamps, the speaker and a sentiment dot on each line. Click a line to play from there.
  With **Follow audio** ticked, the current line is highlighted while the recording plays. The count above the
  transcript shows how many personal data items were removed.

**Right side: what the AI found**

| Panel | Contents |
|---|---|
| Summary | A short summary; reason (with detail); root cause; outcome; customer emotion at the start → at the end; sentiment scores for customer and agent (−1 to +1); follow-up actions |
| QA scorecard | The QA %, *Pass* or *Below threshold*, the average score out of 5, the counts of Fully met / Partial / Not met / N/A, and all 20 parameters with their rating and score. **Why this rating** opens the quote from the call, the reasoning and a recommendation. Parameters marked *audio* also use the speech measurements; those marked *CRM* depend on what the agent records in the CRM and are judged only from what was said |
| Compliance | Each mandatory check: ✓ passed, ✕ failed or – not applicable, with the quote that proves it |
| Coaching recommendations | What the agent should do differently, each marked High, Medium or Low priority |
| Measured from the recording | Agent pace (words per minute), agent talk time %, dead air (silences of 10 s or more, and the longest), talk-overs (and how many the agent caused). These are measured, not estimated by the AI |
| Processing | When the call was processed, the services and model used, the prompt version and the tokens used |

If a call failed, the page shows the stage and the error, the processing history, and a **Retry** button.

## How a call is scored

**The 20-parameter QA evaluation** follows the customer's evaluation form (`Config/qa_rubric.yaml`):

| # | Parameter | | # | Parameter |
|---|---|---|---|---|
| 1 | Greeting / Self & Company Introduction | | 11 | Professionalism / Casual / Politeness |
| 2 | RPC (right party contact) confirmation | | 12 | Hold Protocol / Dead Air *(audio)* |
| 3 | Probing for Basic Details | | 13 | Customized script adherence |
| 4 | Complete & correct company profiling | | 14 | Convincing / Negotiation / Objection handling / Urgency creation |
| 5 | BANT confirmation (Budget, Authority, Need, Time) | | 15 | PM meeting benefits informed |
| 6 | Active listening / Repetition / Interruption / Overlapping *(audio)* | | 16 | Probing for Appointment date & time |
| 7 | Appreciate / Apologize / Empathy | | 17 | Incorrect / Falsifying information *(counts double)* |
| 8 | Two-way communication / Rapport building / Personalization | | 18 | Closing / Summarization |
| 9 | Grammar / Pronunciation / Sentence formation | | 19 | Remarks Capturing *(CRM)* |
| 10 | Rate of speech / Speech clarity / Voice modulation *(audio)* | | 20 | Tagging *(CRM)* |

Each parameter is rated on every call:

| Rating | Score | Counts towards QA % |
|---|---|---|
| Fully met | 5 | 100 % |
| Partial | 3 | 50 % |
| Not met | 0 | 0 % |
| N/A | – | Left out: the situation did not arise. For example, there are no BANT questions on a card-fraud service call |

**QA %** is the weighted average over the parameters that applied. A call **passes** at 75 % or more; the
threshold can be changed in Settings. Because N/A parameters are left out, service calls and sales calls can be
compared fairly.

**Compliance checks** (`Config/compliance_checks.yaml`) are separate from QA and are pass / fail:

1. Call recording disclosure
2. Identity verified before account details (only when account details are discussed)
3. No request for OTP, CVV, PIN or full card number
4. Fees and charges disclosed (when a paid product or change is discussed)
5. Escalation path offered (when the customer is dissatisfied)

A single failed check sets the **Compliance breach** flag, whatever the QA score.

**Call reasons** come from a fixed list (`Config/call_reasons.yaml`): billing, payments, card services, account
update, plan change, technical issue, cancellation, sales opportunity and general enquiry. A fixed list keeps
reports consistent from month to month.

## Agents and agent scorecards

**Agents** (Supervisor, QA, Admin): one row per agent with calls, average QA (with bar), QA pass rate,
compliance rate, % resolved, average customer sentiment, average handle time, and the **coaching focus**: the
parameter where the agent scores lowest. Below the table, **Team % met by QA parameter** shows the team's
strongest and weakest areas.

**Agent scorecard** (click an agent; agents see their own one as **My scorecard**):

- KPI tiles: average QA and pass rate, compliance, resolved on call, customer sentiment, handle time, calls.
- **QA score trend** over the period.
- **Coaching recommendations** combined across the agent's calls, by priority, with how many calls each one came from.
- **20-parameter evaluation table:** for each parameter, the average score, the number of Fully / Partial /
  Not met / N/A ratings, and **% met** with a marker for the team average. The analysis column gives a
  **verdict** (*Strength* at 80 % or more, *Inconsistent* at 50–79 %, *Needs improvement* below 50 %), an example
  quote and a recommendation. This is the view to use in a one-to-one coaching session.
- **Compliance checks** pass rate per check, and the agent's **recent calls**.
- **Download PDF report** (top right) saves a detailed report for the selected date range. It is meant for
  one-to-one coaching sessions or to hand to the agent:
  - the KPIs, an overall assessment against the pass mark, and the agent's strengths and focus areas;
  - the QA score for each call against the pass mark, and the coaching recommendations by priority;
  - the full 20-parameter evaluation with the team average, plus compliance pass rates;
  - a list of all calls, then a **call-by-call review**: summary, root cause, flags, compliance failures with
    quotes, each *Not met* / *Partial* parameter with the reason, the quote and what to do, what was done well,
    coaching, follow-ups and the speech measurements.

  Agents can download their own report from **My scorecard**. The review covers up to the 60 most recent calls
  in the range. Personal data stays redacted in the PDF, as on screen.

## Insights

The built-in analytics page (Supervisor, QA, Admin):

- **% met by QA parameter** across all calls: where the whole floor is weak.
- **Compliance pass rate by check**, counting only calls where the check applied.
- **Customer sentiment**, **Resolution** and **Customer emotion at end of call**.
- **QA score trend**, **Call reasons and outcomes** (click a reason to see its calls) and **Root causes**.

When a Power BI report is published and linked (see `Analytics/README.md`), this page embeds it instead. The
demo uses the built-in charts. Power BI is optional, and it needs extra licences and capacity.

## Pipeline: processing and uploading recordings

(Supervisor, Admin.) The page refreshes every 5 seconds.

- **Status counts** and the **processing success rate**.
- **In progress and failed:** each call's status, stage, attempts and last update, with the error and a
  **Retry** button for failed calls.
- **Recently completed:** calls with the services used and their QA %.

**Upload a recording** tests the system with your own file. Choose a `.wav` file and fill in what you know:
call ID (generated if left blank), call start, agent ID / name / email, team, customer ID, direction
(inbound / outbound). Tick **Stereo recording** if the agent and customer are on separate channels. After
**Upload and analyse**, the call appears in *In progress* and is complete in 1–3 minutes.

Tips:
- Recordings must be in English for now.
- Use the same agent ID for every call by that agent, so their scorecard groups the calls together.
- For an agent to see the call under **My scorecard** with Entra ID sign-in, the agent email must match their
  work account.

In production, the telephony system uploads recordings automatically and nobody uses this form.

## Settings: QA rubric, compliance checks, call reasons

(Admin only.) Three tabs, each an editable configuration file:

| Tab | What you can change |
|---|---|
| QA rubric | Parameter names, descriptions (the scoring rule the AI follows), weights, and `pass_threshold` |
| Compliance checks | Mandatory disclosures and prohibited behaviours, and when each check applies |
| Call reasons | The list every call is classified into |

**Validate and save** checks the file and refuses it with an explanation if something is wrong, so a typo
cannot break scoring. **Reset to default** restores the version shipped with the system. Changes apply to calls
analysed after saving. To re-score an existing call, open it and click **Re-analyse**.

The **Environment** panel shows which services the system is connected to.

## CRM API and Power BI data

- **CRM API:** a read-only JSON API for the CRM or other systems: `/api/calls` (list, with filters such as
  `from`, `to`, `agent`, `customer`, `flag`) and `/api/calls/<call id>` (full result with the redacted
  transcript). Callers send the header `X-API-Key`. The keys are in Key Vault as the secret `crm-api-keys`
  (portal → Key Vault → Secrets).
- **Power BI:** each processed call is also written as Parquet files (`calls`, `qa_scores`, `compliance`,
  `utterances`) to the storage account under `insights/curated/`. The Power BI model definitions are in `Analytics/`.

## Suggested demo walkthrough (15–20 minutes)

Prepare beforehand: check that the four demo calls show as completed (sign in as Admin and open **Pipeline**).
Expected results for each call are in [Data/demo_calls/README.md](../Data/demo_calls/README.md).

1. **The problem (1 min).** Supervisors manually review 1–2 % of calls; this system analyses every call.
2. **Overview as Kabir Khan, Supervisor (3 min).** KPI tiles; *Needs attention* lists DEMO-0002 (compliance
   breach) and DEMO-0003 (unhappy, repeat, escalated); top reasons; root causes; leaderboard.
3. **A compliance breach: DEMO-0002 (4 min).** Play the moment the agent asks for the CVV and OTP. Show that the
   card number is removed from the transcript, the failed compliance checks with quotes, the low QA score with
   *Why this rating*, and the coaching recommendations.
4. **A difficult call: DEMO-0003 (2 min).** Emotion from frustrated to disappointed, sentiment strip turning red,
   escalation path offered (passed), good empathy despite the call not being resolved.
5. **A sales call: DEMO-0004 (3 min).** Sales parameters are rated (BANT, objection handling, appointment, PM
   meeting); the hold is measured from the audio (*Measured from the recording*: a silence of about 13 s). Compare
   with DEMO-0001, where these parameters are N/A.
6. **Coaching (3 min).** **Agents** → open Vikram Singh. Walk through the 20-parameter evaluation table, the
   verdicts and recommendations, and the team-average markers.
7. **Agent's own view (1 min).** Sign out and sign in as **Vikram Singh (Agent)**: only his own call and
   **My scorecard**; the Agents, Insights, Pipeline and Settings pages are not available.
8. **Configurable (2 min).** Sign in as Asha Rao (Admin). In **Settings → QA rubric**, show that the parameters
   are the customer's own form, editable without code. Optionally upload a new recording on **Pipeline** and watch
   it process.
9. **Close.** Security (personal data removed before the AI sees the text; data stays in the customer's Azure
   subscription), CRM API, Power BI option, phase 2 (real-time agent assist).

## Questions customers often ask

| Question | Answer |
|---|---|
| Does the AI see customers' personal data? | No. Azure AI Language removes it before the transcript is sent to Azure OpenAI. Only the redacted text is stored for the web app. |
| Where is the data stored? | In the customer's own Azure subscription and region (Central India for this demo). Azure OpenAI does not use the data to train models. |
| How accurate is the scoring? | Every rating comes with the quote and the reasoning behind it, so a QA analyst can check it. Accuracy should be measured against human QA on a sample of real calls during a pilot. |
| Can we use our own QA form? | Yes. This demo already uses the customer's 20-parameter form. Admins change parameters, weights and the pass mark in Settings. |
| Hindi or Hinglish calls? | English only in this version; multilingual support is planned. |
| Real-time help for agents during the call? | Phase 2; this version analyses calls after they end. |
| How long does a call take to process? | Usually 1–3 minutes after the recording arrives. |
| What does it cost to run? | The demo environment is sized at the minimum. The AI services are billed per use (audio hours, text records, tokens), so cost scales with call volume. |

## Troubleshooting

| Symptom | What to do |
|---|---|
| The site takes about 30 seconds to load the first time | Normal after a restart or a period without use |
| "No calls yet" or calls missing | Check the date range: try **All**. Then check **Pipeline** for calls still in progress or failed |
| A call is stuck in progress for more than 10 minutes, or failed | Open it and read the error, then click **Retry**. In the Azure portal, the Function app (`…-func`) → *Log stream* shows the details |
| An agent demo user is missing from the sign-in list | That agent's calls have not finished processing yet |
| "Too many wrong passwords" | Wait 15 minutes |
| Scores differ slightly after re-analysing | Expected: the language model can vary a little between runs |
| An "Application Error" page | Azure portal → the web app (`…-web`) → *Log stream*, and send the error to the team that deployed it |
