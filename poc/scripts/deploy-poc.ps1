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
            $values[$parts[0].Trim()] = $parts[1].Trim()
        }
    }
    return $values
}

$env = Read-EnvFile -Path $ConfigFile

$subscriptionId = $env['AZURE_SUBSCRIPTION_ID']
$resourceGroup = $env['AZURE_RESOURCE_GROUP']
$location = $env['AZURE_LOCATION']

if ([string]::IsNullOrWhiteSpace($subscriptionId) -or [string]::IsNullOrWhiteSpace($resourceGroup) -or [string]::IsNullOrWhiteSpace($location)) {
    throw "AZURE_SUBSCRIPTION_ID, AZURE_RESOURCE_GROUP, and AZURE_LOCATION are required in $ConfigFile"
}

az account set --subscription $subscriptionId
$rgExists = az group exists --name $resourceGroup
if ($rgExists -eq 'false') {
    az group create --name $resourceGroup --location $location 1>$null
}

$deploymentName = 'ri-poc-deploy'

$brokerImage = $env['BROKER_IMAGE']
if ([string]::IsNullOrWhiteSpace($brokerImage)) { $brokerImage = 'ghcr.io/your-org/ri-auth-broker:latest' }

$mcpImage = $env['MCP_IMAGE']
if ([string]::IsNullOrWhiteSpace($mcpImage)) { $mcpImage = 'ghcr.io/your-org/ri-revenue-mcp:latest' }

$acrLoginServer = ($brokerImage.Split('/')[0]).Trim()
$acrName = ($acrLoginServer.Split('.')[0]).Trim()
if ([string]::IsNullOrWhiteSpace($acrName)) {
    throw "Could not infer ACR name from BROKER_IMAGE: $brokerImage"
}

$acrCreds = az acr credential show --name $acrName | ConvertFrom-Json
$acrUsername = $acrCreds.username
$acrPassword = ($acrCreds.passwords | Select-Object -First 1).value

if ([string]::IsNullOrWhiteSpace($acrUsername) -or [string]::IsNullOrWhiteSpace($acrPassword)) {
    throw "Failed to retrieve ACR credentials for registry $acrName"
}

az deployment group create `
    --name $deploymentName `
  --resource-group $resourceGroup `
  --template-file ./infra/main.bicep `
  --parameters location=$location `
  --parameters brokerImage=$brokerImage `
  --parameters mcpImage=$mcpImage `
    --parameters acrLoginServer=$acrLoginServer `
    --parameters acrUsername=$acrUsername `
    --parameters acrPassword=$acrPassword `
  --parameters azureOpenAIEndpoint=$($env['AZURE_OPENAI_ENDPOINT']) `
  --parameters azureOpenAIDeployment=$($env['AZURE_OPENAI_DEPLOYMENT']) `
  --parameters databricksServerHostname=$($env['DATABRICKS_SERVER_HOSTNAME']) `
  --parameters databricksHttpPath=$($env['DATABRICKS_HTTP_PATH']) `
    --parameters createDatabricksWorkspace=$($env['CREATE_DATABRICKS_WORKSPACE']) `
    --parameters databricksWorkspaceName=$($env['DATABRICKS_WORKSPACE_NAME']) `
    --parameters databricksWorkspaceSku=$($env['DATABRICKS_WORKSPACE_SKU']) `
    --parameters databricksManagedResourceGroupName=$($env['DATABRICKS_MANAGED_RG_NAME']) `
    --parameters createDatabricksAccessConnector=$($env['CREATE_DATABRICKS_ACCESS_CONNECTOR']) `
    --parameters databricksAccessConnectorName=$($env['DATABRICKS_ACCESS_CONNECTOR_NAME']) `
  --parameters tenantId=$($env['AZURE_TENANT_ID']) `
  --parameters brokerClientId=$($env['BROKER_CLIENT_ID']) `
  --parameters brokerClientSecret=$($env['BROKER_CLIENT_SECRET']) `
  --parameters brokerExpectedAudience=$($env['BROKER_EXPECTED_AUDIENCE']) `
  --parameters brokerAllowedTenants=$($env['BROKER_ALLOWED_TENANTS']) `
  --parameters brokerSharedServiceKey=$($env['BROKER_SHARED_SERVICE_KEY']) `
  --parameters mcpAllowedSchema=$($env['MCP_ALLOWED_SCHEMA']) `
  --parameters mcpMaxRows=$($env['MCP_MAX_ROWS']) `
  --parameters mcpQueryTimeoutSeconds=$($env['MCP_QUERY_TIMEOUT_SECONDS'])

$brokerUrl = az deployment group show --resource-group $resourceGroup --name $deploymentName --query "properties.outputs.brokerUrl.value" -o tsv
$mcpUrl = az deployment group show --resource-group $resourceGroup --name $deploymentName --query "properties.outputs.mcpUrl.value" -o tsv
$dbxUrl = az deployment group show --resource-group $resourceGroup --name $deploymentName --query "properties.outputs.databricksWorkspaceUrl.value" -o tsv

Write-Host "\nDeployment complete"
Write-Host "Broker URL: $brokerUrl"
Write-Host "MCP URL: $mcpUrl"
if (-not [string]::IsNullOrWhiteSpace($dbxUrl)) {
    Write-Host "Databricks Workspace URL: $dbxUrl"
}
