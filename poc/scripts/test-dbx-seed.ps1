$ErrorActionPreference = 'Stop'

$dbxHost = 'https://adb-6619665651605575.15.azuredatabricks.net'
$warehouseId = '361f959f4a9a963a'

$token = az account get-access-token --resource 2ff814a6-3304-4ab8-85cb-cd0e6f879c1d --query accessToken -o tsv
$headers = @{ Authorization = "Bearer $token"; 'Content-Type' = 'application/json' }

function Invoke-DbxStatement {
    param([string]$Statement)

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
        throw "Statement failed: $Statement`nError: $msg"
    }

    return $status
}

Invoke-DbxStatement -Statement 'CREATE SCHEMA IF NOT EXISTS hive_metastore.ri_poc_test'
Invoke-DbxStatement -Statement 'CREATE OR REPLACE TABLE hive_metastore.ri_poc_test.fact_revenue (region STRING, product STRING, net_amount DOUBLE, arr_amount DOUBLE)'
Invoke-DbxStatement -Statement "INSERT OVERWRITE hive_metastore.ri_poc_test.fact_revenue VALUES ('NA','Analytics Pro',108000,95000),('EMEA','Analytics Pro',81000,70000),('NA','Predictive Insights',57000,48000),('EMEA','Predictive Insights',46000,39000)"
$result = Invoke-DbxStatement -Statement 'SELECT region, SUM(net_amount) AS net_revenue, SUM(arr_amount) AS arr FROM hive_metastore.ri_poc_test.fact_revenue GROUP BY region ORDER BY region'

Write-Host 'Databricks seed test succeeded.'
$result.result.data_array | ConvertTo-Json -Depth 5
