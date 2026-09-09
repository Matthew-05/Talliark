using System.Xml.Linq;
using Talliark.Addin.Modules.CustomXml.Models;

namespace Talliark.Addin.Modules.CustomXml.Serialization
{
    internal static class TalliarkPdfBinarySerializer
    {
        public static string ToXml(string pdfId, PdfBinaryParts parts)
        {
            XNamespace ns = XNamespace.Get(TalliarkXml.PdfDataNamespaceUri(pdfId));

            var root = new XElement(ns + TalliarkXml.PdfDataRootElementName,
                new XElement(ns + TalliarkXml.Base64ElementName, parts?.Base64 ?? string.Empty));

            if (!string.IsNullOrEmpty(parts?.GeometryBase64))
                root.Add(new XElement(ns + TalliarkXml.GeometryBase64ElementName, parts.GeometryBase64));

            if (!string.IsNullOrEmpty(parts?.TableStructureBase64))
                root.Add(new XElement(ns + TalliarkXml.TableStructureBase64ElementName, parts.TableStructureBase64));

            if (!string.IsNullOrEmpty(parts?.DocumentValuesBase64))
                root.Add(new XElement(ns + TalliarkXml.DocumentValuesBase64ElementName, parts.DocumentValuesBase64));
            if (!string.IsNullOrEmpty(parts?.FinancialStructureBase64))
                root.Add(new XElement(ns + TalliarkXml.FinancialStructureBase64ElementName, parts.FinancialStructureBase64));

            return new XDocument(new XDeclaration("1.0", "utf-8", null), root)
                .ToString(SaveOptions.DisableFormatting);
        }

        public static PdfBinaryParts FromXml(string xml)
        {
            var parts = new PdfBinaryParts();

            if (string.IsNullOrWhiteSpace(xml))
                return parts;

            XDocument doc = XDocument.Parse(xml);
            XElement root = doc.Root;
            if (root == null)
                return parts;

            XNamespace ns = root.Name.Namespace;
            parts.Base64 = root.Element(ns + TalliarkXml.Base64ElementName)?.Value ?? string.Empty;
            parts.GeometryBase64 = NullIfEmpty(root.Element(ns + TalliarkXml.GeometryBase64ElementName)?.Value);
            parts.TableStructureBase64 = NullIfEmpty(root.Element(ns + TalliarkXml.TableStructureBase64ElementName)?.Value);
            parts.DocumentValuesBase64 = NullIfEmpty(root.Element(ns + TalliarkXml.DocumentValuesBase64ElementName)?.Value);
            parts.FinancialStructureBase64 = NullIfEmpty(root.Element(ns + TalliarkXml.FinancialStructureBase64ElementName)?.Value);
            return parts;
        }

        private static string NullIfEmpty(string value) => string.IsNullOrEmpty(value) ? null : value;
    }
}
