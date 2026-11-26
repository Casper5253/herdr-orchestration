function Resolve-HxPolicyPath {
    param([Parameter(Mandatory)][string]$ScriptDir)

    if ($env:HX_POLICY_PATH) { return $env:HX_POLICY_PATH }

    $runtimeRoot = Join-Path $HOME '.hx-runtime'
    $current = Join-Path $runtimeRoot 'current'
    if (Test-Path $current) {
        $install = Get-Content -LiteralPath $current -TotalCount 1 -ErrorAction SilentlyContinue
        if ($install) {
            $candidate = Join-Path $install.TrimEnd("`r") 'policy.json'
            if (Test-Path $candidate) { return $candidate }
        }
        throw 'a runtime is activated but its policy.json is missing; repair or roll back the hx runtime.'
    }

    $checkout = Join-Path $ScriptDir '..\policy.json'
    if (Test-Path $checkout) {
        Write-Warning 'no pinned hx runtime is activated; using checkout policy only as bootstrap fallback.'
        return $checkout
    }
    throw 'policy.json not found; activate the pinned hx runtime, set HX_POLICY_PATH, or run the launcher from the hx repo checkout.'
}
