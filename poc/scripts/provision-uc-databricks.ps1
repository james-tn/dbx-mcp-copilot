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
        if ($parts.Count -eq 2) { $values[$parts[0].Trim()] = $parts[1].Trim().Trim('"') }
    }
    return $values
}

function Update-EnvValue {
    param([string]$Path, [string]$Key, [string]$Value)
    $content = Get-Content $Path
    $updated = $false
    $newContent = foreach ($line in $content) {
        if ($line -match "^$([regex]::Escape($Key))=") {
            $updated = $true
            "$Key=$Value"
        } else {
            $line
        }
    }
    if (-not $updated) { $newContent += "$Key=$Value" }
    Set-Content -Path $Path -Value $newContent -Encoding UTF8
}

function Invoke-DbxStatement {
    param(
        [string]$DatabricksHost,
        [string]$Token,
        [string]$WarehouseId,
        [string]$Statement,
        [int]$MaxAttempts = 90
    )

    $headers = @{ Authorization = "Bearer $Token"; 'Content-Type' = 'application/json' }
    $body = @{ statement = $Statement; warehouse_id = $WarehouseId } | ConvertTo-Json -Depth 10
    $resp = Invoke-RestMethod -Method Post -Uri "$DatabricksHost/api/2.0/sql/statements" -Headers $headers -Body $body
    $statementId = $resp.statement_id

    for ($i = 0; $i -lt $MaxAttempts; $i++) {
        Start-Sleep -Seconds 2
        $status = Invoke-RestMethod -Method Get -Uri "$DatabricksHost/api/2.0/sql/statements/$statementId" -Headers @{ Authorization = "Bearer $Token" }
        $state = $status.status.state
        if ($state -notin @('PENDING', 'RUNNING')) {
            if ($state -ne 'SUCCEEDED') {
                $err = $status.status.error.message
                throw "Statement failed: $Statement`nError: $err"
            }
            return $status
        }
    }

    throw "Timed out waiting for statement completion."
}

$env = Read-EnvFile -Path $ConfigFile

$subscriptionId = $env['AZURE_SUBSCRIPTION_ID']
$resourceGroup = $env['AZURE_RESOURCE_GROUP']
$location = $env['AZURE_LOCATION']
$workspaceName = $env['DATABRICKS_WORKSPACE_NAME']
$workspaceSku = $env['DATABRICKS_WORKSPACE_SKU']
$managedRgName = $env['DATABRICKS_MANAGED_RG_NAME']
$uccName = $env['DATABRICKS_ACCESS_CONNECTOR_NAME']

if ([string]::IsNullOrWhiteSpace($subscriptionId)) { throw 'AZURE_SUBSCRIPTION_ID is required in .env' }
if ([string]::IsNullOrWhiteSpace($resourceGroup)) { throw 'AZURE_RESOURCE_GROUP is required in .env' }
if ([string]::IsNullOrWhiteSpace($workspaceName)) { throw 'DATABRICKS_WORKSPACE_NAME is required in .env' }
if ([string]::IsNullOrWhiteSpace($workspaceSku)) { $workspaceSku = 'premium' }
if ([string]::IsNullOrWhiteSpace($managedRgName)) { $managedRgName = "$workspaceName-mrg" }
if ([string]::IsNullOrWhiteSpace($uccName)) { $uccName = "$workspaceName-ucc" }

az account set --subscription $subscriptionId
az group create --name $resourceGroup --location $location 1>$null

Write-Host 'Creating Databricks Access Connector...'
az databricks access-connector create --name $uccName --resource-group $resourceGroup --location $location --identity-type SystemAssigned 1>$null

Write-Host 'Creating Premium Databricks workspace...'
az databricks workspace create --name $workspaceName --resource-group $resourceGroup --location $location --sku $workspaceSku --managed-resource-group $managedRgName 1>$null

$workspace = az databricks workspace show --name $workspaceName --resource-group $resourceGroup | ConvertFrom-Json
$dbxHost = "https://$($workspace.workspaceUrl)"
$workspaceCatalog = ($workspaceName -replace '-', '_').ToLower()
Update-EnvValue -Path $ConfigFile -Key 'DATABRICKS_SERVER_HOSTNAME' -Value $workspace.workspaceUrl

Write-Host "Workspace ready: $dbxHost"

$dbxToken = az account get-access-token --resource 2ff814a6-3304-4ab8-85cb-cd0e6f879c1d --query accessToken -o tsv

$warehouseName = 'ri-poc-uc-warehouse'
$warehouseId = ''

$existingWh = Invoke-RestMethod -Method Get -Uri "$dbxHost/api/2.0/sql/warehouses" -Headers @{ Authorization = "Bearer $dbxToken" }
$match = $existingWh.warehouses | Where-Object { $_.name -eq $warehouseName } | Select-Object -First 1
if ($null -ne $match) {
    $warehouseId = $match.id
    Write-Host "Found existing warehouse: $warehouseName ($warehouseId)"
} else {
    Write-Host 'Creating SQL warehouse...'
    $createBody = @{
        name = $warehouseName
        cluster_size = '2X-Small'
        min_num_clusters = 1
        max_num_clusters = 1
        auto_stop_mins = 10
        enable_photon = $true
        warehouse_type = 'PRO'
    } | ConvertTo-Json -Depth 8

    $created = Invoke-RestMethod -Method Post -Uri "$dbxHost/api/2.0/sql/warehouses" -Headers @{ Authorization = "Bearer $dbxToken"; 'Content-Type'='application/json' } -Body $createBody
    $warehouseId = $created.id
}

for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Seconds 5
    $wh = Invoke-RestMethod -Method Get -Uri "$dbxHost/api/2.0/sql/warehouses/$warehouseId" -Headers @{ Authorization = "Bearer $dbxToken" }
    if ($wh.state -eq 'RUNNING') { break }
    if ($wh.state -eq 'STOPPED') {
        Invoke-RestMethod -Method Post -Uri "$dbxHost/api/2.0/sql/warehouses/$warehouseId/start" -Headers @{ Authorization = "Bearer $dbxToken" } 1>$null
    }
}

$httpPath = "/sql/1.0/warehouses/$warehouseId"
Update-EnvValue -Path $ConfigFile -Key 'DATABRICKS_HTTP_PATH' -Value $httpPath

Write-Host "Warehouse ready: $warehouseId"

Write-Host 'Checking Unity Catalog readiness...'
$ucReady = $false
$ucError = ''
try {
    $check = Invoke-DbxStatement -DatabricksHost $dbxHost -Token $dbxToken -WarehouseId $warehouseId -Statement "SELECT is_account_group_member('grp_revenue_na') AS uc_check" -MaxAttempts 40
    $ucReady = $true
} catch {
    $ucError = $_.Exception.Message
}

if (-not $ucReady) {
    Write-Warning 'Unity Catalog is not fully enabled on this workspace/warehouse yet.'
    Write-Warning 'Complete Databricks account-level metastore assignment, then rerun this script.'
    Write-Warning $ucError
    exit 2
}

Write-Host 'UC check passed. Seeding UC dataset...'
$sqlText = Get-Content 'poc/scripts/seed-databricks-revenue-uc.sql' -Raw
$sqlText = $sqlText.Replace('__CATALOG__', $workspaceCatalog)
$sqlLines = ($sqlText -split "`n") | Where-Object { -not $_.Trim().StartsWith('--') }
$sqlJoined = $sqlLines -join "`n"
$statements = @()
foreach ($chunk in ($sqlJoined -split ';')) {
    $t = $chunk.Trim()
    if ($t) { $statements += $t }
}

$idx = 0
foreach ($statement in $statements) {
    $idx++
    Write-Host "Executing seed statement $idx/$($statements.Count)..."
    Invoke-DbxStatement -DatabricksHost $dbxHost -Token $dbxToken -WarehouseId $warehouseId -Statement $statement -MaxAttempts 120 | Out-Null
}

$count = Invoke-DbxStatement -DatabricksHost $dbxHost -Token $dbxToken -WarehouseId $warehouseId -Statement "SELECT count(*) AS c FROM $workspaceCatalog.ri_poc.fact_revenue"
$dateRange = Invoke-DbxStatement -DatabricksHost $dbxHost -Token $dbxToken -WarehouseId $warehouseId -Statement "SELECT min(date_key), max(date_key), count(*) FROM $workspaceCatalog.ri_poc.dim_date"

Update-EnvValue -Path $ConfigFile -Key 'MCP_ALLOWED_SCHEMA' -Value "$workspaceCatalog.ri_poc"

Write-Host "UC data seed complete. fact_revenue_rows=$($count.result.data_array[0][0])"
Write-Host "date_range=$($dateRange.result.data_array[0][0])..$($dateRange.result.data_array[0][1]) months=$($dateRange.result.data_array[0][2])"
