param(
    [Parameter(Mandatory = $true)]
    [string]$GitHubOrg,

    [Parameter(Mandatory = $true)]
    [string]$GitHubRepo,

    [Parameter(Mandatory = $true)]
    [string]$SubscriptionId,

    [Parameter(Mandatory = $true)]
    [string]$TenantId,

    [Parameter(Mandatory = $true)]
    [string]$IntegrationScope,

    [Parameter(Mandatory = $true)]
    [string]$ProductionScope,

    [Parameter(Mandatory = $false)]
    [string]$IntegrationAcrScope = "",

    [Parameter(Mandatory = $false)]
    [string]$IntegrationKeyVaultScope = "",

    [Parameter(Mandatory = $false)]
    [string]$ProductionKeyVaultScope = "",

    [Parameter(Mandatory = $false)]
    [string]$BootstrapScope = "",

    [Parameter(Mandatory = $false)]
    [string]$IntegrationAppName = "gh-dbx-mcp-copilot-integration",

    [Parameter(Mandatory = $false)]
    [string]$ProductionAppName = "gh-dbx-mcp-copilot-production",

    [Parameter(Mandatory = $false)]
    [string]$BootstrapAppName = "gh-dbx-mcp-copilot-bootstrap-foundation",

    [Parameter(Mandatory = $false)]
    [switch]$IncludePullRequestSubjects
)

$ErrorActionPreference = "Stop"

if (-not $IntegrationAcrScope) {
    $IntegrationAcrScope = $IntegrationScope
}

function Ensure-AppRegistration {
    param([string]$DisplayName)

    $existing = az ad app list --display-name $DisplayName --query "[0].appId" -o tsv 2>$null
    if ($existing) {
        return $existing
    }

    return az ad app create `
        --display-name $DisplayName `
        --sign-in-audience AzureADMyOrg `
        --query appId `
        -o tsv
}

function Ensure-ServicePrincipal {
    param([string]$AppId)

    az ad sp show --id $AppId 1>$null 2>$null
    if ($LASTEXITCODE -ne 0) {
        az ad sp create --id $AppId | Out-Null
    }
}

function Get-AppObjectId {
    param([string]$AppId)

    az ad app show --id $AppId --query id -o tsv
}

function Ensure-FederatedCredential {
    param(
        [string]$AppObjectId,
        [string]$Name,
        [string]$Subject
    )

    $existing = az ad app federated-credential list --id $AppObjectId --query "[?name=='$Name'].name" -o tsv 2>$null
    if ($existing) {
        return
    }

    $payload = @{
        name      = $Name
        issuer    = "https://token.actions.githubusercontent.com"
        subject   = $Subject
        audiences = @("api://AzureADTokenExchange")
    } | ConvertTo-Json -Compress

    az ad app federated-credential create --id $AppObjectId --parameters $payload | Out-Null
}

function Ensure-RoleAssignment {
    param(
        [string]$Assignee,
        [string]$RoleName,
        [string]$Scope
    )

    if (-not $Scope) {
        return
    }

    $existing = az role assignment list --assignee $Assignee --role $RoleName --scope $Scope --query "[0].id" -o tsv 2>$null
    if ($existing) {
        return
    }

    az role assignment create --assignee $Assignee --role $RoleName --scope $Scope | Out-Null
}

function Configure-Identity {
    param(
        [string]$AppName,
        [string]$EnvironmentName,
        [string]$ContributorScope,
        [string]$AcrScope,
        [string]$KeyVaultScope
    )

    $appId = Ensure-AppRegistration -DisplayName $AppName
    Ensure-ServicePrincipal -AppId $appId
    $objectId = Get-AppObjectId -AppId $appId

    Ensure-FederatedCredential `
        -AppObjectId $objectId `
        -Name "github-$EnvironmentName" `
        -Subject "repo:$GitHubOrg/$GitHubRepo:environment:$EnvironmentName"

    if ($IncludePullRequestSubjects) {
        Ensure-FederatedCredential `
            -AppObjectId $objectId `
            -Name "github-pull-request" `
            -Subject "repo:$GitHubOrg/$GitHubRepo:pull_request"
    }

    Ensure-RoleAssignment -Assignee $appId -RoleName "Contributor" -Scope $ContributorScope

    if ($AcrScope) {
        Ensure-RoleAssignment -Assignee $appId -RoleName "AcrPush" -Scope $AcrScope
    }

    if ($KeyVaultScope) {
        Ensure-RoleAssignment -Assignee $appId -RoleName "Key Vault Secrets User" -Scope $KeyVaultScope
    }

    return @{
        Environment = $EnvironmentName
        AppId       = $appId
    }
}

az account set --subscription $SubscriptionId | Out-Null

$results = @()
$results += Configure-Identity -AppName $IntegrationAppName -EnvironmentName "integration" -ContributorScope $IntegrationScope -AcrScope $IntegrationAcrScope -KeyVaultScope $IntegrationKeyVaultScope
$results += Configure-Identity -AppName $ProductionAppName -EnvironmentName "production" -ContributorScope $ProductionScope -AcrScope "" -KeyVaultScope $ProductionKeyVaultScope

if ($BootstrapScope) {
    $results += Configure-Identity -AppName $BootstrapAppName -EnvironmentName "bootstrap-foundation" -ContributorScope $BootstrapScope -AcrScope "" -KeyVaultScope ""
}

Write-Host ""
Write-Host "GitHub OIDC setup complete." -ForegroundColor Green
Write-Host ""
Write-Host "Put these values into the matching GitHub Environments:" -ForegroundColor Cyan
$results | ForEach-Object {
    Write-Host "  $($_.Environment): AZURE_CLIENT_ID=$($_.AppId)" -ForegroundColor Yellow
}
Write-Host "  shared: AZURE_TENANT_ID=$TenantId" -ForegroundColor Yellow
Write-Host "  shared: AZURE_SUBSCRIPTION_ID=$SubscriptionId" -ForegroundColor Yellow
Write-Host ""
Write-Host "RBAC assigned by this script:" -ForegroundColor Cyan
Write-Host "  integration: Contributor on $IntegrationScope" -ForegroundColor Yellow
Write-Host "  integration: AcrPush on $IntegrationAcrScope" -ForegroundColor Yellow
if ($IntegrationKeyVaultScope) {
    Write-Host "  integration: Key Vault Secrets User on $IntegrationKeyVaultScope" -ForegroundColor Yellow
}
Write-Host "  production: Contributor on $ProductionScope" -ForegroundColor Yellow
if ($ProductionKeyVaultScope) {
    Write-Host "  production: Key Vault Secrets User on $ProductionKeyVaultScope" -ForegroundColor Yellow
}
if ($BootstrapScope) {
    Write-Host "  bootstrap-foundation: Contributor on $BootstrapScope" -ForegroundColor Yellow
}
Write-Host ""
Write-Host "No role-assignment-admin roles were granted." -ForegroundColor DarkYellow
