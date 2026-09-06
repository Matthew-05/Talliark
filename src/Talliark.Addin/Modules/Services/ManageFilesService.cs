using System;
using System.Collections.Generic;
using System.Linq;
using Talliark.Addin.Modules.CustomXml;
using Talliark.Addin.Modules.CustomXml.Models;
using Excel = Microsoft.Office.Interop.Excel;

namespace Talliark.Addin.Modules.Services
{
    /// <summary>
    /// Provides file-management operations (rename, remove, move, folder CRUD) for the
    /// file-manager UI. All methods load and save via <see cref="TalliarkCustomXmlPartStore"/>.
    /// </summary>
    public sealed class ManageFilesService
    {
        private readonly AddPdfDocumentService _addService = new AddPdfDocumentService();

        public string AddPdf(Excel.Workbook workbook, string name, string base64, string folderId = null)
        {
            if (workbook == null) throw new ArgumentNullException(nameof(workbook));
            return _addService.AddEmbeddedPdfFromBase64(workbook, name, base64, folderId);
        }

        public string AddPdfFromFilePath(Excel.Workbook workbook, string pdfFilePath, string folderId = null)
        {
            if (workbook == null) throw new ArgumentNullException(nameof(workbook));
            if (string.IsNullOrWhiteSpace(pdfFilePath))
                throw new ArgumentException("PDF path must be non-empty.", nameof(pdfFilePath));
            return _addService.AddEmbeddedPdf(workbook, pdfFilePath, folderId);
        }

        public TalliarkContent RenamePdf(Excel.Workbook workbook, string id, string newName)
        {
            if (workbook == null) throw new ArgumentNullException(nameof(workbook));
            if (string.IsNullOrWhiteSpace(id)) throw new ArgumentException("id must be non-empty.", nameof(id));
            if (string.IsNullOrWhiteSpace(newName)) throw new ArgumentException("newName must be non-empty.", nameof(newName));

            WorkbookProtectionGuard.ThrowIfStructureProtected(workbook);

            var store = new TalliarkCustomXmlPartStore(workbook);
            TalliarkContent content = store.LoadContent();

            List<PdfMetadata> pdfs = content.Pdfs.ToList();
            int index = pdfs.FindIndex(p => string.Equals(p.Id, id, StringComparison.Ordinal));
            if (index < 0)
                throw new InvalidOperationException("PDF not found: " + id);

            PdfMetadata existing = pdfs[index];
            pdfs[index] = new PdfMetadata(existing.Id, newName.Trim(), existing.FolderId, existing.DateAdded, existing.FileSizeBytes)
            {
                OcrStatus = existing.OcrStatus,
            };

            var updated = new TalliarkContent(content.Version, content.Folders, pdfs);
            store.SaveContent(updated);
            return updated;
        }

        public void RemovePdf(Excel.Workbook workbook, string id)
        {
            if (workbook == null) throw new ArgumentNullException(nameof(workbook));
            if (string.IsNullOrWhiteSpace(id)) throw new ArgumentException("id must be non-empty.", nameof(id));

            WorkbookProtectionGuard.ThrowIfStructureProtected(workbook);

            var store = new TalliarkCustomXmlPartStore(workbook);
            TalliarkContent content = store.LoadContent();
            List<PdfMetadata> pdfs = content.Pdfs
                .Where(p => !string.Equals(p.Id, id, StringComparison.Ordinal))
                .ToList();

            if (pdfs.Count == content.Pdfs.Count)
                throw new InvalidOperationException("PDF not found: " + id);

            store.SaveContent(new TalliarkContent(content.Version, content.Folders, pdfs));
            store.DeletePdfBinary(id);
            new DeleteLinkService().DeleteLinksForPdf(id, workbook);
        }

        public TalliarkContent MoveFile(Excel.Workbook workbook, string id, string folderId)
        {
            if (workbook == null) throw new ArgumentNullException(nameof(workbook));
            if (string.IsNullOrWhiteSpace(id)) throw new ArgumentException("id must be non-empty.", nameof(id));

            WorkbookProtectionGuard.ThrowIfStructureProtected(workbook);

            var store = new TalliarkCustomXmlPartStore(workbook);
            TalliarkContent content = store.LoadContent();

            List<PdfMetadata> pdfs = content.Pdfs.ToList();
            int index = pdfs.FindIndex(p => string.Equals(p.Id, id, StringComparison.Ordinal));
            if (index < 0)
                throw new InvalidOperationException("PDF not found: " + id);

            PdfMetadata existing = pdfs[index];
            string normalised = string.IsNullOrWhiteSpace(folderId) ? null : folderId.Trim();
            pdfs[index] = new PdfMetadata(existing.Id, existing.Name, normalised, existing.DateAdded, existing.FileSizeBytes)
            {
                OcrStatus = existing.OcrStatus,
            };

            var updated = new TalliarkContent(content.Version, content.Folders, pdfs);
            store.SaveContent(updated);
            return updated;
        }

        public void UpdatePdfAfterOcr(Excel.Workbook workbook, string id, string newBase64,
            string geometryBase64, string tableStructureBase64, string documentValuesBase64,
            string financialStructureBase64, string reconcileBase64 = null)
        {
            if (workbook == null) throw new ArgumentNullException(nameof(workbook));
            if (string.IsNullOrWhiteSpace(id)) throw new ArgumentException("id must be non-empty.", nameof(id));
            if (newBase64 == null) throw new ArgumentNullException(nameof(newBase64));

            WorkbookProtectionGuard.ThrowIfStructureProtected(workbook);

            var store = new TalliarkCustomXmlPartStore(workbook);
            if (!store.TryGetMetadata(id, out PdfMetadata existing))
                throw new InvalidOperationException("PDF not found: " + id);

            store.TryLoadPdfBinary(id, out PdfBinaryParts priorParts);

            store.SavePdfBinary(id, new PdfBinaryParts
            {
                Base64 = newBase64,
                GeometryBase64 = geometryBase64,
                TableStructureBase64 = tableStructureBase64,
                DocumentValuesBase64 = documentValuesBase64,
                FinancialStructureBase64 = financialStructureBase64,
                ReconcileBase64 = reconcileBase64 ?? priorParts.ReconcileBase64,
            });

            var updated = new PdfMetadata(existing.Id, existing.Name, existing.FolderId, existing.DateAdded, existing.FileSizeBytes)
            {
                OcrStatus = PdfStatus.Ocr,
            };
            store.UpsertMetadata(updated);
        }

        public void UpdatePdfGeometry(Excel.Workbook workbook, string id, string geometryBase64,
            string tableStructureBase64, string documentValuesBase64,
            string financialStructureBase64, string reconcileBase64 = null)
        {
            if (workbook == null) throw new ArgumentNullException(nameof(workbook));
            if (string.IsNullOrWhiteSpace(id)) throw new ArgumentException("id must be non-empty.", nameof(id));

            WorkbookProtectionGuard.ThrowIfStructureProtected(workbook);

            var store = new TalliarkCustomXmlPartStore(workbook);
            if (!store.TryGetMetadata(id, out PdfMetadata existing))
                throw new InvalidOperationException("PDF not found: " + id);

            store.TryLoadPdfBinary(id, out PdfBinaryParts binaryParts);
            store.SavePdfBinary(id, new PdfBinaryParts
            {
                Base64 = binaryParts.Base64,
                GeometryBase64 = geometryBase64,
                TableStructureBase64 = tableStructureBase64,
                DocumentValuesBase64 = documentValuesBase64,
                FinancialStructureBase64 = financialStructureBase64,
                ReconcileBase64 = reconcileBase64 ?? binaryParts.ReconcileBase64,
            });

            var updated = new PdfMetadata(existing.Id, existing.Name, existing.FolderId, existing.DateAdded, existing.FileSizeBytes)
            {
                OcrStatus = PdfStatus.Ocr,
            };
            store.UpsertMetadata(updated);
        }

        public string AddFolder(Excel.Workbook workbook, string name)
        {
            if (workbook == null) throw new ArgumentNullException(nameof(workbook));
            if (string.IsNullOrWhiteSpace(name)) throw new ArgumentException("name must be non-empty.", nameof(name));

            WorkbookProtectionGuard.ThrowIfStructureProtected(workbook);

            string id = Guid.NewGuid().ToString("D");
            var store = new TalliarkCustomXmlPartStore(workbook);
            store.UpsertFolder(new PdfFolder(id, name.Trim()));
            return id;
        }

        public void RenameFolder(Excel.Workbook workbook, string id, string newName)
        {
            if (workbook == null) throw new ArgumentNullException(nameof(workbook));
            if (string.IsNullOrWhiteSpace(id)) throw new ArgumentException("id must be non-empty.", nameof(id));
            if (string.IsNullOrWhiteSpace(newName)) throw new ArgumentException("newName must be non-empty.", nameof(newName));

            WorkbookProtectionGuard.ThrowIfStructureProtected(workbook);

            var store = new TalliarkCustomXmlPartStore(workbook);
            TalliarkContent content = store.LoadContent();

            if (content.Folders.All(f => !string.Equals(f.Id, id, StringComparison.Ordinal)))
                throw new InvalidOperationException("Folder not found: " + id);

            store.UpsertFolder(new PdfFolder(id, newName.Trim()));
        }

        public void RemoveFolder(Excel.Workbook workbook, string id)
        {
            if (workbook == null) throw new ArgumentNullException(nameof(workbook));
            if (string.IsNullOrWhiteSpace(id)) throw new ArgumentException("id must be non-empty.", nameof(id));

            WorkbookProtectionGuard.ThrowIfStructureProtected(workbook);

            var store = new TalliarkCustomXmlPartStore(workbook);
            store.RemoveFolder(id);
        }
    }
}
