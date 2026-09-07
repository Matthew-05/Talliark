<div align="center">
  <img src="src/resources/branding/talliark-icon.svg" alt="Talliark" width="116" height="133">
  <h1>Talliark</h1>
  <p>An Excel add-in for keeping supporting documents connected to spreadsheet work.</p>
</div>

> **This project is under active development.** Its name, branding, features, and documentation may change before a stable release.

Talliark is being built for document-heavy Excel workflows. It keeps source documents with the workbook, links worksheet cells to exact regions on a page, and processes documents locally.

## Current status

Talliark is not yet ready for production use. Early builds are available on the [Releases page](https://github.com/Matthew-05/Talliark/releases) for testing on Windows with the desktop version of Microsoft Excel.

More complete installation instructions, product documentation, screenshots, and support information will be added as the project approaches a stable release.

## Development corpus

Public documents used by the local OCR and analysis benchmarks are not
redistributed in this repository. Run
`.\scripts\setup-development-corpus.ps1` after cloning to download the pinned
source files, verify their SHA-256 hashes, and assemble the image-derived PDFs.
See the
[sample corpus documentation](sample-document-corpus/README.md) for provenance
and usage.

## License

Talliark is available under the [Mozilla Public License 2.0](LICENSE).
