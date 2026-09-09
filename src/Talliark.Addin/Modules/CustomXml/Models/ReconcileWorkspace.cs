using System;
using System.Collections.Generic;
using System.Linq;

namespace Talliark.Addin.Modules.CustomXml.Models
{
    /// <summary>
    /// Workbook-scoped Reconcile project. The primary slot holds the versioned
    /// current statement; each comparison slot holds an unversioned prior-period
    /// snapshot. A scan of either stores the analysis artifacts on that slot.
    /// </summary>
    public sealed class ReconcileWorkspace
    {
        public const uint CurrentVersion = 1;
        public uint Version { get; set; } = CurrentVersion;
        public string ProjectName { get; set; }
        public ReconcileDocument Primary { get; set; }
        public IList<ReconcileComparison> Comparisons { get; set; } = new List<ReconcileComparison>();

        public ReconcileComparison Comparison1 => Comparisons?.FirstOrDefault(
            c => string.Equals(c.Role, ReconcileRoles.Comparison1, StringComparison.Ordinal));

        public void Validate()
        {
            if (ProjectName != null && (string.IsNullOrWhiteSpace(ProjectName) || ProjectName.Trim().Length > 120))
                throw new InvalidOperationException("A Reconcile project name must be between 1 and 120 characters.");
            if (Primary != null) Primary.Validate();
            var comparisons = Comparisons ?? new List<ReconcileComparison>();
            if (comparisons.Count > 2) throw new InvalidOperationException("A Reconcile workspace supports at most two comparison slots.");
            if (comparisons.Count(c => c.Role == ReconcileRoles.Comparison1) > 1
                || comparisons.Count(c => c.Role == ReconcileRoles.Comparison2) > 1)
                throw new InvalidOperationException("A Reconcile workspace cannot contain duplicate comparison roles.");
            foreach (ReconcileComparison comparison in comparisons) comparison.Validate();
        }
    }

    public static class ReconcileRoles
    {
        public const string Primary = "primary";
        public const string Comparison1 = "comparison-1";
        public const string Comparison2 = "comparison-2";
        public static bool IsValid(string role) => role == Primary || role == Comparison1 || role == Comparison2;
        public static bool IsComparison(string role) => role == Comparison1 || role == Comparison2;
    }

    /// <summary>
    /// The versioned current statement. A scan stores the analysis artifacts and
    /// the stored result on the current version.
    /// </summary>
    public sealed class ReconcileDocument
    {
        public string Id { get; set; }
        public string Role { get; set; }
        public string DisplayName { get; set; }
        public ReconcileDocumentVersion Version { get; set; }

        internal void Validate()
        {
            if (string.IsNullOrWhiteSpace(Id) || Role != ReconcileRoles.Primary
                || string.IsNullOrWhiteSpace(DisplayName) || Version == null
                || string.IsNullOrWhiteSpace(Version.Id))
                throw new InvalidOperationException("The Reconcile workspace contains an invalid primary slot.");
        }
    }

    /// <summary>
    /// A prior-period snapshot for comparison. It carries no version identity:
    /// the slot always refers to the current snapshot for that period, and
    /// re-adding a statement replaces the snapshot and its stored scan
    /// wholesale rather than creating a version. A scan of the snapshot still
    /// stores the same OCR and Reconcile artifacts the primary carries.
    /// </summary>
    public sealed class ReconcileComparison
    {
        public string Id { get; set; }
        public string Role { get; set; }
        public string DisplayName { get; set; }
        public string PdfBase64 { get; set; }
        public Dictionary<int, int> PageRotations { get; set; }
        public DateTime ImportedAt { get; set; }
        public string GeometryBase64 { get; set; }
        public string TableStructureBase64 { get; set; }
        public string DocumentValuesBase64 { get; set; }
        public string FinancialStructureBase64 { get; set; }
        public string ReconcileBase64 { get; set; }

        internal void Validate()
        {
            if (string.IsNullOrWhiteSpace(Id) || !ReconcileRoles.IsComparison(Role)
                || string.IsNullOrWhiteSpace(DisplayName) || string.IsNullOrEmpty(PdfBase64))
                throw new InvalidOperationException("The Reconcile workspace contains an invalid comparison slot.");
        }
    }

    public sealed class ReconcileDocumentVersion : PdfArtifactParts
    {
        public string Id { get; set; }
        public DateTime ImportedAt { get; set; }
        public Dictionary<int, int> PageRotations { get; set; }
        public string ReconcileBase64 { get; set; }
    }
}