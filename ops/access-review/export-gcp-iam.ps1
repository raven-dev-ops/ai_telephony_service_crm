[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectId,

    [string]$OrganizationId,

    [string]$OutputJson,

    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Invoke-GcloudJsonSafe {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,

        [string]$Label
    )

    $labelValue = $Label
    if (-not $labelValue) {
        $labelValue = $Arguments -join " "
    }

    $fullArgs = @("--project=$ProjectId")
    $fullArgs += $Arguments
    $fullArgs += "--format=json"

    $cmd = "gcloud " + ($fullArgs -join " ")
    if ($DryRun) {
        Write-Host $cmd
        return [ordered]@{
            ok = $true
            label = $labelValue
            args = $fullArgs
            data = $null
        }
    }

    $raw = (& gcloud @fullArgs 2>&1) | Out-String
    if ($LASTEXITCODE -ne 0) {
        return [ordered]@{
            ok = $false
            label = $labelValue
            args = $fullArgs
            error = $raw.Trim()
        }
    }
    if (-not $raw.Trim()) {
        return [ordered]@{
            ok = $true
            label = $labelValue
            args = $fullArgs
            data = $null
        }
    }
    try {
        return [ordered]@{
            ok = $true
            label = $labelValue
            args = $fullArgs
            data = ($raw | ConvertFrom-Json)
        }
    }
    catch {
        return [ordered]@{
            ok = $false
            label = $labelValue
            args = $fullArgs
            error = "Failed to parse JSON response: $($_.Exception.Message)"
            raw = $raw.Trim()
        }
    }
}

$startedAtUtc = (Get-Date).ToUniversalTime()
$gcloudVersion = $null
try {
    $gcloudVersion = (& gcloud --version 2>&1 | Select-Object -First 1).Trim()
}
catch { }

Write-Host "Exporting GCP IAM evidence for project: $ProjectId"

$results = [ordered]@{}
$results.project_iam_policy = Invoke-GcloudJsonSafe -Arguments @("projects", "get-iam-policy", $ProjectId) -Label "project_iam_policy"
$results.service_accounts = Invoke-GcloudJsonSafe -Arguments @("iam", "service-accounts", "list") -Label "service_accounts"

if ($OrganizationId) {
    # Org policies require org-level permissions; record failures without stopping.
    $results.org_policies = Invoke-GcloudJsonSafe -Arguments @(
        "resource-manager", "org-policies", "list",
        "--organization=$OrganizationId"
    ) -Label "org_policies"
}

$evidence = [ordered]@{
    action = "gcp_iam_export"
    generated_at_utc = $startedAtUtc.ToString("o")
    project_id = $ProjectId
    organization_id = $OrganizationId
    gcloud_version = $gcloudVersion
    results = $results
}

if ($OutputJson) {
    ($evidence | ConvertTo-Json -Depth 50) | Out-File -FilePath $OutputJson -Encoding utf8
    Write-Host "Wrote evidence JSON: $OutputJson"
} else {
    $defaultName = "gcp-iam-evidence-$($startedAtUtc.ToString('yyyyMMdd'))Z.json"
    ($evidence | ConvertTo-Json -Depth 50) | Out-File -FilePath $defaultName -Encoding utf8
    Write-Host "Wrote evidence JSON: $defaultName"
}
