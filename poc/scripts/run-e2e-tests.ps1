param(
    [Parameter(Mandatory = $false)]
    [string]$ConfigFile = "./.env",

    [Parameter(Mandatory = $false)]
    [string]$UserToken = ""
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

$mcpUrl = $env['MCP_SERVICE_URL']
if ([string]::IsNullOrWhiteSpace($mcpUrl)) {
    throw "Set MCP_SERVICE_URL in $ConfigFile before running E2E tests."
}

if ([string]::IsNullOrWhiteSpace($UserToken)) {
    $aud = $env['BROKER_EXPECTED_AUDIENCE']
    if ([string]::IsNullOrWhiteSpace($aud)) {
        throw "BROKER_EXPECTED_AUDIENCE is required in $ConfigFile to auto-fetch user token."
    }

    $scope = "$aud/access_as_user"
    $tokenResult = az account get-access-token --scope $scope | ConvertFrom-Json
    $UserToken = $tokenResult.accessToken
}

pwsh ./tests/e2e/e2e_revenue_queries.ps1 -McpUrl $mcpUrl -UserToken $UserToken
Write-Host "E2E script completed."
