# Daily Account Planner C# Wrapper Pilot

This is a minimal side-by-side C# pilot for wrapper behavior comparison.

It intentionally keeps scope small:

- `GET /healthz`
- `POST /api/messages`
- fast in-turn message replies
- delayed path that queues work and sends via proactive continuation (for comparison)

## Environment placeholders

The service accepts the same core env names used by the Python wrapper where practical:

- `BOT_APP_ID` (or `MicrosoftAppId`)
- `BOT_APP_PASSWORD` (or `MicrosoftAppPassword`)
- `AZURE_TENANT_ID` (or `MicrosoftAppTenantId`)
- `PLANNER_SERVICE_BASE_URL`
- `PLANNER_API_EXPECTED_AUDIENCE`
- `PLANNER_API_SCOPE`
- `WRAPPER_FORWARD_TIMEOUT_SECONDS`
- `WRAPPER_LONG_RUNNING_ACK_THRESHOLD_SECONDS`
- `WRAPPER_ENABLE_LONG_RUNNING_MESSAGES`

## Local run

```bash
cd mvp/m365_wrapper_csharp
dotnet restore
dotnet run
```

Service defaults to `http://localhost:3978`.

## Docker build

From repo root:

```bash
docker build -f mvp/m365_wrapper_csharp/Dockerfile -t daily-account-planner-wrapper-csharp-pilot mvp
docker run --rm -p 3978:3978 daily-account-planner-wrapper-csharp-pilot
```

## Delay command

Send `/delay <seconds> <text>` to trigger the delayed path.

Example:

```text
/delay 15 summarize key account focus
```
