using System.Xml.Linq;

namespace Talliark.Addin.Modules.CustomXml
{
    internal static class TalliarkXml
    {
        public const string StorageNamespacePrefix = "urn:talliark:schemas:storage:";

        public const string ContentNamespaceUri = "urn:talliark:schemas:storage:1:content";

        public static readonly XNamespace ContentNs = ContentNamespaceUri;

        public const string ContentRootElementName = "TalliarkContent";

        public const string LinksNamespaceUri = "urn:talliark:schemas:storage:1:links";

        public static readonly XNamespace LinksNs = LinksNamespaceUri;

        public const string LinksRootElementName = "TalliarkLinks";

        public const string ReconcileNamespaceUri = "urn:talliark:schemas:storage:1:reconcile";
        public static readonly XNamespace ReconcileNs = ReconcileNamespaceUri;

        public const uint SchemaVersion = 1;

        public const string FoldersElementName = "Folders";

        public const string FolderElementName = "Folder";

        public const string PdfsElementName = "Pdfs";

        public const string PdfElementName = "Pdf";

        public const string LinkedRectanglesElementName = "LinkedRectangles";

        public const string LinkedRectangleElementName = "LinkedRectangle";

        public const string CellElementName = "Cell";

        public const string RectElementName = "Rect";

        public const string PdfIdAttribute = "pdfId";

        public const string FolderIdAttribute = "folderId";

        public const string DateAddedAttribute = "dateAdded";

        public const string FileSizeBytesAttribute = "fileSizeBytes";

        public const string OcrStatusAttribute = "ocrStatus";

        public const string GeometryBase64Attribute = "geometryBase64";

        // Per-PDF binary XML parts
        public const string PdfDataNamespaceBase = "urn:talliark:schemas:storage:1:pdf-data:";

        public static string PdfDataNamespaceUri(string pdfId) => PdfDataNamespaceBase + pdfId;

        public const string PdfDataRootElementName = "PdfData";

        public const string Base64ElementName = "Base64";

        public const string GeometryBase64ElementName = "GeometryBase64";

        public const string TableStructureBase64ElementName = "TableStructureBase64";

        public const string DocumentValuesBase64ElementName = "DocumentValuesBase64";

        public const string FinancialStructureBase64ElementName = "FinancialStructureBase64";

        public const string ReconcileBase64ElementName = "ReconcileBase64";

        // Page rotation storage
        public const string PageRotationsElementName = "PageRotations";

        public const string PageRotationElementName = "Page";

        public const string PageIndexAttribute = "index";

        public const string RotationAttribute = "rotation";

        public static bool IsStorageNamespace(string namespaceUri)
        {
            return !string.IsNullOrWhiteSpace(namespaceUri)
                && namespaceUri.StartsWith(StorageNamespacePrefix, System.StringComparison.Ordinal);
        }
    }
}
