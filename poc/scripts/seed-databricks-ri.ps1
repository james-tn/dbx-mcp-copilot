$ErrorActionPreference = 'Stop'

$dbxHost = 'https://adb-7405610222366876.16.azuredatabricks.net'
$warehouseId = $env:DBX_WAREHOUSE_ID
if (-not $warehouseId) {
    Write-Error "Set DBX_WAREHOUSE_ID environment variable to the SQL warehouse ID"
    exit 1
}

$token = az account get-access-token --resource 2ff814a6-3304-4ab8-85cb-cd0e6f879c1d --query accessToken -o tsv
$headers = @{ Authorization = "Bearer $token"; 'Content-Type' = 'application/json' }

function Invoke-DbxStatement {
    param([string]$Statement, [string]$Label = '')

    if ($Label) { Write-Host "  $Label..." -NoNewline }
    $body = @{ statement = $Statement; warehouse_id = $warehouseId } | ConvertTo-Json -Depth 5
    $response = Invoke-RestMethod -Method Post -Uri "$dbxHost/api/2.0/sql/statements" -Headers $headers -Body $body
    $statementId = $response.statement_id

    do {
        Start-Sleep -Seconds 2
        $status = Invoke-RestMethod -Method Get -Uri "$dbxHost/api/2.0/sql/statements/$statementId" -Headers @{ Authorization = "Bearer $token" }
        $state = $status.status.state
    } while ($state -in @('PENDING', 'RUNNING'))

    if ($state -ne 'SUCCEEDED') {
        $msg = $status.status.error.message
        throw "Statement failed: $Label`nError: $msg"
    }

    if ($Label) { Write-Host " done" }
    return $status
}

Write-Host "Revenue Intelligence — Databricks seed"
Write-Host "Workspace: $dbxHost"
Write-Host "Warehouse: $warehouseId"
Write-Host ""

# Read SQL file and split on semicolons
$sqlFile = Join-Path $PSScriptRoot 'seed-databricks-ri.sql'
$sqlContent = Get-Content $sqlFile -Raw -Encoding utf8

# Split into individual statements, skip comments and empty lines
$statements = $sqlContent -split ';\s*\n' | ForEach-Object { $_.Trim() } | Where-Object {
    $_ -and ($_ -notmatch '^\s*--')
}

Write-Host "Executing $($statements.Count) SQL statements..."
Write-Host ""

$i = 0
foreach ($stmt in $statements) {
    $i++
    # Extract a label from the first line
    $firstLine = ($stmt -split '\n')[0].Trim()
    if ($firstLine.Length -gt 80) { $firstLine = $firstLine.Substring(0, 77) + '...' }
    Invoke-DbxStatement -Statement $stmt -Label "[$i/$($statements.Count)] $firstLine"
}

Write-Host ""
Write-Host "Seed complete. Verifying row counts..."

$result = Invoke-DbxStatement -Statement @"
SELECT 'accounts' AS object_name, COUNT(*) AS row_count FROM veeam_demo.ri.accounts
UNION ALL SELECT 'reps', COUNT(*) FROM veeam_demo.ri.reps
UNION ALL SELECT 'opportunities', COUNT(*) FROM veeam_demo.ri.opportunities
UNION ALL SELECT 'contacts', COUNT(*) FROM veeam_demo.ri.contacts
UNION ALL SELECT 'secure_accounts', COUNT(*) FROM veeam_demo.ri_secure.accounts
UNION ALL SELECT 'secure_reps', COUNT(*) FROM veeam_demo.ri_secure.reps
UNION ALL SELECT 'secure_opportunities', COUNT(*) FROM veeam_demo.ri_secure.opportunities
UNION ALL SELECT 'secure_contacts', COUNT(*) FROM veeam_demo.ri_secure.contacts
"@

$result.result.data_array | ForEach-Object { Write-Host ('  ' + $_) }
Write-Host ""
Write-Host "Done."
