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

function Invoke-GraphApplicationPatch {
  param(
    [string]$ApplicationObjectId,
    [hashtable]$Body
  )

  $tempFile = Join-Path ([System.IO.Path]::GetTempPath()) ("graph-app-patch-" + [guid]::NewGuid().ToString() + ".json")
  try {
    $Body | ConvertTo-Json -Depth 10 | Set-Content -Path $tempFile -Encoding UTF8
    az rest --method PATCH --uri "https://graph.microsoft.com/v1.0/applications/$ApplicationObjectId" --headers "Content-Type=application/json" --body "@$tempFile" 1>$null
  }
  finally {
    if (Test-Path $tempFile) {
      Remove-Item $tempFile -Force
    }
  }
}

function Set-RequiredResourceAccess {
  param(
    [string]$ApplicationObjectId,
    [object[]]$RequiredResourceAccess
  )

  Invoke-GraphApplicationPatch -ApplicationObjectId $ApplicationObjectId -Body @{
    requiredResourceAccess = $RequiredResourceAccess
  }
}

function Ensure-ServicePrincipal {
  param([string]$AppId)

  $existing = az ad sp list --filter "appId eq '$AppId'" --query "[0].id" -o tsv
  if ([string]::IsNullOrWhiteSpace($existing)) {
    az ad sp create --id $AppId 1>$null
  }
}

function New-AppIfMissing {
    param(
        [string]$DisplayName,
    [bool]$ExposeAsApi = $false
    )

    $existing = az ad app list --display-name $DisplayName --query "[0]" | ConvertFrom-Json
    if ($null -ne $existing) {
        Write-Host "Found existing app: $DisplayName"
    if ($ExposeAsApi -and ($null -eq $existing.identifierUris -or $existing.identifierUris.Count -eq 0)) {
      $safeIdentifierUri = "api://$($existing.appId)"
      az ad app update --id $existing.id --identifier-uris $safeIdentifierUri 1>$null
      $existing = az ad app show --id $existing.id | ConvertFrom-Json
    }
        return $existing
    }

    $createArgs = @(
        'ad', 'app', 'create',
        '--display-name', $DisplayName,
        '--sign-in-audience', 'AzureADMyOrg'
    )

    $created = az @createArgs | ConvertFrom-Json

  if ($ExposeAsApi) {
    $safeIdentifierUri = "api://$($created.appId)"
    az ad app update --id $created.id --identifier-uris $safeIdentifierUri 1>$null
    $created = az ad app show --id $created.id | ConvertFrom-Json
  }

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

$mcpApp = New-AppIfMissing -DisplayName $mcpApiName -ExposeAsApi $true
$brokerApp = New-AppIfMissing -DisplayName $brokerName
$copilotClientApp = New-AppIfMissing -DisplayName $copilotClientName
$mcpIdentifierUri = $mcpApp.identifierUris[0]

# Ensure service principals exist
Ensure-ServicePrincipal -AppId $mcpApp.appId
Ensure-ServicePrincipal -AppId $brokerApp.appId
Ensure-ServicePrincipal -AppId $copilotClientApp.appId

# Create delegated scope for MCP API
$scopeId = [guid]::NewGuid().ToString()
$existingMcpScope = az ad app show --id $mcpApp.id --query "api.oauth2PermissionScopes[?value=='access_as_user'] | [0]" | ConvertFrom-Json
if ($null -eq $existingMcpScope) {
    Invoke-GraphApplicationPatch -ApplicationObjectId $mcpApp.id -Body @{
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
    }
}

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
)

Set-RequiredResourceAccess -ApplicationObjectId $copilotClientApp.id -RequiredResourceAccess $requiredResourceAccess

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
)

Set-RequiredResourceAccess -ApplicationObjectId $brokerApp.id -RequiredResourceAccess $brokerRequiredResourceAccess

# Create broker secret
$brokerSecret = az ad app credential reset --id $brokerApp.id --append --display-name "poc-broker-secret" --years 1 | ConvertFrom-Json

Write-Host "\n=== App Registration Outputs ==="
Write-Host "MCP API AppId: $($mcpApp.appId)"
Write-Host "MCP Identifier URI: $mcpIdentifierUri"
Write-Host "Broker AppId: $($brokerApp.appId)"
Write-Host "Copilot Client AppId: $($copilotClientApp.appId)"
Write-Host "Broker Client Secret (save now): $($brokerSecret.password)"
Write-Host "\nGrant admin consent for Copilot client -> MCP scope and Broker -> Databricks user_impersonation in Entra portal if not already granted."
