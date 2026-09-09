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
            var root = new XElement(ns + "ReconcileWorkspace",
                new XAttribute("version", workspace.Version));
            if (!string.IsNullOrWhiteSpace(workspace.ProjectName))
                root.Add(new XAttribute("projectName", workspace.ProjectName.Trim()));

            // The primary slot is the versioned current statement.
            if (workspace.Primary != null)
            {
                ReconcileDocumentVersion version = workspace.Primary.Version;
                var versionElement = new XElement(ns + "Version",
                    new XAttribute("id", version.Id),
                    new XAttribute("importedAt", version.ImportedAt.ToUniversalTime().ToString("o", CultureInfo.InvariantCulture)));
                AddRotations(versionElement, ns, version.PageRotations);
                AddArtifacts(versionElement, ns, version);
                if (!string.IsNullOrEmpty(version.ReconcileBase64))
                    versionElement.Add(new XElement(ns + TalliarkXml.ReconcileBase64ElementName, version.ReconcileBase64));
                root.Add(new XElement(ns + "Primary",
                    new XAttribute("id", workspace.Primary.Id),
                    new XAttribute("displayName", workspace.Primary.DisplayName), versionElement));
            }

            // Comparison slots carry no version identity, but a scan of the
            // snapshot stores the same OCR and Reconcile artifacts as the primary.
            if (workspace.Comparisons != null && workspace.Comparisons.Count > 0)
            {
                var comparisons = new XElement(ns + "Comparisons");
                foreach (ReconcileComparison comparison in workspace.Comparisons)
                {
                    var element = new XElement(ns + "Comparison",
                        new XAttribute("id", comparison.Id),
                        new XAttribute("role", comparison.Role),
                        new XAttribute("displayName", comparison.DisplayName),
                        new XAttribute("importedAt", comparison.ImportedAt.ToUniversalTime().ToString("o", CultureInfo.InvariantCulture)));
                    AddRotations(element, ns, comparison.PageRotations);
                    element.Add(new XElement(ns + TalliarkXml.Base64ElementName, comparison.PdfBase64));
                    AddComparisonArtifacts(element, ns, comparison);
                    comparisons.Add(element);
                }
                root.Add(comparisons);
            }
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
                ProjectName = (string)root.Attribute("projectName"),
            };

            XElement primaryElement = root.Element(ns + "Primary");
            if (primaryElement != null)
            {
                XElement v = primaryElement.Element(ns + "Version");
                if (v == null) throw new InvalidOperationException("Reconcile primary document is missing its current version.");
                workspace.Primary = new ReconcileDocument
                {
                    Id = (string)primaryElement.Attribute("id"),
                    Role = ReconcileRoles.Primary,
                    DisplayName = (string)primaryElement.Attribute("displayName"),
                    Version = new ReconcileDocumentVersion
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
                    }
                };
            }

            var comparisons = new List<ReconcileComparison>();
            foreach (XElement c in root.Element(ns + "Comparisons")?.Elements(ns + "Comparison") ?? Enumerable.Empty<XElement>())
            {
                comparisons.Add(new ReconcileComparison
                {
                    Id = (string)c.Attribute("id"),
                    Role = (string)c.Attribute("role"),
                    DisplayName = (string)c.Attribute("displayName"),
                    ImportedAt = ParseDate((string)c.Attribute("importedAt")),
                    PageRotations = ParseRotations(c, ns),
                    PdfBase64 = Value(c, ns, TalliarkXml.Base64ElementName) ?? string.Empty,
                    GeometryBase64 = Value(c, ns, TalliarkXml.GeometryBase64ElementName),
                    TableStructureBase64 = Value(c, ns, TalliarkXml.TableStructureBase64ElementName),
                    DocumentValuesBase64 = Value(c, ns, TalliarkXml.DocumentValuesBase64ElementName),
                    FinancialStructureBase64 = Value(c, ns, TalliarkXml.FinancialStructureBase64ElementName),
                    ReconcileBase64 = Value(c, ns, TalliarkXml.ReconcileBase64ElementName),
                });
            }
            workspace.Comparisons = comparisons;
            workspace.Validate();
            return workspace;
        }

        private static void AddRotations(XElement parent, XNamespace ns, Dictionary<int, int> rotations)
        {
            if (rotations == null || rotations.Count == 0) return;
            parent.Add(new XElement(ns + TalliarkXml.PageRotationsElementName,
                rotations.Where(x => x.Value != 0).OrderBy(x => x.Key).Select(x =>
                    new XElement(ns + TalliarkXml.PageRotationElementName,
                        new XAttribute(TalliarkXml.PageIndexAttribute, x.Key),
                        new XAttribute(TalliarkXml.RotationAttribute, x.Value)))));
        }

        private static void AddArtifacts(XElement parent, XNamespace ns, PdfArtifactParts parts)
        {
            parent.Add(new XElement(ns + TalliarkXml.Base64ElementName, parts.Base64 ?? string.Empty));
            Add(parent, ns, TalliarkXml.GeometryBase64ElementName, parts.GeometryBase64);
            Add(parent, ns, TalliarkXml.TableStructureBase64ElementName, parts.TableStructureBase64);
            Add(parent, ns, TalliarkXml.DocumentValuesBase64ElementName, parts.DocumentValuesBase64);
            Add(parent, ns, TalliarkXml.FinancialStructureBase64ElementName, parts.FinancialStructureBase64);
        }

        private static void AddComparisonArtifacts(XElement parent, XNamespace ns, ReconcileComparison comparison)
        {
            Add(parent, ns, TalliarkXml.GeometryBase64ElementName, comparison.GeometryBase64);
            Add(parent, ns, TalliarkXml.TableStructureBase64ElementName, comparison.TableStructureBase64);
            Add(parent, ns, TalliarkXml.DocumentValuesBase64ElementName, comparison.DocumentValuesBase64);
            Add(parent, ns, TalliarkXml.FinancialStructureBase64ElementName, comparison.FinancialStructureBase64);
            Add(parent, ns, TalliarkXml.ReconcileBase64ElementName, comparison.ReconcileBase64);
        }

        private static void Add(XElement p, XNamespace ns, string name, string value) { if (!string.IsNullOrEmpty(value)) p.Add(new XElement(ns + name, value)); }
        private static string Value(XElement p, XNamespace ns, string name) { string v = p.Element(ns + name)?.Value; return string.IsNullOrEmpty(v) ? null : v; }
        private static DateTime ParseDate(string value) { return DateTime.Parse(value, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind); }
        private static uint ParseUInt(string value, uint fallback) { return uint.TryParse(value, out uint parsed) ? parsed : fallback; }
        private static Dictionary<int, int> ParseRotations(XElement parent, XNamespace ns)
        {
            var result = new Dictionary<int, int>();
            foreach (XElement p in parent.Element(ns + TalliarkXml.PageRotationsElementName)?.Elements(ns + TalliarkXml.PageRotationElementName) ?? Enumerable.Empty<XElement>())
                if (int.TryParse((string)p.Attribute(TalliarkXml.PageIndexAttribute), out int index)
                    && int.TryParse((string)p.Attribute(TalliarkXml.RotationAttribute), out int rotation)) result[index] = rotation;
            return result;
        }
    }
}