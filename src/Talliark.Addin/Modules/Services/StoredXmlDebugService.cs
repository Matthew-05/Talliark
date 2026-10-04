using System;
using System.Collections.Generic;
using System.Xml;
using System.Xml.Linq;
using Talliark.Addin.Modules.CustomXml;
using Excel = Microsoft.Office.Interop.Excel;

namespace Talliark.Addin.Modules.Services
{
    /// <summary>Captures a workbook's Talliark XML for the developer inspector.</summary>
    internal sealed class StoredXmlDebugService
    {
        internal IList<StoredXmlDebugEntry> Capture(Excel.Workbook workbook)
        {
            if (workbook == null) throw new ArgumentNullException(nameof(workbook));

            IList<TalliarkXmlPartSnapshot> snapshots =
                new TalliarkCustomXmlPartStore(workbook).LoadXmlPartSnapshots();
            var entries = new List<StoredXmlDebugEntry>(snapshots.Count);
            var nameCounts = new Dictionary<string, int>(StringComparer.Ordinal);

            foreach (TalliarkXmlPartSnapshot snapshot in snapshots)
            {
                string baseName = GetDisplayName(snapshot.NamespaceUri);
                nameCounts.TryGetValue(baseName, out int count);
                count++;
                nameCounts[baseName] = count;

                string displayName = count == 1 ? baseName : $"{baseName} ({count})";
                entries.Add(new StoredXmlDebugEntry(
                    displayName,
                    snapshot.NamespaceUri,
                    FormatXmlForDisplay(snapshot.Xml),
                    snapshot.Xml.Length));
            }

            return entries;
        }

        private static string GetDisplayName(string namespaceUri)
        {
            if (string.Equals(namespaceUri, TalliarkXml.ContentNamespaceUri, StringComparison.Ordinal))
                return "Content";
            if (string.Equals(namespaceUri, TalliarkXml.LinksNamespaceUri, StringComparison.Ordinal))
                return "Links";
            if (string.Equals(namespaceUri, TalliarkXml.ReconcileNamespaceUri, StringComparison.Ordinal))
                return "Reconcile";
            if (namespaceUri.StartsWith(TalliarkXml.PdfDataNamespaceBase, StringComparison.Ordinal))
                return "PDF data — " + namespaceUri.Substring(TalliarkXml.PdfDataNamespaceBase.Length);

            return namespaceUri;
        }

        /// <summary>
        /// Indents the XML for the read-only viewer. The stored text remains untouched;
        /// malformed XML is still shown verbatim so the inspector can diagnose it.
        /// </summary>
        internal static string FormatXmlForDisplay(string xml)
        {
            if (string.IsNullOrWhiteSpace(xml)) return string.Empty;

            try
            {
                return XDocument.Parse(xml).ToString();
            }
            catch (XmlException)
            {
                return xml;
            }
        }
    }

    internal sealed class StoredXmlDebugEntry
    {
        internal StoredXmlDebugEntry(
            string displayName, string namespaceUri, string xml, int storedCharacterCount)
        {
            DisplayName = displayName;
            NamespaceUri = namespaceUri;
            Xml = xml ?? string.Empty;
            StoredCharacterCount = storedCharacterCount;
        }

        internal string DisplayName { get; }

        internal string NamespaceUri { get; }

        internal string Xml { get; }

        internal int StoredCharacterCount { get; }
    }
}
