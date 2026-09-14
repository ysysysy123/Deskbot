[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$Message,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string[]]$Paths,

    [switch]$Push
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot

& git -C $repoRoot rev-parse --is-inside-work-tree | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Not a Git repository: $repoRoot"
}

$alreadyStaged = @(& git -C $repoRoot diff --cached --name-only)
if ($alreadyStaged.Count -gt 0) {
    throw "The index already contains staged files. Commit or unstage them before publishing a handoff."
}

foreach ($path in $Paths) {
    if ([System.IO.Path]::IsPathRooted($path)) {
        throw "Use repository-relative paths only: $path"
    }
    $normalizedPath = $path.Replace('\', '/')
    if ($normalizedPath -eq '.' -or $normalizedPath.StartsWith('../') -or $normalizedPath.Contains('/../') -or $normalizedPath -match '[*?\[]' -or $normalizedPath.StartsWith(':')) {
        throw "Use an explicit path without traversal, wildcards, or Git pathspec magic: $path"
    }
    & git -C $repoRoot add -- $path
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to stage: $path"
    }
}

$stagedFiles = @(& git -C $repoRoot diff --cached --name-only --diff-filter=ACMR)
if ($stagedFiles.Count -eq 0) {
    throw 'No changes were staged.'
}

$blockedPattern = '(^|/)(\.env($|\.)|credentials[^/]*\.json$|client_secret[^/]*\.json$|token[^/]*\.json$|[^/]*\.(pem|key|p12|pfx)$)'
$blockedFiles = @($stagedFiles | Where-Object { $_ -match $blockedPattern })
if ($blockedFiles.Count -gt 0) {
    throw "Refusing to commit possible credentials: $($blockedFiles -join ', ')"
}

& git -C $repoRoot diff --cached --check
if ($LASTEXITCODE -ne 0) {
    throw 'Staged changes failed git diff --check.'
}

& git -C $repoRoot commit -m $Message
if ($LASTEXITCODE -ne 0) {
    throw 'Commit failed.'
}

if ($Push) {
    & git -C $repoRoot remote get-url origin | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'No origin remote is configured. Add a private GitHub remote before pushing.'
    }
    & git -C $repoRoot push -u origin HEAD
    if ($LASTEXITCODE -ne 0) {
        throw 'Push failed.'
    }
}

Write-Output "Handoff committed from $repoRoot"
