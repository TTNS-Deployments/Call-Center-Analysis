# Security

## Reporting a vulnerability

Do **not** open a public issue for security problems. Raise a ticket marked *Security* in the DBCH support portal (see [SUPPORT.md](SUPPORT.md)), or contact the TTNS service delivery team directly. Include the steps to reproduce and the potential impact.

## How the solution protects call data

| Area | Control |
|---|---|
| Identity | Users sign in with Microsoft Entra ID; access is role-based (Admin, Supervisor, QA, Agent). Agents only see their own calls. |
| Service access | Functions and the web app use **system-assigned managed identities** with least-privilege RBAC roles. There are no service keys in app settings. |
| Secrets | The Django secret, the Entra client secret and the CRM API key are stored in **Key Vault** and read through Key Vault references. |
| PII | Transcripts are redacted by Azure AI Language **before** Azure OpenAI is called. The UI, Cosmos DB, Parquet, Power BI and the CRM API only receive redacted text. |
| Raw data | Recordings and raw transcripts are kept in private containers, with lifecycle rules for tiering and deletion (configurable per environment). |
| Transport | HTTPS only, TLS 1.2 minimum, secure cookies, HSTS and CSRF protection on the web app. |
| Monitoring | Application Insights and Log Analytics for both apps. Microsoft Defender for Cloud is **not** enabled by the template: it is a subscription-level setting, so enable the plans you need yourself (see the deployment guide, step 8 of "After deployment"). |

Out of scope per the SOW: DDoS/WAF management and security or legal compliance audits.
