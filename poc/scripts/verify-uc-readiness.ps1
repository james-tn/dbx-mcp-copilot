param(
    [Parameter(Mandatory = $false)]
    [string]$ConfigFile = "./.env"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Read-EnvFile {
    param([string]$Path)
    $values = @{}
    if (-not (Test-Path $Path)) { throw "Config file not found: $Path" }
    Get-Content $Path | ForEach-Object {
        if ([string]::IsNullOrWhiteSpace($_) -or $_.Trim().StartsWith('#')) { return }
        $parts = $_.Split('=', 2)
        if ($parts.Count -eq 2) {
            $values[$parts[0].Trim()] = $parts[1].Trim().Trim('"')
        }
    }
    return $values
}

$env = Read-EnvFile -Path $ConfigFile
$dbxHost = "https://$($env['DATABRICKS_SERVER_HOSTNAME'])"
$httpPath = $env['DATABRICKS_HTTP_PATH']
$warehouseId = ($httpPath -replace '^/sql/1.0/warehouses/', '')

if ([string]::IsNullOrWhiteSpace($dbxHost) -or [string]::IsNullOrWhiteSpace($warehouseId)) {
    throw "DATABRICKS_SERVER_HOSTNAME and DATABRICKS_HTTP_PATH must be set in $ConfigFile"
}

$token = az account get-access-token --resource 2ff814a6-3304-4ab8-85cb-cd0e6f879c1d --query accessToken -o tsv
$authHeaders = @{ Authorization = "Bearer $token" }

Write-Host "Checking metastore assignment endpoint..."
try {
    $meta = Invoke-RestMethod -Method Get -Uri "$dbxHost/api/2.1/unity-catalog/current-metastore-assignment" -Headers $authHeaders
    Write-Host "Metastore assignment found: $($meta.metastore_id)"
} catch {
    Write-Warning "Metastore assignment check failed. Workspace may not be UC-enabled yet."
}

Write-Host "Checking UC function on warehouse $warehouseId..."
$stmtBody = @{ statement = "SELECT is_account_group_member('grp_revenue_na') AS uc_check"; warehouse_id = $warehouseId } | ConvertTo-Json
$exec = Invoke-RestMethod -Method Post -Uri "$dbxHost/api/2.0/sql/statements" -Headers ($authHeaders + @{ 'Content-Type' = 'application/json' }) -Body $stmtBody
$statementId = $exec.statement_id

do {
    Start-Sleep -Seconds 2
    $status = Invoke-RestMethod -Method Get -Uri "$dbxHost/api/2.0/sql/statements/$statementId" -Headers $authHeaders
    $state = $status.status.state
} while ($state -in @('PENDING', 'RUNNING'))

if ($state -eq 'SUCCEEDED') {
    Write-Host "UC check passed. Warehouse is UC-enabled."
    exit 0
}

Write-Error "UC check failed: $($status.status.error.message)"
exit 1
