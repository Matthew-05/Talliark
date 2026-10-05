param([string]$Compiler = '', [switch]$ExcelRoundtrip)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
if (-not $Compiler) {
    $Compiler = (Get-ChildItem -LiteralPath "$env:ProgramFiles/Microsoft Visual Studio" -Filter csc.exe -Recurse |
        Where-Object { $_.FullName -like '*MSBuild*Roslyn*' } | Select-Object -First 1).FullName
}
if (-not $Compiler) { throw 'Pass -Compiler with the path to the Visual Studio C# compiler.' }
$taskOutput = Join-Path $taskRoot 'output/reconcile-review-storage-tests.exe'
New-Item -ItemType Directory -Path (Split-Path -Parent $taskOutput) -Force | Out-Null
$framework = "$env:WINDIR/Microsoft.NET/Framework/v4.0.30319"
& $Compiler /nologo /target:exe "/out:$taskOutput" "/r:$framework/System.Xml.Linq.dll" "/r:$framework/System.Web.Extensions.dll" "/r:$framework/Microsoft.CSharp.dll" `
    (Join-Path $taskRoot 'src/Talliark.Addin/Modules/CustomXml/Serialization/TalliarkReconcileReviewSerializer.cs') `
    (Join-Path $taskRoot 'src/Talliark.Addin/Modules/CustomXml/Models/ReconcileReview.generated.cs') `
    (Join-Path $taskRoot 'src/Talliark.Addin.Tests/ReconcileReviewStorageTests.cs')
if ($LASTEXITCODE -ne 0) { throw 'C# storage tests failed to compile.' }
if ($ExcelRoundtrip) { & $taskOutput $taskRoot --excel }
else { & $taskOutput $taskRoot }
if ($LASTEXITCODE -ne 0) { throw 'C# storage tests failed.' }
