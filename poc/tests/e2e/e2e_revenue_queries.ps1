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

$uri = "$($McpUrl.TrimEnd('/'))/mcp/tools/ask_revenue_intelligence"
$headers = @{
    Authorization = "Bearer $UserToken"
    'Content-Type' = 'application/json'
}

$body = @{
    question = $Question
} | ConvertTo-Json

$response = Invoke-RestMethod -Method Post -Uri $uri -Headers $headers -Body $body

Write-Host "Generated SQL:"
Write-Host $response.generated_sql
Write-Host "Rows: $($response.row_count)"
$response.rows | ConvertTo-Json -Depth 5
