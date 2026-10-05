using System;
using System.Collections.Generic;
using System.Linq;
using System.Xml.Linq;

namespace Talliark.Addin.Modules.CustomXml.Serialization
{
    /// <summary>Independent versioned review store; unchanged v1 workspaces stay readable.</summary>
    internal static class TalliarkReconcileReviewSerializer
    {
        public const string NamespaceUri = "urn:talliark:schemas:storage:1:reconcile-review";

        public static IDictionary<string, string> FromXml(string xml)
        {
            var output = new Dictionary<string, string>(StringComparer.Ordinal);
            if (string.IsNullOrEmpty(xml)) return output;
            XElement root = XDocument.Parse(xml).Root;
            if (root == null || root.Name != XName.Get("ReconcileReviews", NamespaceUri)
                || (string)root.Attribute("version") != "1")
                throw new InvalidOperationException("Unsupported Reconcile review store. Existing data was preserved.");
            XNamespace ns = NamespaceUri;
            foreach (XElement element in root.Elements(ns + "Document"))
            {
                string id = (string)element.Attribute("id");
                string value = (string)element.Element(ns + "ReviewBase64");
                if (string.IsNullOrWhiteSpace(id) || string.IsNullOrEmpty(value) || output.ContainsKey(id))
                    throw new InvalidOperationException("Invalid Reconcile review store. Existing data was preserved.");
                output.Add(id, value);
            }
            return output;
        }

        public static string ToXml(IDictionary<string, string> reviews)
        {
            XNamespace ns = NamespaceUri;
            return new XElement(ns + "ReconcileReviews", new XAttribute("version", 1),
                reviews.OrderBy(item => item.Key, StringComparer.Ordinal).Select(item =>
                    new XElement(ns + "Document", new XAttribute("id", item.Key),
                        new XElement(ns + "ReviewBase64", item.Value)))).ToString(SaveOptions.DisableFormatting);
        }
    }
}
