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
    "/r:$framework/System.Windows.Forms.dll" `
    (Join-Path $taskRoot 'src/Talliark.Addin/Modules/CustomXml/Serialization/TalliarkReconcileReviewSerializer.cs') `
    (Join-Path $taskRoot 'src/Talliark.Addin/Modules/CustomXml/Models/ReconcileReview.generated.cs') `
    (Join-Path $taskRoot 'src/Talliark.Addin.Tests/ReconcileReviewStorageTests.cs')
if ($LASTEXITCODE -ne 0) { throw 'C# storage tests failed to compile.' }
if ($ExcelRoundtrip) {
    @'
import sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path[:0] = [str(root / 'src/python'), str(root / 'src/python/tests')]
from test_reconcile_review import sample_scan
from engines.binary_codec import json_to_base64
model = sample_scan()
model['source']['documentId'] = 'service-doc'
model['summary'] = {'tablesExamined': 1, 'totalsNominated': 2, 'confirmed': 2, 'breaks': 0, 'unresolved': 0}
(root / 'output/reconcile-service-scan.txt').write_text(json_to_base64(model))
model['source']['geometryFingerprint'] = 'changed-geometry'
model['tables'][0]['cells'][2]['normalizedValue'] = '7'
(root / 'output/reconcile-service-changed-scan.txt').write_text(json_to_base64(model))
'@ | & (Join-Path $taskRoot 'src/python/dist/worker/python.exe') - $taskRoot
    if ($LASTEXITCODE -ne 0) { throw 'Could not prepare synthetic service fixtures.' }
    & $taskOutput $taskRoot --excel
}
else { & $taskOutput $taskRoot }
if ($LASTEXITCODE -ne 0) { throw 'C# storage tests failed.' }
