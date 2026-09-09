using System;
using Talliark.Addin.Modules.CustomXml;
using Talliark.Addin.Modules.CustomXml.Models;
using Excel = Microsoft.Office.Interop.Excel;

namespace Talliark.Addin.Modules.Services
{
    /// <summary>Creates and replaces independent workbook-scoped Reconcile snapshots.</summary>
    internal sealed class ReconcileWorkspaceService
    {
        /// <summary>
        /// Replaces one slot with an independent snapshot. The primary slot is
        /// versioned — a new document identity and version identity carry the
        /// PDF bytes. A comparison slot is unversioned: re-adding a statement
        /// for that period replaces the slot's snapshot wholesale.
        /// </summary>
        public void ReplaceDocument(
            Excel.Workbook workbook, string role, string displayName, string pdfBase64,
            System.Collections.Generic.Dictionary<int, int> rotations = null)
        {
            if (workbook == null) throw new ArgumentNullException(nameof(workbook));
            if (!ReconcileRoles.IsValid(role)) throw new ArgumentException("A valid Reconcile document role is required.", nameof(role));
            if (string.IsNullOrWhiteSpace(displayName)) throw new ArgumentException("A display name is required.", nameof(displayName));
            if (string.IsNullOrEmpty(pdfBase64)) throw new ArgumentException("PDF bytes are required.", nameof(pdfBase64));
            WorkbookProtectionGuard.ThrowIfStructureProtected(workbook);

            var store = new TalliarkCustomXmlPartStore(workbook);
            ReconcileWorkspace workspace = store.LoadReconcileWorkspace();

            if (role == ReconcileRoles.Primary)
            {
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
                workspace.Primary = document;
                store.SaveReconcileWorkspace(workspace);
                return;
            }

            var comparison = new ReconcileComparison
            {
                Id = Guid.NewGuid().ToString(),
                Role = role,
                DisplayName = displayName.Trim(),
                ImportedAt = DateTime.UtcNow,
                PdfBase64 = pdfBase64,
                PageRotations = rotations == null
                    ? null
                    : new System.Collections.Generic.Dictionary<int, int>(rotations),
            };
            var comparisons = new System.Collections.Generic.List<ReconcileComparison>(
                workspace.Comparisons ?? new ReconcileComparison[0]);
            comparisons.RemoveAll(c => c.Role == role);
            int order = role == ReconcileRoles.Comparison1 ? 0 : 1;
            int insertAt = comparisons.FindIndex(c => RoleOrder(c.Role) > order);
            comparisons.Insert(insertAt < 0 ? comparisons.Count : insertAt, comparison);
            workspace.Comparisons = comparisons;
            store.SaveReconcileWorkspace(workspace);
        }

        public void CopyFromImported(Excel.Workbook workbook, string role, string sourcePdfId)
        {
            var store = new TalliarkCustomXmlPartStore(workbook);
            if (!store.TryGetPdf(sourcePdfId, out PdfDocument source))
                throw new InvalidOperationException("The selected imported document no longer exists.");
            // Copy values into a new concrete model; no ordinary id or part reference survives.
            ReplaceDocument(workbook, role, source.Name, source.Base64, source.PageRotations);
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
            if (workspace.Primary == null)
                throw new InvalidOperationException("Select a current financial statement before scanning.");
            workspace.ProjectName = trimmed;
            store.SaveReconcileWorkspace(workspace);
        }

        private static int RoleOrder(string role) => role == ReconcileRoles.Comparison1 ? 0 : 1;
    }
}