using System;
using Talliark.Addin.Modules.CustomXml;
using Talliark.Addin.Modules.CustomXml.Models;
using Excel = Microsoft.Office.Interop.Excel;

namespace Talliark.Addin.Modules.Services
{
    /// <summary>Creates and replaces independent workbook-scoped Reconcile snapshots.</summary>
    internal sealed class ReconcileWorkspaceService
    {
        public ReconcileDocument ReplaceDocument(
            Excel.Workbook workbook, string role, string displayName, string pdfBase64,
            System.Collections.Generic.Dictionary<int, int> rotations = null)
        {
            if (workbook == null) throw new ArgumentNullException(nameof(workbook));
            if (!ReconcileRoles.IsValid(role)) throw new ArgumentException("A valid Reconcile document role is required.", nameof(role));
            if (string.IsNullOrWhiteSpace(displayName)) throw new ArgumentException("A display name is required.", nameof(displayName));
            if (string.IsNullOrEmpty(pdfBase64)) throw new ArgumentException("PDF bytes are required.", nameof(pdfBase64));
            WorkbookProtectionGuard.ThrowIfStructureProtected(workbook);

            var document = new ReconcileDocument
            {
                Id = Guid.NewGuid().ToString(),
                Role = role,
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
            documents.RemoveAll(d => d.Role == role);
            int order = role == ReconcileRoles.Primary ? 0 : role == ReconcileRoles.Comparison1 ? 1 : 2;
            int insertAt = documents.FindIndex(d => RoleOrder(d.Role) > order);
            documents.Insert(insertAt < 0 ? documents.Count : insertAt, document);
            workspace.Documents = documents;
            store.SaveReconcileWorkspace(workspace);
            return document;
        }

        public ReconcileDocument CopyFromImported(Excel.Workbook workbook, string role, string sourcePdfId)
        {
            var store = new TalliarkCustomXmlPartStore(workbook);
            if (!store.TryGetPdf(sourcePdfId, out PdfDocument source))
                throw new InvalidOperationException("The selected imported document no longer exists.");
            // Copy values into a new concrete model; no ordinary id or part reference survives.
            return ReplaceDocument(workbook, role, source.Name, source.Base64, source.PageRotations);
        }

        public void CompleteSetup(Excel.Workbook workbook, string projectName)
        {
            if (workbook == null) throw new ArgumentNullException(nameof(workbook));
            if (string.IsNullOrWhiteSpace(projectName)) throw new ArgumentException("A project name is required.", nameof(projectName));
            string trimmed = projectName.Trim();
            if (trimmed.Length > 120) throw new ArgumentException("The project name must be 120 characters or fewer.", nameof(projectName));
            WorkbookProtectionGuard.ThrowIfStructureProtected(workbook);
            var store = new TalliarkCustomXmlPartStore(workbook);
            ReconcileWorkspace workspace = store.LoadReconcileWorkspace();
            if (workspace.Primary == null || workspace.Comparison1 == null)
                throw new InvalidOperationException("Select both the current and prior financial statements before scanning.");
            workspace.ProjectName = trimmed;
            store.SaveReconcileWorkspace(workspace);
        }

        private static int RoleOrder(string role) => role == ReconcileRoles.Primary ? 0 : role == ReconcileRoles.Comparison1 ? 1 : 2;
    }
}
