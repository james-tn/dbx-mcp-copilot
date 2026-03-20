param(
    [Parameter(Mandatory = $true)]
    [string]$AcrName,

    [Parameter(Mandatory = $false)]
    [string]$Tag = 'latest'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$brokerImage = "$AcrName.azurecr.io/ri-auth-broker:$Tag"
$mcpImage = "$AcrName.azurecr.io/ri-revenue-mcp:$Tag"

az acr build --registry $AcrName --image "ri-auth-broker:$Tag" ./services/auth-broker
az acr build --registry $AcrName --image "ri-revenue-mcp:$Tag" ./services/revenue-mcp

Write-Host "Built images:"
Write-Host $brokerImage
Write-Host $mcpImage
Write-Host "Update BROKER_IMAGE and MCP_IMAGE in .env before deploy if needed."
