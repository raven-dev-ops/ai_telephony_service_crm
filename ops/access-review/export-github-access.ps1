[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Repo,

    [string]$Org,

    [string]$Branch = "main",

    [string]$OutputJson,

    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Invoke-CommandJson {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Exe,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $cmd = $Exe + " " + ($Arguments -join " ")
    if ($DryRun) {
        Write-Host $cmd
        return $null
    }

    $out = (& $Exe @Arguments 2>&1) | Out-String
    if ($LASTEXITCODE -ne 0) {
        throw "$Exe failed: $out"
    }
    if (-not $out.Trim()) {
        return $null
    }
    return $out | ConvertFrom-Json
}

function Invoke-GhApiSafe {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Endpoint,

        [string]$Label
    )

    $labelValue = $Label
    if (-not $labelValue) {
        $labelValue = $Endpoint
    }

    $args = @(
        "api",
        "-H", "Accept: application/vnd.github+json",
        $Endpoint,
        "--paginate"
    )

    $cmd = "gh " + ($args -join " ")
    if ($DryRun) {
        Write-Host $cmd
        return [ordered]@{
            ok = $true
            label = $labelValue
            endpoint = $Endpoint
            data = $null
        }
    }

    $raw = (& gh @args 2>&1) | Out-String
    if ($LASTEXITCODE -ne 0) {
        return [ordered]@{
            ok = $false
            label = $labelValue
            endpoint = $Endpoint
            error = $raw.Trim()
        }
    }
    if (-not $raw.Trim()) {
        return [ordered]@{
            ok = $true
            label = $labelValue
            endpoint = $Endpoint
            data = $null
        }
    }
    try {
        return [ordered]@{
            ok = $true
            label = $labelValue
            endpoint = $Endpoint
            data = ($raw | ConvertFrom-Json)
        }
    }
    catch {
        return [ordered]@{
            ok = $false
            label = $labelValue
            endpoint = $Endpoint
            error = "Failed to parse JSON response: $($_.Exception.Message)"
            raw = $raw.Trim()
        }
    }
}

if ($Repo -notmatch ".+/.+") {
    throw "-Repo must be in the form owner/repo (got '$Repo')"
}

$repoOwner = $Repo.Split("/", 2)[0]
if (-not $Org) {
    $Org = $repoOwner
}

$startedAtUtc = (Get-Date).ToUniversalTime()
$gitSha = $null
try {
    $gitSha = (& git rev-parse HEAD 2>$null).Trim()
}
catch { }

$ghVersion = $null
try {
    $ghVersion = (& gh --version 2>&1 | Select-Object -First 1).Trim()
}
catch { }

$results = [ordered]@{}
Write-Host "Exporting GitHub access evidence for repo: $Repo"

$results.repo = Invoke-GhApiSafe -Endpoint "/repos/$Repo" -Label "repo"
$results.repo_collaborators = Invoke-GhApiSafe -Endpoint "/repos/$Repo/collaborators?affiliation=all&per_page=100" -Label "repo_collaborators"
$results.repo_teams = Invoke-GhApiSafe -Endpoint "/repos/$Repo/teams?per_page=100" -Label "repo_teams"
$results.repo_actions_secrets = Invoke-GhApiSafe -Endpoint "/repos/$Repo/actions/secrets?per_page=100" -Label "repo_actions_secrets"
$results.repo_actions_variables = Invoke-GhApiSafe -Endpoint "/repos/$Repo/actions/variables?per_page=100" -Label "repo_actions_variables"
$results.repo_environments = Invoke-GhApiSafe -Endpoint "/repos/$Repo/environments?per_page=100" -Label "repo_environments"
$results.branch_protection = Invoke-GhApiSafe -Endpoint "/repos/$Repo/branches/$Branch/protection" -Label "branch_protection"
$results.org_members = Invoke-GhApiSafe -Endpoint "/orgs/$Org/members?per_page=100" -Label "org_members"

# Best-effort: include environment secret inventories (names only) when accessible.
$environmentSecrets = @()
if ($results.repo_environments.ok -and $results.repo_environments.data -and $results.repo_environments.data.environments) {
    foreach ($env in $results.repo_environments.data.environments) {
        $envName = $env.name
        if (-not $envName) { continue }
        $environmentSecrets += Invoke-GhApiSafe -Endpoint "/repos/$Repo/environments/$envName/secrets?per_page=100" -Label "environment_secrets:$envName"
        $environmentSecrets += Invoke-GhApiSafe -Endpoint "/repos/$Repo/environments/$envName/variables?per_page=100" -Label "environment_variables:$envName"
    }
}
$results.environment_inventories = $environmentSecrets

$evidence = [ordered]@{
    action = "github_access_export"
    generated_at_utc = $startedAtUtc.ToString("o")
    repo = $Repo
    org = $Org
    branch = $Branch
    git_sha = $gitSha
    gh_version = $ghVersion
    results = $results
}

if ($OutputJson) {
    ($evidence | ConvertTo-Json -Depth 20) | Out-File -FilePath $OutputJson -Encoding utf8
    Write-Host "Wrote evidence JSON: $OutputJson"
} else {
    $defaultName = "github-access-evidence-$($startedAtUtc.ToString('yyyyMMdd'))Z.json"
    ($evidence | ConvertTo-Json -Depth 20) | Out-File -FilePath $defaultName -Encoding utf8
    Write-Host "Wrote evidence JSON: $defaultName"
}
