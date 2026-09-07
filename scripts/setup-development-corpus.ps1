<#
.SYNOPSIS
    Downloads the gitignored public-document corpus used by local benchmarks.

.DESCRIPTION
    Fetches every source from its publisher or upstream repository, verifies
    its pinned SHA-256, and populates sample-document-corpus/. Image sources are
    assembled into image-only PDFs at 200 DPI. Existing files with the expected
    hash are left untouched; a mismatched file is not replaced without -Force.

.EXAMPLE
    .\scripts\setup-development-corpus.ps1

.EXAMPLE
    .\scripts\setup-development-corpus.ps1 -VerifyOnly

.PARAMETER Force
    Replace an existing direct-download file whose SHA-256 does not match, and
    rebuild image-derived PDFs.

.PARAMETER VerifyOnly
    Check that every local corpus document exists without downloading files.

.PARAMETER PythonPath
    Python executable to use for image-derived PDFs. Pillow is required and is
    installed by src/python/requirements.txt.
#>

param(
    [switch]$Force,
    [switch]$VerifyOnly,
    [string]$PythonPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ocrCommit = "196072acef056aa11b63e60564d98762bd38f638"
$googleCommit = "001ba391ab4a2f40d001cc0387618cb3c3699523"
$ocrBase = "https://raw.githubusercontent.com/ocrmypdf/OCRmyPDF"
$googleBase = "https://raw.githubusercontent.com/GoogleCloudPlatform/document-ai-samples/$googleCommit"

$downloads = @(
    [pscustomobject]@{ RelativePath = "financial-statements\apple 10k.pdf"; Url = "https://d18rn0p25nwr6d.cloudfront.net/CIK-0000320193/c24e7a28-5254-4dfa-9447-62aaa3c24bb1.pdf"; Sha256 = "108590052c3ba5400c63660d787fe7ed4e43868292946d7a7facebe9ab7d1aab" }
    [pscustomobject]@{ RelativePath = "financial-statements\disney 10-k.pdf"; Url = "https://investors.thewaltdisneycompany.com/files/doc_financials/2025/ar/2025-Annual-Report.pdf"; Sha256 = "4567eb113db33ae6061dd047743093365b7aa875f9b2908c25bc044c389da5fe" }
    [pscustomobject]@{ RelativePath = "financial-statements\quest 10k.pdf"; Url = "https://s29.q4cdn.com/924252060/files/doc_financials/2025/ar/d494cf61-2aaf-485f-b90e-ecc0853ca722.pdf"; Sha256 = "c374675aac0f31fee5972319e487fbd918af88b0c0c5c626cf9e6fac67508335" }
    [pscustomobject]@{ RelativePath = "financial-statements\Amazon_AR.pdf"; Url = "https://s2.q4cdn.com/299287126/files/doc_financials/annual/Amazon_AR.PDF"; Sha256 = "7ad17bf1634f10ea777505d5c35de1735bc3e2944c614e1a80c68360c942d3ad" }
    [pscustomobject]@{ RelativePath = "financial-statements\Boeing-2023-Annual-Report.pdf"; Url = "https://s2.q4cdn.com/661678649/files/doc_financials/2023/ar/Boeing-2023-Annual-Report.pdf"; Sha256 = "663a413e937a8596f39f6443ee9ea761b1124b500aee6feda1d19b5f5f9195b5" }
    [pscustomobject]@{ RelativePath = "financial-statements\RoyCarver-2014-Rpt-Final.pdf"; Url = "https://carvertrust.org/wp-content/uploads/2017/03/RoyCarver-2014-Rpt-Final.pdf"; Sha256 = "f912cf0136af5cab9e898b0eedb0b2d04c1242d08ba97226f9c77993eeff6f5d" }
    [pscustomobject]@{ RelativePath = "financial-statements\cafr1112bfs.pdf"; Url = "https://www.sandiegocounty.gov/content/dam/sdc/auditor/annual_report12/pdf/cafr1112bfs.pdf"; Sha256 = "524419643cd63f23a2f65ee4292b2ac2353c0924852bcb709e67c01d6c2eaf1d" }
    [pscustomobject]@{ RelativePath = "financial-statements\Sample-Financial-Statements-1.pdf"; Url = "https://mbpc.tcnj.edu/wp-content/uploads/sites/148/2021/10/Sample-Financial-Statements-1.pdf"; Sha256 = "3c424fe06ddfe8ce9291ce10cfd2f7633ceb23dfc448a920b538584b0ec8023a" }
    [pscustomobject]@{ RelativePath = "mixed-pdf-purposes\01-difficult-illustrated-book-page.pdf"; Url = "$ocrBase/ce2dbdf372ec711f6c134914363e035943f5cbb8/tests/resources/c02-22.pdf"; Sha256 = "ae6a3bec3809e1540911bda42dabb42ffbd63cfda17e74a5c3e9dcd87129462a" }
    [pscustomobject]@{ RelativePath = "mixed-pdf-purposes\02-two-column-scan.pdf"; Url = "$ocrBase/$ocrCommit/tests/resources/linn.pdf"; Sha256 = "e923f6e8e036185f8f2aae5f7fdeefd8ac658d627cebd4ebf630de4cbf0a2d64" }
    [pscustomobject]@{ RelativePath = "mixed-pdf-purposes\03-ccitt-monochrome-scan.pdf"; Url = "$ocrBase/$ocrCommit/tests/resources/ccitt.pdf"; Sha256 = "5f4b129bf0eb0d32358a917cd1754c6fd68cac589ad79076b6d0191ebe84f0f1" }
    [pscustomobject]@{ RelativePath = "mixed-pdf-purposes\04-skewed-scan.pdf"; Url = "$ocrBase/$ocrCommit/tests/resources/skew.pdf"; Sha256 = "6be6b54d49df71351974774404e299b756d8d3cbeb2c4a31f15fae8dd983d72f" }
    [pscustomobject]@{ RelativePath = "mixed-pdf-purposes\05-rotated-skewed-scan.pdf"; Url = "$ocrBase/$ocrCommit/tests/resources/rotated_skew.pdf"; Sha256 = "5122c07d05a61219eb9f8305776ef16def6b34c29358e05c5ae2e884a351438f" }
    [pscustomobject]@{ RelativePath = "mixed-pdf-purposes\06-multiple-page-rotations.pdf"; Url = "$ocrBase/$ocrCommit/tests/resources/cardinal.pdf"; Sha256 = "ebd7b2233aea5f320562df9635901bbd8e734cb42c85defa1059163d4b1d6ccb" }
    [pscustomobject]@{ RelativePath = "mixed-pdf-purposes\07-high-dpi-typewriter.pdf"; Url = "$ocrBase/$ocrCommit/tests/resources/2400dpi.pdf"; Sha256 = "98cf83efd35673301dbfcbfa900e42a31f8ea90cb16ffd9b8be2aa28394ef142" }
    [pscustomobject]@{ RelativePath = "mixed-pdf-purposes\08-nonsquare-dpi-scan.pdf"; Url = "$ocrBase/$ocrCommit/tests/resources/aspect.pdf"; Sha256 = "1585370a7897e0cbef3af440f4c107b5ee2a31d208fad794ad225d856b460628" }
    [pscustomobject]@{ RelativePath = "mixed-pdf-purposes\09-french-diacritics.pdf"; Url = "$ocrBase/$ocrCommit/tests/resources/francais.pdf"; Sha256 = "600c27f8dd2ef085a94d3500f6ccdb3b37f4b89aa8ffdf9a00a06a1367887e8b" }
    [pscustomobject]@{ RelativePath = "mixed-pdf-purposes\10-image-in-form-xobject.pdf"; Url = "$ocrBase/$ocrCommit/tests/resources/formxobject.pdf"; Sha256 = "bd9060153923c1b716ddb2d3408d686145a9d896bfa6b7c8155f7a1f64776066" }
    [pscustomobject]@{ RelativePath = "mixed-pdf-purposes\11-multipage-mixed.pdf"; Url = "$ocrBase/545cd031b03a57bb07afb7d57c067fe4e0cdc128/tests/resources/multipage.pdf"; Sha256 = "1cf372a2b66c3be729b853bb19a0361244feec0e39f31eeff548a00ffcd7ab70" }
    [pscustomobject]@{ RelativePath = "mixed-pdf-purposes\12-jbig2-monochrome-scan.pdf"; Url = "$ocrBase/$ocrCommit/tests/resources/jbig2.pdf"; Sha256 = "210b845b62eab7118bc1e6076a3235a37eb947218254198c6102d06422f35037" }
    [pscustomobject]@{ RelativePath = "mixed-pdf-purposes\13-scanned-line-item-invoice.pdf"; Url = "$googleBase/community/codelabs/docai-specialized-processors/google_invoice.pdf"; Sha256 = "fc38c1d68a565e1ef56f43de99dbe2c4effed643dfa0e8d3b9d345b33010ed8f" }
    [pscustomobject]@{ RelativePath = "mixed-pdf-purposes\14-stained-energy-invoice.pdf"; Url = "$googleBase/web-app-pix2info-python/src/samples/4-INVOICE_PROCESSOR/a.%20Energy%20invoice%20(stained).pdf"; Sha256 = "010d1f502d442271c8cee72cfd9f5c5e1e3ab800aba7300a5354440c22d64cee" }
    [pscustomobject]@{ RelativePath = "mixed-pdf-purposes\15-mixed-procurement-packet.pdf"; Url = "$googleBase/community/codelabs/docai-specialized-processors/procurement_multi_document.pdf"; Sha256 = "29aec93511c025d18afe6bd74d39c2657f34694e6efa4a03c0727e48978f4abe" }
    [pscustomobject]@{ RelativePath = "mixed-pdf-purposes\16-scanned-form-with-table.pdf"; Url = "$googleBase/community/codelabs/docai-form-parser/form_with_tables.pdf"; Sha256 = "86c26db043a60fa04b15882ac7674d222ef75176af9de747635bce78e46f96e3" }
)

$derivedDocuments = @(
    [pscustomobject]@{ RelativePath = "mixed-pdf-purposes\17-photographed-grocery-receipt.pdf"; Sources = @([pscustomobject]@{ Name = "receipt.png"; Url = "$googleBase/web-app-pix2info-python/src/samples/3-EXPENSE_PROCESSOR/a.%20Receipt.png"; Sha256 = "852e08ae5c6c8b79713e6ca1894415cef785e6c47bafbffd22804d1870a24ecc" }) }
    [pscustomobject]@{ RelativePath = "mixed-pdf-purposes\18-photographed-ticket-receipt.pdf"; Sources = @([pscustomobject]@{ Name = "enrichments.jpg"; Url = "$googleBase/web-app-pix2info-python/src/samples/3-EXPENSE_PROCESSOR/b.%20Enrichments.jpg"; Sha256 = "01f14c656aeeda50afc6ebf51bf8bc522f2ad35041509540a0a84347a5697db6" }) }
    [pscustomobject]@{
        RelativePath = "mixed-pdf-purposes\19-scanned-table-layouts.pdf"
        Sources = @(
            [pscustomobject]@{ Name = "table-without-borders.png"; Url = "$googleBase/web-app-pix2info-python/src/samples/2-FORM_PARSER_PROCESSOR/c.%20Table%20without%20borders.png"; Sha256 = "391c664245fca1e90b87a7ef971ec212f5df2cd658cd3f43382debc3e5a68962" }
            [pscustomobject]@{ Name = "table-with-borders.png"; Url = "$googleBase/web-app-pix2info-python/src/samples/2-FORM_PARSER_PROCESSOR/d.%20Table%20with%20borders.png"; Sha256 = "a695364379bdb98bbbc52e58e078029dd25ffd65056005304ec38acbf1128a6b" }
        )
    }
    [pscustomobject]@{
        RelativePath = "mixed-pdf-purposes\20-historical-general-ledger.pdf"
        Sources = @(
            [pscustomobject]@{ Name = "ledger-0250.jpg"; Url = "https://tile.loc.gov/storage-services/master/mss/mgw/mgw5/115/0200/0250.jpg"; Sha256 = "d30c2bd183f629af47a012e6b53e72345cccc1e16ba1b5c7270c6fb422834f3d" }
            [pscustomobject]@{ Name = "ledger-0600.jpg"; Url = "https://tile.loc.gov/storage-services/master/mss/mgw/mgw5/115/0600/0600.jpg"; Sha256 = "fec8a4a64224bc7cc481df58aac7838edf47f49de303e6aee6ad8ec1b970c8d3" }
        )
    }
)

function Get-Sha256 {
    param([Parameter(Mandatory)][string]$Path)
    (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
}

function Invoke-CorpusDownload {
    param([Parameter(Mandatory)][string]$Url, [Parameter(Mandatory)][string]$DestinationPath)
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $userAgent = "Talliark development corpus (https://github.com/Matthew-05/Talliark)"
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) {
        & curl.exe -fL --retry 3 --retry-delay 2 -A $userAgent -o $DestinationPath $Url
        if ($LASTEXITCODE -eq 0) { return }
        Remove-Item -LiteralPath $DestinationPath -Force -ErrorAction SilentlyContinue
    }
    Invoke-WebRequest -Uri $Url -OutFile $DestinationPath -UseBasicParsing -Headers @{ "User-Agent" = $userAgent }
}

function Get-PythonCommand {
    if ($PythonPath) {
        if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) { throw "PythonPath does not exist: $PythonPath" }
        return [pscustomobject]@{ Executable = $PythonPath; Prefix = @() }
    }
    foreach ($candidate in @((Join-Path $repositoryRoot ".venv\Scripts\python.exe"), (Join-Path $repositoryRoot "src\python\.venv\Scripts\python.exe"))) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return [pscustomobject]@{ Executable = $candidate; Prefix = @() } }
    }
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($python) { return [pscustomobject]@{ Executable = $python.Source; Prefix = @() } }
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) { return [pscustomobject]@{ Executable = $launcher.Source; Prefix = @("-3") } }
    throw "Python 3 with Pillow is required to assemble corpus PDFs 17-20. Install src/python/requirements.txt or pass -PythonPath."
}

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$corpusRoot = Join-Path $repositoryRoot "sample-document-corpus"
$failures = 0

foreach ($download in $downloads) {
    $destinationPath = Join-Path $corpusRoot $download.RelativePath
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destinationPath) | Out-Null
    $existingHash = if (Test-Path -LiteralPath $destinationPath) { Get-Sha256 $destinationPath } else { $null }
    if ($existingHash -eq $download.Sha256) {
        Write-Host "Ready: $($download.RelativePath)" -ForegroundColor Green
        continue
    }
    if ($VerifyOnly) {
        $status = if ($null -eq $existingHash) { "missing" } else { "hash mismatch" }
        Write-Error "$($download.RelativePath): $status" -ErrorAction Continue
        $failures++
        continue
    }
    if (($null -ne $existingHash) -and -not $Force) {
        Write-Error "$($download.RelativePath): hash mismatch; rerun with -Force to replace it." -ErrorAction Continue
        $failures++
        continue
    }
    $temporaryPath = Join-Path ([IO.Path]::GetTempPath()) ([IO.Path]::GetRandomFileName())
    try {
        Write-Host "Downloading $($download.RelativePath)..." -ForegroundColor Cyan
        Invoke-CorpusDownload $download.Url $temporaryPath
        $downloadedHash = Get-Sha256 $temporaryPath
        if ($downloadedHash -ne $download.Sha256) { throw "SHA-256 mismatch for $($download.RelativePath). Expected $($download.Sha256), got $downloadedHash." }
        Move-Item -LiteralPath $temporaryPath -Destination $destinationPath -Force
        Write-Host "Ready: $($download.RelativePath)" -ForegroundColor Green
    } catch {
        Write-Error $_ -ErrorAction Continue
        $failures++
    } finally {
        Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
    }
}

foreach ($derived in $derivedDocuments) {
    $destinationPath = Join-Path $corpusRoot $derived.RelativePath
    if ((Test-Path -LiteralPath $destinationPath) -and -not $Force) {
        Write-Host "Ready: $($derived.RelativePath) (locally assembled)" -ForegroundColor Green
        continue
    }
    if ($VerifyOnly) {
        Write-Error "$($derived.RelativePath): missing" -ErrorAction Continue
        $failures++
        continue
    }
    $sourceDirectory = Join-Path ([IO.Path]::GetTempPath()) ("talliark-corpus-" + [Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $sourceDirectory | Out-Null
    try {
        $sourcePaths = @()
        foreach ($source in $derived.Sources) {
            $sourcePath = Join-Path $sourceDirectory $source.Name
            Invoke-CorpusDownload $source.Url $sourcePath
            $sourceHash = Get-Sha256 $sourcePath
            if ($sourceHash -ne $source.Sha256) { throw "SHA-256 mismatch for source $($source.Name). Expected $($source.Sha256), got $sourceHash." }
            $sourcePaths += $sourcePath
        }
        $python = Get-PythonCommand
        $builderPath = Join-Path $PSScriptRoot "build-public-corpus-pdf.py"
        $arguments = @($python.Prefix) + @($builderPath, "--output", $destinationPath)
        foreach ($sourcePath in $sourcePaths) { $arguments += @("--source", $sourcePath) }
        & $python.Executable @arguments
        if ($LASTEXITCODE -ne 0) { throw "PDF assembly failed for $($derived.RelativePath)." }
        Write-Host "Ready: $($derived.RelativePath) (locally assembled)" -ForegroundColor Green
    } catch {
        Write-Error $_ -ErrorAction Continue
        $failures++
    } finally {
        Remove-Item -LiteralPath $sourceDirectory -Recurse -Force -ErrorAction SilentlyContinue
    }
}

if ($failures -gt 0) { throw "Development corpus setup failed for $failures document(s)." }
Write-Host "Development corpus is ready at $corpusRoot" -ForegroundColor Green
