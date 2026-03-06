param(
    [Parameter(Mandatory = $false)]
    [string]$ConfigFile = "./.env",

    [Parameter(Mandatory = $false)]
    [string]$NamePrefix = "ri-poc",

    [Parameter(Mandatory = $false)]
    [string]$DatabricksResourceAppId = "2ff814a6-3304-4ab8-85cb-cd0e6f879c1d"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Read-EnvFile {
    param([string]$Path)
    $values = @{}
    if (-not (Test-Path $Path)) { return $values }
    Get-Content $Path | ForEach-Object {
        if ([string]::IsNullOrWhiteSpace($_) -or $_.Trim().StartsWith('#')) { return }
        $parts = $_.Split('=', 2)
        if ($parts.Count -eq 2) {
            $values[$parts[0].Trim()] = $parts[1].Trim()
        }
    }
    return $values
}

function New-AppIfMissing {
    param(
        [string]$DisplayName,
        [string]$IdentifierUri
    )

    $existing = az ad app list --display-name $DisplayName --query "[0]" | ConvertFrom-Json
    if ($null -ne $existing) {
        Write-Host "Found existing app: $DisplayName"
        return $existing
    }

    $createArgs = @(
        'ad', 'app', 'create',
        '--display-name', $DisplayName,
        '--sign-in-audience', 'AzureADMyOrg'
    )

    if (-not [string]::IsNullOrWhiteSpace($IdentifierUri)) {
        $createArgs += @('--identifier-uris', $IdentifierUri)
    }

    $created = az @createArgs | ConvertFrom-Json
    Write-Host "Created app: $DisplayName"
    return $created
}

$envValues = Read-EnvFile -Path $ConfigFile
$tenantId = $envValues['AZURE_TENANT_ID']

if ([string]::IsNullOrWhiteSpace($tenantId)) {
    throw "AZURE_TENANT_ID must be set in $ConfigFile"
}

az account show 1>$null

$mcpApiName = "$NamePrefix-mcp-api"
$brokerName = "$NamePrefix-auth-broker"
$copilotClientName = "$NamePrefix-copilot-client"

$mcpIdentifierUri = "api://$mcpApiName"

$mcpApp = New-AppIfMissing -DisplayName $mcpApiName -IdentifierUri $mcpIdentifierUri
$brokerApp = New-AppIfMissing -DisplayName $brokerName -IdentifierUri ""
$copilotClientApp = New-AppIfMissing -DisplayName $copilotClientName -IdentifierUri ""

# Ensure service principals exist
az ad sp create --id $mcpApp.appId 1>$null
az ad sp create --id $brokerApp.appId 1>$null
az ad sp create --id $copilotClientApp.appId 1>$null

# Create delegated scope for MCP API
$scopeId = [guid]::NewGuid().ToString()
$apiPatch = @{
  api = @{
    requestedAccessTokenVersion = 2
    oauth2PermissionScopes = @(
      @{
        adminConsentDescription = 'Access Revenue Intelligence MCP API as signed-in user.'
        adminConsentDisplayName = 'Access Revenue MCP API'
        id = $scopeId
        isEnabled = $true
        type = 'User'
        userConsentDescription = 'Allow app to access Revenue MCP API on your behalf.'
        userConsentDisplayName = 'Access Revenue MCP API'
        value = 'access_as_user'
      }
    )
  }
} | ConvertTo-Json -Depth 6

az ad app update --id $mcpApp.id --set "api=$($apiPatch | ConvertFrom-Json | Select-Object -ExpandProperty api | ConvertTo-Json -Compress)" 1>$null

# Configure copilot client required resource access for MCP scope
$mcpScope = az ad app show --id $mcpApp.appId --query "api.oauth2PermissionScopes[?value=='access_as_user'].id | [0]" -o tsv

$requiredResourceAccess = @(
  @{
    resourceAppId = $mcpApp.appId
    resourceAccess = @(
      @{
        id = $mcpScope
        type = 'Scope'
      }
    )
  }
) | ConvertTo-Json -Depth 5 -Compress

az ad app update --id $copilotClientApp.id --required-resource-accesses $requiredResourceAccess 1>$null

# Configure broker delegated access to Azure Databricks
$databricksSp = az ad sp list --filter "appId eq '$DatabricksResourceAppId'" --query "[0]" | ConvertFrom-Json
if ($null -eq $databricksSp) {
    throw "Could not find Databricks service principal for appId $DatabricksResourceAppId"
}

$databricksScopeId = ($databricksSp.oauth2PermissionScopes | Where-Object { $_.value -eq 'user_impersonation' } | Select-Object -First 1).id

$brokerRequiredResourceAccess = @(
  @{
    resourceAppId = $DatabricksResourceAppId
    resourceAccess = @(
      @{
        id = $databricksScopeId
        type = 'Scope'
      }
    )
  }
) | ConvertTo-Json -Depth 5 -Compress

az ad app update --id $brokerApp.id --required-resource-accesses $brokerRequiredResourceAccess 1>$null

# Create broker secret
$brokerSecret = az ad app credential reset --id $brokerApp.id --append --display-name "poc-broker-secret" --years 1 | ConvertFrom-Json

Write-Host "\n=== App Registration Outputs ==="
Write-Host "MCP API AppId: $($mcpApp.appId)"
Write-Host "MCP Identifier URI: $mcpIdentifierUri"
Write-Host "Broker AppId: $($brokerApp.appId)"
Write-Host "Copilot Client AppId: $($copilotClientApp.appId)"
Write-Host "Broker Client Secret (save now): $($brokerSecret.password)"
Write-Host "\nGrant admin consent for Copilot client -> MCP scope and Broker -> Databricks user_impersonation in Entra portal if not already granted."
