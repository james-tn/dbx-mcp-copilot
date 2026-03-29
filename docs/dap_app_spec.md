# Daily Account Planner (DAP) API — Technical Documentation  

> Status: reference-only document.
> It describes the DAP-compatible downstream API pattern and simulator path,
> not the current planner service API that powers this repo's main ACA runtime.
  
**Version:** 1.0.0    
**Production Status:** Production    
**Last Updated:** 2026-03-24    
**Contact:** b.cosley@veeam.com    
  
---  
  
# Table of Contents  
  
1. Daily Account Planner (DAP) API — Technical Documentation    
   1.1 Overview    
   1.2 Architecture Position    
   1.3 Base URL    
   1.4 Authentication and Authorization    
   - 1.4.1 Token Headers    
   - 1.4.2 Endpoint Authentication Requirements    
   - 1.4.3 OBO Pattern (On-Behalf-Of)    
   1.5 Endpoints    
   - 1.5.1 GET /api/v1/healthcheck    
   - 1.5.2 POST /api/v1/accounts/query    
   - 1.5.3 POST /api/v1/debug/headers    
   1.6 Data Access Documentation    
   - 1.6.1 Tables Queried    
   - 1.6.2 Access Method    
   - 1.6.3 SQL Query — POST /api/v1/accounts/query    
   - 1.6.4 Unity Catalog and Permissions    
   1.7 Error Codes    
   - 1.7.1 Error Response Examples    
   1.8 Integration Guide — Copilot Studio / Power Platform    
   - 1.8.1 Recommended Connector Configuration    
   - 1.8.2 Recommended Integration Workflow    
   - 1.8.3 Integration Checklist    
   1.9 Deployment Details    
  
---  
  
# 1. Overview  
  
The **Daily Account Planner (DAP) API** is a FastAPI microservice deployed on **Databricks Apps (Azure Databricks)**.  
  
It surfaces **account intelligence data** to support a sales planning agent that identifies the **highest priority accounts for net-new prospecting**, specifically those with **no open Salesforce opportunities**, ranked using AI-generated scores.  
  
**Intended consumers**  
  
- Copilot Studio    
- Power Platform    
- M365 Agent SDK wrapper    
- Azure Container Apps    
  
---  
  
# 1.2 Architecture Position  
  
## System Context  
  
```  
Copilot Studio / Power Platform  
    │  
    │ (OAuth Bearer token via M365 Agent SDK)  
    ▼  
M365 Wrapper / Gateway (protocol adapter)  
    │  
    │ (forwards Authorization or X-Forwarded-Access-Token header)  
    ▼  
DAP Agent Service (this API)  
(business logic, runs on Databricks Apps)  
    │  
    │ (Databricks SQL REST API v2.0, OBO token)  
    ▼  
Azure Databricks SQL Warehouse  
Unity Catalog (prod_catalog)  
```  
  
The API follows an **On-Behalf-Of (OBO)** pattern. It extracts the caller’s Bearer token and forwards it to Databricks so **Unity Catalog enforces permissions based on the caller’s Azure identity**, not a service account.  
  
---  
  
# 1.3 Base URL  
  
The base URL is the **Databricks Apps deployment URL assigned at provisioning time**.  
  
All endpoint paths below are relative to that base.  
  
Obtain the deployed URL from the **Databricks Apps console**.  
  
---  
  
# 1.4 Authentication and Authorization  
  
## 1.4.1 Token Headers  
  
The API accepts a Bearer token via two headers, checked in priority order.  
  
| Priority | Header | Format | Notes |  
|---|---|---|---|  
| 1 (preferred) | Authorization | Bearer token | Standard HTTP Bearer scheme |  
| 2 (fallback) | X-Forwarded-Access-Token | token | Used when a gateway strips the Authorization header |  
  
If neither header is present on a protected endpoint, the API returns:  
  
```  
HTTP 401 Unauthorized  
```  
  
The consuming application is responsible for acquiring a **valid Azure Entra ID OAuth 2.0 token** and including it in requests.  
  
---  
  
## 1.4.2 Endpoint Authentication Requirements  
  
| Endpoint | Auth Required |  
|---|---|  
| GET /api/v1/healthcheck | No |  
| POST /api/v1/accounts/query | Yes — Bearer token required |  
| POST /api/v1/debug/headers | No |  
  
---  
  
## 1.4.3 OBO Pattern (On-Behalf-Of)  
  
The recommended authentication pattern is **Azure AD On-Behalf-Of (OBO) flow**.  
  
The **M365 wrapper** should acquire a token on behalf of the authenticated user and forward it to the DAP API.  
  
The API then uses the same token when querying Databricks so **Unity Catalog permissions apply to the end user** rather than a shared service account.  
  
---  
  
# 1.5 Endpoints  
  
---  
  
# 1.5.1 GET /api/v1/healthcheck  
  
**Purpose**  
  
Confirms the service is running and reachable.    
Useful as a **pre-flight connectivity check**.  
  
**Method**  
  
```  
GET  
```  
  
**Path**  
  
```  
/api/v1/healthcheck  
```  
  
**Authentication**  
  
None required.  
  
## Request  
  
No parameters or request body.  
  
## Response — 200 OK  
  
Example  
  
```json  
{  
  "status": "OK",  
  "timestamp": "2026-03-24T14:30:00.123456+00:00"  
}  
```  
  
Field descriptions:  
  
| Field | Type | Description |  
|---|---|---|  
| status | string | Always "OK" when healthy |  
| timestamp | string (ISO 8601 UTC) | Server time of response |  
  
---  
  
# 1.5.2 POST /api/v1/accounts/query  
  
**Purpose**  
  
Returns a ranked list of accounts for a given sales team that **have no open Salesforce opportunities**, sorted by **AI-generated scores**.  
  
**Method**  
  
```  
POST  
```  
  
**Path**  
  
```  
/api/v1/accounts/query  
```  
  
**Authentication**  
  
Bearer token required.  
  
---  
  
## Request Headers  
  
```  
Authorization: Bearer [token]  
Content-Type: application/json  
```  
  
---  
  
## Request Body  
  
Example  
  
```json  
{  
  "sales_team": "ENT-APAC-01",  
  "row_limit": 50  
}  
```  
  
| Field | Type | Required | Default | Constraints | Description |  
|---|---|---|---|---|---|  
| sales_team | string | Yes | — | Non-empty | Sales team identifier |  
| row_limit | integer | No | 20 | Min:1 Max:5000 | Max rows returned |  
  
---  
  
## Response — 200 OK  
  
Example  
  
```json  
{  
  "sales_team": "ENT-APAC-01",  
  "row_count": 2,  
  "rows": [  
    {  
      "account_id": "0011a00000XYZ001",  
      "account_name": "Contoso Ltd",  
      "need": 0.94,  
      "intent": 0.87,  
      "xf_score": 0.91  
    },  
    {  
      "account_id": "0011a00000XYZ002",  
      "account_name": "Fabrikam Inc",  
      "need": 0.88,  
      "intent": 0.79,  
      "xf_score": 0.85  
    }  
  ]  
}  
```  
  
### Response Fields  
  
| Field | Type | Description |  
|---|---|---|  
| sales_team | string | Echo of request value |  
| row_count | integer | Number of rows returned |  
| rows[].account_id | string | Salesforce Account ID |  
| rows[].account_name | string | Account name |  
| rows[].need | float | Need score |  
| rows[].intent | float | Intent score |  
| rows[].xf_score | float | Cross-fit score |  
  
---  
  
## Ranking and Filtering Logic  
  
Results are sorted descending by:  
  
1. need    
2. intent    
3. xf_score    
  
Automatic filters applied:  
  
| Filter | Condition | Rationale |  
|---|---|---|  
| Sales team scope | s.sales_team = :sales_team | Returns only that team's accounts |  
| No open opportunities | a.HasAnyOpps__c = false | Focus on net-new prospecting |  
| Current records only | a.__END_AT IS NULL | Excludes historical records |  
  
---  
  
## Note on `sales_team` Values  
  
The `sales_team` parameter must match values in:  
  
```  
prod_catalog.data_science_account_iq_gold.account_iq_scores  
```  
  
The agent should map the authenticated user’s identity to their **sales team identifier**.  
  
Contact **b.cosley@veeam.com** for the canonical list.  
  
---  
  
# 1.5.3 POST /api/v1/debug/headers  
  
**Purpose**  
  
Diagnostic endpoint returning metadata about incoming headers to confirm **token forwarding**.  
  
**Method**  
  
```  
POST  
```  
  
**Path**  
  
```  
/api/v1/debug/headers  
```  
  
**Authentication**  
  
None required.  
  
---  
  
## Response — 200 OK  
  
Example  
  
```json  
{  
  "has_authorization": true,  
  "authorization_prefix": "Bearer eyJhbGciOiJSU",  
  "x_ms_client_principal": null,  
  "all_header_keys": [  
    "authorization",  
    "content-type",  
    "host",  
    "user-agent"  
  ]  
}  
```  
  
| Field | Type | Description |  
|---|---|---|  
| has_authorization | boolean | Whether Authorization header was present |  
| authorization_prefix | string/null | First 20 characters of Authorization header |  
| x_ms_client_principal | string/null | Azure injected header |  
| all_header_keys | string[] | List of request headers |  
  
---  
  
## Production Security Note  
  
This endpoint exposes header metadata and should be **disabled or access-controlled in production**.  
  
---  
  
# 1.6 Data Access Documentation  
  
## 1.6.1 Tables Queried  
  
| Table | Catalog | Schema | Description |  
|---|---|---|---|  
| account_iq_scores | prod_catalog | data_science_account_iq_gold | AI-generated account scores |  
| account | prod_catalog | salesforce_bronze | Raw Salesforce account data |  
  
---  
  
## 1.6.2 Access Method  
  
| Property | Value |  
|---|---|  
| Protocol | Databricks SQL REST API v2.0 |  
| Warehouse | SQL Warehouse (ID: be160f1edb836d88) |  
| HTTP Path | /sql/1.0/warehouses/be160f1edb836d88 |  
| Auth | OBO Bearer token |  
| Result format | JSON |  
| Timeout | 30 seconds |  
| SQL injection prevention | Parameterized queries |  
  
---  
  
## 1.6.3 SQL Query — POST /api/v1/accounts/query  
  
```sql  
SELECT  
  s.account_id,  
  s.account_name,  
  s.need,  
  s.intent,  
  s.xf_score  
FROM prod_catalog.data_science_account_iq_gold.account_iq_scores s  
JOIN prod_catalog.salesforce_bronze.account a  
  ON s.account_id = a.Id  
WHERE  
  s.sales_team = :sales_team  
  AND a.HasAnyOpps__c = false  
  AND a.`__END_AT` IS NULL  
ORDER BY  
  s.need DESC,  
  s.intent DESC,  
  s.xf_score DESC  
LIMIT :row_limit  
```  
  
Parameters:  
  
| Parameter | Source |  
|---|---|  
| :sales_team | Request body |  
| :row_limit | Request body (default 20) |  
  
---  
  
## 1.6.4 Unity Catalog and Permissions  
  
Access to `prod_catalog` tables is governed by **Databricks Unity Catalog**.  
  
Because the API forwards the caller’s Bearer token:  
  
- Unity Catalog evaluates permissions using the **caller’s identity**.  
  
If the caller lacks `SELECT` permission, Databricks returns an authorization error which the API forwards upstream.  
  
---  
  
# 1.7 Error Codes  
  
| HTTP Status | Condition | Response |  
|---|---|---|  
| 200 | Success | Endpoint payload |  
| 401 | Missing bearer token | `"detail": "Missing bearer token"` |  
| 422 | Validation failure | FastAPI validation response |  
| 4xx / 5xx | Databricks error | Forwarded upstream |  
| 500 | Parsing failure | `"detail": "Failed to parse Databricks result"` |  
  
---  
  
## 1.7.1 Error Response Examples  
  
### 401 Missing Token  
  
```json  
{  
  "detail": "Missing bearer token"  
}  
```  
  
### 422 Missing Field  
  
```json  
{  
  "detail": [  
    {  
      "type": "missing",  
      "loc": ["body", "sales_team"],  
      "msg": "Field required",  
      "input": {}  
    }  
  ]  
}  
```  
  
### 422 row_limit Invalid  
  
```json  
{  
  "detail": [  
    {  
      "type": "greater_than_equal",  
      "loc": ["body", "row_limit"],  
      "msg": "Input should be greater than or equal to 1",  
      "input": 0  
    }  
  ]  
}  
```  
  
### 500 Databricks Parse Failure  
  
```json  
{  
  "detail": "Failed to parse Databricks result"  
}  
```  
  
---  
  
# 1.8 Integration Guide — Copilot Studio / Power Platform  
  
## 1.8.1 Recommended Connector Configuration  
  
- **Base URL:** Databricks Apps deployment URL    
- **Security scheme:** OAuth 2.0    
- **Header**  
  
```  
Authorization: Bearer {{token}}  
```  
  
Fallback  
  
```  
X-Forwarded-Access-Token: {{token}}  
```  
  
---  
  
## 1.8.2 Recommended Integration Workflow  
  
1. Health check — call `/api/v1/healthcheck`  
2. Authenticate — obtain Azure Entra ID token  
3. Map sales team — resolve user identity → sales_team  
4. Query accounts — call `/api/v1/accounts/query`  
5. Present results — render in Copilot or Power Platform UI  
  
---  
  
## 1.8.3 Integration Checklist  
  
- Base URL configured    
- Authorization header configured    
- OBO flow configured in M365 wrapper    
- sales_team mapped from user identity    
- healthcheck used for connectivity test    
- debug/headers used for token validation    
- Token forwarding verified (`Bearer ey...`)    
- Unity Catalog permissions granted    
  
---  
  
# 1.9 Deployment Details  
  
| Property | Value |  
|---|---|  
| Framework | FastAPI |  
| Server | Uvicorn (ASGI) |  
| Startup command | `uvicorn app:app --host 0.0.0.0 --port 8000` |  
| Port | 8000 |  
| Databricks host | adb-1715711735713564.4.azuredatabricks.net |  
| Azure OpenAI endpoint | ds-llms-east-us-2.openai.azure.com |  
| Azure OpenAI deployment | llm-api-gpt41-1 |  
