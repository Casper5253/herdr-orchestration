# The one live price gate for a Devin launch, shared by both team launchers.
# Being on policy.devin.allowed_models is not enough: `devin models list` must
# offer the model and tag it Free right now, or the launch is refused.
function Assert-DevinModelFree {
    param($Policy, [string]$Model)
    if ($Model -cnotin $Policy.devin.allowed_models) {
        throw 'Devin model is not in policy.devin.allowed_models; refusing a paid or retired model.'
    }
    $modelOut = devin models list --format json | Out-String
    if ($LASTEXITCODE -ne 0) { throw 'devin models list failed.' }
    $catalog = try { $modelOut | ConvertFrom-Json } catch { throw 'devin models list returned no JSON.' }
    $variants = @($catalog.families | ForEach-Object { $_.variants } | Where-Object { $_.model_uid -ceq $Model })
    if ($variants.Count -ne 1 -or $variants[0].cost_tier -cne 'Free') {
        throw 'Devin model is unavailable or not Free; refusing launch.'
    }
}
