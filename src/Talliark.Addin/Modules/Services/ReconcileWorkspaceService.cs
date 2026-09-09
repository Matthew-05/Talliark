using System;
using Talliark.Addin.Modules.CustomXml;
using Talliark.Addin.Modules.CustomXml.Models;
using Excel = Microsoft.Office.Interop.Excel;

namespace Talliark.Addin.Modules.Services
{
    /// <summary>Creates and replaces the independent workbook-scoped Reconcile snapshot.</summary>
    internal sealed class ReconcileWorkspaceService
    {
        public ReconcileDocument ReplacePrimary(
            Excel.Workbook workbook, string displayName, string pdfBase64,
            System.Collections.Generic.Dictionary<int, int> rotations = null)
        {
            if (workbook == null) throw new ArgumentNullException(nameof(workbook));
            if (string.IsNullOrWhiteSpace(displayName)) throw new ArgumentException("A display name is required.", nameof(displayName));
            if (string.IsNullOrEmpty(pdfBase64)) throw new ArgumentException("PDF bytes are required.", nameof(pdfBase64));
            WorkbookProtectionGuard.ThrowIfStructureProtected(workbook);

            var document = new ReconcileDocument
            {
                Id = Guid.NewGuid().ToString(),
                Role = ReconcileRoles.Primary,
                DisplayName = displayName.Trim(),
                Version = new ReconcileDocumentVersion
                {
                    Id = Guid.NewGuid().ToString(),
                    ImportedAt = DateTime.UtcNow,
                    Base64 = pdfBase64,
                    PageRotations = rotations == null
                        ? null
                        : new System.Collections.Generic.Dictionary<int, int>(rotations),
                }
            };

            var store = new TalliarkCustomXmlPartStore(workbook);
            ReconcileWorkspace workspace = store.LoadReconcileWorkspace();
            var documents = new System.Collections.Generic.List<ReconcileDocument>(workspace.Documents ?? new ReconcileDocument[0]);
            documents.RemoveAll(d => d.Role == ReconcileRoles.Primary);
            documents.Insert(0, document);
            workspace.Documents = documents;
            store.SaveReconcileWorkspace(workspace);
            return document;
        }

        public ReconcileDocument CopyFromImported(Excel.Workbook workbook, string sourcePdfId)
        {
            var store = new TalliarkCustomXmlPartStore(workbook);
            if (!store.TryGetPdf(sourcePdfId, out PdfDocument source))
                throw new InvalidOperationException("The selected imported document no longer exists.");
            // Copy values into a new concrete model; no ordinary id or part reference survives.
            return ReplacePrimary(workbook, source.Name, source.Base64, source.PageRotations);
        }
    }
}
