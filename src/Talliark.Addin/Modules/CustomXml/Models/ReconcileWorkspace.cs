using System;
using System.Collections.Generic;
using System.Linq;

namespace Talliark.Addin.Modules.CustomXml.Models
{
    public sealed class ReconcileWorkspace
    {
        public const uint CurrentVersion = 1;
        public uint Version { get; set; } = CurrentVersion;
        public string ProjectName { get; set; }
        public IList<ReconcileDocument> Documents { get; set; } = new List<ReconcileDocument>();

        public ReconcileDocument Primary => Documents?.FirstOrDefault(
            d => string.Equals(d.Role, ReconcileRoles.Primary, StringComparison.Ordinal));

        public ReconcileDocument Comparison1 => Documents?.FirstOrDefault(
            d => string.Equals(d.Role, ReconcileRoles.Comparison1, StringComparison.Ordinal));

        public void Validate()
        {
            if (ProjectName != null && (string.IsNullOrWhiteSpace(ProjectName) || ProjectName.Trim().Length > 120))
                throw new InvalidOperationException("A Reconcile project name must be between 1 and 120 characters.");
            var documents = Documents ?? new List<ReconcileDocument>();
            if (documents.Count > 3) throw new InvalidOperationException("A Reconcile workspace supports at most three document slots.");
            if (documents.Count(d => d.Role == ReconcileRoles.Primary) > 1)
                throw new InvalidOperationException("A Reconcile workspace supports exactly one primary slot.");
            if (documents.Count(d => d.Role == ReconcileRoles.Comparison1) > 1
                || documents.Count(d => d.Role == ReconcileRoles.Comparison2) > 1)
                throw new InvalidOperationException("A Reconcile workspace cannot contain duplicate comparison roles.");
            foreach (ReconcileDocument document in documents) document.Validate();
        }
    }

    public static class ReconcileRoles
    {
        public const string Primary = "primary";
        public const string Comparison1 = "comparison-1";
        public const string Comparison2 = "comparison-2";
        public static bool IsValid(string role) => role == Primary || role == Comparison1 || role == Comparison2;
    }

    public sealed class ReconcileDocument
    {
        public string Id { get; set; }
        public string Role { get; set; }
        public string DisplayName { get; set; }
        public ReconcileDocumentVersion Version { get; set; }

        internal void Validate()
        {
            if (string.IsNullOrWhiteSpace(Id) || !ReconcileRoles.IsValid(Role)
                || string.IsNullOrWhiteSpace(DisplayName) || Version == null
                || string.IsNullOrWhiteSpace(Version.Id))
                throw new InvalidOperationException("The Reconcile workspace contains an invalid document slot.");
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
