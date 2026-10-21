$ErrorActionPreference = 'Stop'

$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$temporaryRoot = (Resolve-Path -LiteralPath (Join-Path $projectRoot 'archivos_temporales')).Path
$resultsRoot = (Resolve-Path -LiteralPath (Join-Path $projectRoot 'resultados_temporales')).Path
$localTestsRoot = (Resolve-Path -LiteralPath (Join-Path $projectRoot 'pruebas_locales')).Path
$localTestXmlPath = Join-Path $localTestsRoot '20260611.xml'
$projectPrefix = $projectRoot.TrimEnd('\') + '\'

foreach ($targetRoot in @($temporaryRoot, $resultsRoot, $localTestsRoot)) {
    if (-not $targetRoot.StartsWith($projectPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "La ruta de limpieza queda fuera del proyecto: $targetRoot"
    }
}

$temporaryFiles = @(Get-ChildItem -LiteralPath $temporaryRoot -File -Recurse)
$jsonFiles = @(Get-ChildItem -LiteralPath $resultsRoot -File -Filter '*.json' -Recurse)
$localTestXmlFiles = @()

if (Test-Path -LiteralPath $localTestXmlPath -PathType Leaf) {
    $localTestXmlFiles = @(Get-Item -LiteralPath $localTestXmlPath)
}

$filesToDelete = @($temporaryFiles) + @($jsonFiles) + @($localTestXmlFiles)
$deletedBytes = 0L

foreach ($file in $filesToDelete) {
    $resolvedFile = (Resolve-Path -LiteralPath $file.FullName).Path
    $insideTemporary = $resolvedFile.StartsWith(
        $temporaryRoot.TrimEnd('\') + '\',
        [System.StringComparison]::OrdinalIgnoreCase
    )
    $insideResults = $resolvedFile.StartsWith(
        $resultsRoot.TrimEnd('\') + '\',
        [System.StringComparison]::OrdinalIgnoreCase
    )
    $isAuthorizedTestXml = $resolvedFile.Equals(
        $localTestXmlPath,
        [System.StringComparison]::OrdinalIgnoreCase
    )

    if (-not ($insideTemporary -or $insideResults -or $isAuthorizedTestXml)) {
        throw "Se rechazó un archivo fuera del alcance autorizado: $resolvedFile"
    }

    $deletedBytes += $file.Length
    Remove-Item -LiteralPath $resolvedFile -Force
}

$remainingTemporaryFiles = @(Get-ChildItem -LiteralPath $temporaryRoot -File -Recurse)
$remainingJsonFiles = @(Get-ChildItem -LiteralPath $resultsRoot -File -Filter '*.json' -Recurse)
$localTestXmlRemaining = Test-Path -LiteralPath $localTestXmlPath -PathType Leaf

[PSCustomObject]@{
    temporary_files_deleted = $temporaryFiles.Count
    json_files_deleted = $jsonFiles.Count
    local_test_xml_deleted = $localTestXmlFiles.Count
    bytes_deleted = $deletedBytes
    temporary_files_remaining = $remainingTemporaryFiles.Count
    json_files_remaining = $remainingJsonFiles.Count
    local_test_xml_remaining = $localTestXmlRemaining
} | ConvertTo-Json
