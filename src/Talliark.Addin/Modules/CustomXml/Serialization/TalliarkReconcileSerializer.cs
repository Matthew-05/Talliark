using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Xml.Linq;
using Talliark.Addin.Modules.CustomXml.Models;

namespace Talliark.Addin.Modules.CustomXml.Serialization
{
    internal static class TalliarkReconcileSerializer
    {
        public static string ToXml(ReconcileWorkspace workspace)
        {
            if (workspace == null) throw new ArgumentNullException(nameof(workspace));
            workspace.Validate();
            XNamespace ns = TalliarkXml.ReconcileNs;
            var documents = new XElement(ns + "Documents");
            foreach (ReconcileDocument document in workspace.Documents ?? new List<ReconcileDocument>())
            {
                ReconcileDocumentVersion version = document.Version;
                var versionElement = new XElement(ns + "Version",
                    new XAttribute("id", version.Id),
                    new XAttribute("importedAt", version.ImportedAt.ToUniversalTime().ToString("o", CultureInfo.InvariantCulture)));
                if (version.PageRotations != null && version.PageRotations.Count > 0)
                {
                    versionElement.Add(new XElement(ns + TalliarkXml.PageRotationsElementName,
                        version.PageRotations.Where(x => x.Value != 0).OrderBy(x => x.Key).Select(x =>
                            new XElement(ns + TalliarkXml.PageRotationElementName,
                                new XAttribute(TalliarkXml.PageIndexAttribute, x.Key),
                                new XAttribute(TalliarkXml.RotationAttribute, x.Value)))));
                }
                AddArtifacts(versionElement, ns, version);
                if (!string.IsNullOrEmpty(version.ReconcileBase64))
                    versionElement.Add(new XElement(ns + TalliarkXml.ReconcileBase64ElementName, version.ReconcileBase64));
                documents.Add(new XElement(ns + "Document",
                    new XAttribute("id", document.Id), new XAttribute("role", document.Role),
                    new XAttribute("displayName", document.DisplayName), versionElement));
            }
            var root = new XElement(ns + "ReconcileWorkspace",
                new XAttribute("version", workspace.Version), documents);
            return new XDocument(new XDeclaration("1.0", "utf-8", null), root).ToString(SaveOptions.DisableFormatting);
        }

        public static ReconcileWorkspace FromXml(string xml)
        {
            if (string.IsNullOrWhiteSpace(xml)) return new ReconcileWorkspace();
            XDocument xdoc = XDocument.Parse(xml);
            XElement root = xdoc.Root ?? throw new InvalidOperationException("Reconcile workspace has no root element.");
            XNamespace ns = root.Name.Namespace;
            var workspace = new ReconcileWorkspace
            {
                Version = ParseUInt(root.Attribute("version")?.Value, ReconcileWorkspace.CurrentVersion),
                Documents = new List<ReconcileDocument>()
            };
            foreach (XElement element in root.Element(ns + "Documents")?.Elements(ns + "Document") ?? Enumerable.Empty<XElement>())
            {
                XElement v = element.Element(ns + "Version");
                if (v == null) throw new InvalidOperationException("Reconcile document is missing its current version.");
                var version = new ReconcileDocumentVersion
                {
                    Id = (string)v.Attribute("id"),
                    ImportedAt = ParseDate((string)v.Attribute("importedAt")),
                    PageRotations = ParseRotations(v, ns),
                    Base64 = Value(v, ns, TalliarkXml.Base64ElementName) ?? string.Empty,
                    GeometryBase64 = Value(v, ns, TalliarkXml.GeometryBase64ElementName),
                    TableStructureBase64 = Value(v, ns, TalliarkXml.TableStructureBase64ElementName),
                    DocumentValuesBase64 = Value(v, ns, TalliarkXml.DocumentValuesBase64ElementName),
                    FinancialStructureBase64 = Value(v, ns, TalliarkXml.FinancialStructureBase64ElementName),
                    ReconcileBase64 = Value(v, ns, TalliarkXml.ReconcileBase64ElementName),
                };
                workspace.Documents.Add(new ReconcileDocument
                {
                    Id = (string)element.Attribute("id"), Role = (string)element.Attribute("role"),
                    DisplayName = (string)element.Attribute("displayName"), Version = version
                });
            }
            workspace.Validate();
            return workspace;
        }

        private static void AddArtifacts(XElement parent, XNamespace ns, PdfArtifactParts parts)
        {
            parent.Add(new XElement(ns + TalliarkXml.Base64ElementName, parts.Base64 ?? string.Empty));
            Add(parent, ns, TalliarkXml.GeometryBase64ElementName, parts.GeometryBase64);
            Add(parent, ns, TalliarkXml.TableStructureBase64ElementName, parts.TableStructureBase64);
            Add(parent, ns, TalliarkXml.DocumentValuesBase64ElementName, parts.DocumentValuesBase64);
            Add(parent, ns, TalliarkXml.FinancialStructureBase64ElementName, parts.FinancialStructureBase64);
        }
        private static void Add(XElement p, XNamespace ns, string name, string value) { if (!string.IsNullOrEmpty(value)) p.Add(new XElement(ns + name, value)); }
        private static string Value(XElement p, XNamespace ns, string name) { string v = p.Element(ns + name)?.Value; return string.IsNullOrEmpty(v) ? null : v; }
        private static DateTime ParseDate(string value) { return DateTime.Parse(value, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind); }
        private static uint ParseUInt(string value, uint fallback) { return uint.TryParse(value, out uint parsed) ? parsed : fallback; }
        private static Dictionary<int, int> ParseRotations(XElement v, XNamespace ns)
        {
            var result = new Dictionary<int, int>();
            foreach (XElement p in v.Element(ns + TalliarkXml.PageRotationsElementName)?.Elements(ns + TalliarkXml.PageRotationElementName) ?? Enumerable.Empty<XElement>())
                if (int.TryParse((string)p.Attribute(TalliarkXml.PageIndexAttribute), out int index)
                    && int.TryParse((string)p.Attribute(TalliarkXml.RotationAttribute), out int rotation)) result[index] = rotation;
            return result;
        }
    }
}
