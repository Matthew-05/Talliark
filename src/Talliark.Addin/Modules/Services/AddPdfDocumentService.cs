using System;
using System.Collections.Generic;
using System.IO;
using Talliark.Addin.Modules.CustomXml;
using Talliark.Addin.Modules.CustomXml.Models;
using Excel = Microsoft.Office.Interop.Excel;

namespace Talliark.Addin.Modules.Services
{
    public sealed class AddPdfDocumentService
    {
        /// <summary>Embeds PDF bytes in the workbook custom XML store without creating a linked rectangle.</summary>
        /// <returns>The GUID of the newly stored PDF.</returns>
        public string AddEmbeddedPdf(Excel.Workbook workbook, string pdfFilePath, string folderId = null)
        {
            if (workbook == null)
                throw new ArgumentNullException(nameof(workbook));
            if (string.IsNullOrWhiteSpace(pdfFilePath))
                throw new ArgumentException("PDF path must be non-empty.", nameof(pdfFilePath));

            WorkbookProtectionGuard.ThrowIfStructureProtected(workbook);

            byte[] bytes = File.ReadAllBytes(pdfFilePath);
            string base64 = Convert.ToBase64String(bytes);

            var store = new TalliarkCustomXmlPartStore(workbook);
            string pdfId = Guid.NewGuid().ToString("D");
            string name = AllocateName(store, Path.GetFileName(pdfFilePath));
            var pdf = new PdfDocument(pdfId, name, base64, folderId, DateTime.UtcNow, bytes.LongLength)
            {
                OcrStatus = PdfTextLayerDetector.ClassifyFromBase64(base64),
            };

            store.UpsertPdf(pdf);
            return pdfId;
        }

        public string AddPreparedPdf(
            Excel.Workbook workbook,
            string name,
            string base64,
            string ocrStatus,
            long fileSizeBytes,
            string folderId = null)
        {
            if (workbook == null)
                throw new ArgumentNullException(nameof(workbook));
            if (string.IsNullOrWhiteSpace(name))
                throw new ArgumentException("PDF name must be non-empty.", nameof(name));
            if (base64 == null)
                throw new ArgumentNullException(nameof(base64));

            WorkbookProtectionGuard.ThrowIfStructureProtected(workbook);

            var store = new TalliarkCustomXmlPartStore(workbook);
            string pdfId = Guid.NewGuid().ToString("D");
            string allocatedName = AllocateName(store, name);
            var pdf = new PdfDocument(pdfId, allocatedName, base64, folderId, DateTime.UtcNow, fileSizeBytes)
            {
                OcrStatus = string.IsNullOrWhiteSpace(ocrStatus) ? PdfStatus.None : ocrStatus,
            };

            store.UpsertPdf(pdf);
            return pdfId;
        }

        /// <summary>Embeds PDF bytes provided as a base64 string without reading from disk.</summary>
        /// <returns>The GUID of the newly stored PDF.</returns>
        public string AddEmbeddedPdfFromBase64(Excel.Workbook workbook, string name, string base64, string folderId = null)
        {
            if (workbook == null)
                throw new ArgumentNullException(nameof(workbook));
            if (string.IsNullOrWhiteSpace(name))
                throw new ArgumentException("PDF name must be non-empty.", nameof(name));
            if (base64 == null)
                throw new ArgumentNullException(nameof(base64));

            WorkbookProtectionGuard.ThrowIfStructureProtected(workbook);

            long fileSizeBytes = 0;
            try { fileSizeBytes = Convert.FromBase64String(base64).LongLength; } catch { }

            var store = new TalliarkCustomXmlPartStore(workbook);
            string pdfId = Guid.NewGuid().ToString("D");
            string allocatedName = AllocateName(store, name);
            var pdf = new PdfDocument(pdfId, allocatedName, base64, folderId, DateTime.UtcNow, fileSizeBytes)
            {
                OcrStatus = PdfTextLayerDetector.ClassifyFromBase64(base64),
            };

            store.UpsertPdf(pdf);
            return pdfId;
        }

        /// <summary>
        /// Allocates a workbook-wide display name for an import. Names are compared without
        /// case so documents that differ only by casing do not become indistinguishable in
        /// the file manager and viewer selectors. The suffix precedes the final extension:
        /// <c>Report.pdf</c>, <c>Report (1).pdf</c>, <c>Report (2).pdf</c>.
        /// </summary>
        private static string AllocateName(TalliarkCustomXmlPartStore store, string requestedName)
        {
            var usedNames = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (PdfMetadata existing in store.LoadContent().Pdfs)
            {
                if (existing != null && !string.IsNullOrEmpty(existing.Name))
                    usedNames.Add(existing.Name);
            }

            return AllocateName(requestedName, usedNames);
        }

        internal static string AllocateName(string requestedName, ISet<string> usedNames)
        {
            if (!usedNames.Contains(requestedName)) return requestedName;

            string extension;
            try
            {
                extension = Path.GetExtension(requestedName) ?? string.Empty;
            }
            catch (ArgumentException)
            {
                extension = string.Empty;
            }

            string stem = requestedName.Substring(0, requestedName.Length - extension.Length);
            for (int suffix = 1; suffix < int.MaxValue; suffix++)
            {
                string candidate = $"{stem} ({suffix}){extension}";
                if (!usedNames.Contains(candidate)) return candidate;
            }

            throw new InvalidOperationException("No available PDF name could be allocated.");
        }

    }
}
