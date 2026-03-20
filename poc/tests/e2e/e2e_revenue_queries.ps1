param(
    [Parameter(Mandatory = $true)]
    [string]$McpUrl,

    [Parameter(Mandatory = $true)]
    [string]$UserToken,

    [Parameter(Mandatory = $false)]
    [string]$Question = "What is net revenue and ARR by region for Q1?"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$uri = "$($McpUrl.TrimEnd('/'))/mcp"
$headers = @{
    Authorization = "Bearer $UserToken"
    'Content-Type' = 'application/json'
    Accept = 'application/json, text/event-stream'
}

$body = @{
    jsonrpc = '2.0'
    id = 1
    method = 'tools/call'
    params = @{
        name = 'revenue_performance_expert'
        arguments = @{
            question = $Question
        }
    }
} | ConvertTo-Json

$response = Invoke-WebRequest -Method Post -Uri $uri -Headers $headers -Body $body
$rawContent = [string]$response.Content
$dataLine = ($rawContent -split "`r?`n" | Where-Object { $_ -like 'data: *' } | Select-Object -First 1)

if ([string]::IsNullOrWhiteSpace($dataLine)) {
    throw "Could not parse MCP response payload. Raw response: $rawContent"
}

$payload = $dataLine.Substring(6) | ConvertFrom-Json

if ($payload.result.isError) {
    $errorText = ($payload.result.content | Where-Object { $_.type -eq 'text' } | Select-Object -First 1).text
    throw "MCP tool returned error: $errorText"
}

$result = $payload.result.structuredContent

Write-Host "Generated SQL:"
Write-Host $result.generated_sql
Write-Host "Rows: $($result.row_count)"
$result.rows | ConvertTo-Json -Depth 5
