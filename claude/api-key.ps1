# Claude Code apiKeyHelper: prints the gateway key held in the environment variable named by the first argument.
# Installed by llm_manager; the key itself is never written to any file.
param([Parameter(Mandatory = $true)][string]$Name)

$value = [Environment]::GetEnvironmentVariable($Name)
if (-not $value) {
    # setx only reaches processes started afterwards, so also read the saved user value
    $value = [Environment]::GetEnvironmentVariable($Name, 'User')
}
if (-not $value) {
    [Console]::Error.WriteLine("llm_manager api-key helper: environment variable $Name is not set")
    exit 1
}
[Console]::Out.Write($value)
