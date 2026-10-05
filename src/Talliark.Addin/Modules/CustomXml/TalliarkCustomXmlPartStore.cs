using System;
using System.Collections.Generic;
using System.Linq;
using System.Runtime.InteropServices;
using System.Xml.Linq;
using Talliark.Addin.Modules.CustomXml.Models;
using Talliark.Addin.Modules.CustomXml.Serialization;
using Excel = Microsoft.Office.Interop.Excel;
using Office = Microsoft.Office.Core;

namespace Talliark.Addin.Modules.CustomXml
{
    public sealed class TalliarkCustomXmlPartStore
    {
        private readonly Excel.Workbook _workbook;

        public TalliarkCustomXmlPartStore(Excel.Workbook workbook)
        {
            _workbook = workbook ?? throw new ArgumentNullException(nameof(workbook));
        }

        // ── Content (metadata) part ───────────────────────────────────────────

        public TalliarkContent LoadContent()
        {
            Office.CustomXMLPart part = FindPartByNamespace(TalliarkXml.ContentNamespaceUri);
            if (part == null)
                return new TalliarkContent(TalliarkXml.SchemaVersion, new PdfFolder[0], new PdfMetadata[0]);

            string xml = part.XML;
            if (string.IsNullOrWhiteSpace(xml))
                return new TalliarkContent(TalliarkXml.SchemaVersion, new PdfFolder[0], new PdfMetadata[0]);

            try
            {
                return TalliarkContentSerializer.FromXDocument(XDocument.Parse(xml));
            }
            catch (System.Xml.XmlException ex)
            {
                throw new InvalidOperationException("Talliark content custom XML part contains invalid XML.", ex);
            }
        }

        public void SaveContent(TalliarkContent content)
        {
            if (content == null) throw new ArgumentNullException(nameof(content));
            string xml = TalliarkContentSerializer.ToXDocument(content).ToString(SaveOptions.DisableFormatting);
            ReplacePart(TalliarkXml.ContentNamespaceUri, xml);
        }

        // ── Per-PDF binary parts ──────────────────────────────────────────────

        public void SavePdfBinary(string id, PdfBinaryParts parts)
        {
            if (string.IsNullOrWhiteSpace(id))
                throw new ArgumentException("PDF id must be non-empty.", nameof(id));

            string xml = TalliarkPdfBinarySerializer.ToXml(id, parts);
            ReplacePart(TalliarkXml.PdfDataNamespaceUri(id), xml);
        }

        public bool TryLoadPdfBinary(string id, out PdfBinaryParts parts)
        {
            parts = new PdfBinaryParts();

            if (string.IsNullOrWhiteSpace(id))
                return false;

            Office.CustomXMLPart part = FindPartByNamespace(TalliarkXml.PdfDataNamespaceUri(id));
            if (part == null)
                return false;

            parts = TalliarkPdfBinarySerializer.FromXml(part.XML);
            return true;
        }

        public void DeletePdfBinary(string id)
        {
            if (string.IsNullOrWhiteSpace(id))
                return;

            DeletePart(TalliarkXml.PdfDataNamespaceUri(id));
        }

        // ── Convenience: full PDF (metadata + binary) ─────────────────────────

        public bool TryGetPdf(string id, out PdfDocument pdf)
        {
            if (string.IsNullOrWhiteSpace(id))
                throw new ArgumentException("PDF id must be non-empty.", nameof(id));

            TalliarkContent content = LoadContent();
            PdfMetadata metadata = content.Pdfs.FirstOrDefault(
                p => string.Equals(p.Id, id, StringComparison.Ordinal));

            if (metadata == null)
            {
                pdf = null;
                return false;
            }

            TryLoadPdfBinary(id, out PdfBinaryParts parts);
            pdf = new PdfDocument(metadata.Id, metadata.Name, parts.Base64 ?? string.Empty,
                metadata.FolderId, metadata.DateAdded, metadata.FileSizeBytes)
            {
                OcrStatus     = metadata.OcrStatus,
                GeometryBase64 = parts.GeometryBase64,
                TableStructureBase64 = parts.TableStructureBase64,
                DocumentValuesBase64 = parts.DocumentValuesBase64,
                FinancialStructureBase64 = parts.FinancialStructureBase64,
                PageRotations  = metadata.PageRotations,
            };
            return true;
        }

        public IList<PdfDocument> LoadAllPdfsWithBinary()
        {
            TalliarkContent content = LoadContent();
            var result = new List<PdfDocument>(content.Pdfs.Count);
            foreach (PdfMetadata m in content.Pdfs)
            {
                TryLoadPdfBinary(m.Id, out PdfBinaryParts parts);
                result.Add(new PdfDocument(m.Id, m.Name, parts.Base64 ?? string.Empty,
                    m.FolderId, m.DateAdded, m.FileSizeBytes)
                {
                    OcrStatus      = m.OcrStatus,
                    GeometryBase64 = parts.GeometryBase64,
                    TableStructureBase64 = parts.TableStructureBase64,
                    DocumentValuesBase64 = parts.DocumentValuesBase64,
                FinancialStructureBase64 = parts.FinancialStructureBase64,
                    PageRotations  = m.PageRotations,
                });
            }
            return result;
        }

        public void UpsertPdf(PdfDocument pdf)
        {
            if (pdf == null) throw new ArgumentNullException(nameof(pdf));

            var metadata = new PdfMetadata(pdf.Id, pdf.Name, pdf.FolderId, pdf.DateAdded, pdf.FileSizeBytes)
            {
                OcrStatus     = pdf.OcrStatus,
                PageRotations = pdf.PageRotations,
            };
            UpsertMetadata(metadata);
            SavePdfBinary(pdf.Id, new PdfBinaryParts
            {
                Base64 = pdf.Base64,
                GeometryBase64 = pdf.GeometryBase64,
                TableStructureBase64 = pdf.TableStructureBase64,
                DocumentValuesBase64 = pdf.DocumentValuesBase64,
                FinancialStructureBase64 = pdf.FinancialStructureBase64,
            });
        }

        // ── Workbook-scoped Reconcile workspace ──────────────────────────────────

        public ReconcileWorkspace LoadReconcileWorkspace()
        {
            string xml = LoadReconcileWorkspaceXml();
            if (string.IsNullOrWhiteSpace(xml)) return new ReconcileWorkspace();
            try { return TalliarkReconcileSerializer.FromXml(xml); }
            catch (System.Xml.XmlException ex)
            {
                throw new InvalidOperationException("Talliark Reconcile custom XML part contains invalid XML.", ex);
            }
        }

        // Exact source snapshot for review currentness checks. Reading it does
        // not deserialize PDFs or recompute geometry fingerprints.
        internal string LoadReconcileWorkspaceXml()
        {
            return FindPartByNamespace(TalliarkXml.ReconcileNamespaceUri)?.XML ?? string.Empty;
        }

        public void SaveReconcileWorkspace(ReconcileWorkspace workspace)
        {
            if (workspace == null) throw new ArgumentNullException(nameof(workspace));
            string xml = TalliarkReconcileSerializer.ToXml(workspace);
            ReplacePart(TalliarkXml.ReconcileNamespaceUri, xml);
        }

        public void DeleteReconcileWorkspace() => DeletePart(TalliarkXml.ReconcileNamespaceUri);

        public string LoadReconcileReview(string documentId)
        {
            Office.CustomXMLPart part = FindPartByNamespace(TalliarkReconcileReviewSerializer.NamespaceUri);
            var reviews = TalliarkReconcileReviewSerializer.FromXml(part?.XML);
            return reviews.TryGetValue(documentId, out string value) ? value : null;
        }

        public void SaveReconcileReview(string documentId, string base64)
        {
            if (string.IsNullOrWhiteSpace(documentId) || string.IsNullOrEmpty(base64))
                throw new ArgumentException("Document identity and review data are required.");
            Office.CustomXMLPart part = FindPartByNamespace(TalliarkReconcileReviewSerializer.NamespaceUri);
            var reviews = TalliarkReconcileReviewSerializer.FromXml(part?.XML);
            reviews[documentId] = base64;
            ReplacePart(TalliarkReconcileReviewSerializer.NamespaceUri, TalliarkReconcileReviewSerializer.ToXml(reviews));
        }

        // ── Metadata-only helpers ─────────────────────────────────────────────

        public bool TryGetMetadata(string id, out PdfMetadata metadata)
        {
            if (string.IsNullOrWhiteSpace(id))
                throw new ArgumentException("PDF id must be non-empty.", nameof(id));

            TalliarkContent content = LoadContent();
            metadata = content.Pdfs.FirstOrDefault(
                p => string.Equals(p.Id, id, StringComparison.Ordinal));
            return metadata != null;
        }

        public void UpsertMetadata(PdfMetadata metadata)
        {
            if (metadata == null) throw new ArgumentNullException(nameof(metadata));
            if (string.IsNullOrWhiteSpace(metadata.Id))
                throw new ArgumentException("PDF id must be non-empty.", nameof(metadata));

            TalliarkContent content = LoadContent();
            List<PdfMetadata> pdfs = content.Pdfs.ToList();
            int index = pdfs.FindIndex(p => string.Equals(p.Id, metadata.Id, StringComparison.Ordinal));
            if (index >= 0) pdfs[index] = metadata;
            else pdfs.Add(metadata);

            SaveContent(new TalliarkContent(content.Version, content.Folders, pdfs));
        }

        public bool RemovePdf(string id)
        {
            if (string.IsNullOrWhiteSpace(id))
                throw new ArgumentException("PDF id must be non-empty.", nameof(id));

            TalliarkContent content = LoadContent();
            List<PdfMetadata> pdfs = content.Pdfs
                .Where(p => !string.Equals(p.Id, id, StringComparison.Ordinal))
                .ToList();

            if (pdfs.Count == content.Pdfs.Count)
                return false;

            SaveContent(new TalliarkContent(content.Version, content.Folders, pdfs));
            DeletePdfBinary(id);
            return true;
        }

        // ── Folders ───────────────────────────────────────────────────────────

        public void UpsertFolder(PdfFolder folder)
        {
            if (folder == null) throw new ArgumentNullException(nameof(folder));
            if (string.IsNullOrWhiteSpace(folder.Id))
                throw new ArgumentException("Folder id must be non-empty.", nameof(folder));

            TalliarkContent content = LoadContent();
            List<PdfFolder> folders = content.Folders.ToList();
            int index = folders.FindIndex(f => string.Equals(f.Id, folder.Id, StringComparison.Ordinal));
            if (index >= 0) folders[index] = folder;
            else folders.Add(folder);

            SaveContent(new TalliarkContent(content.Version, folders, content.Pdfs));
        }

        public bool RemoveFolder(string id)
        {
            if (string.IsNullOrWhiteSpace(id))
                throw new ArgumentException("Folder id must be non-empty.", nameof(id));

            TalliarkContent content = LoadContent();
            List<PdfFolder> folders = content.Folders
                .Where(f => !string.Equals(f.Id, id, StringComparison.Ordinal))
                .ToList();

            if (folders.Count == content.Folders.Count)
                return false;

            // Move any PDFs in this folder to uncategorised
            List<PdfMetadata> pdfs = content.Pdfs.Select(p =>
            {
                if (!string.Equals(p.FolderId, id, StringComparison.Ordinal))
                    return p;
                return new PdfMetadata(p.Id, p.Name, null, p.DateAdded, p.FileSizeBytes)
                {
                    OcrStatus     = p.OcrStatus,
                    PageRotations = p.PageRotations,
                };
            }).ToList();

            SaveContent(new TalliarkContent(content.Version, folders, pdfs));
            return true;
        }

        // ── Links part ────────────────────────────────────────────────────────

        public IList<LinkedRectangle> LoadLinks()
        {
            Office.CustomXMLPart part = FindPartByNamespace(TalliarkXml.LinksNamespaceUri);
            if (part == null)
                return new List<LinkedRectangle>();

            string xml = part.XML;
            if (string.IsNullOrWhiteSpace(xml))
                return new List<LinkedRectangle>();

            try
            {
                return TalliarkLinksSerializer.FromXDocument(XDocument.Parse(xml));
            }
            catch (System.Xml.XmlException ex)
            {
                throw new InvalidOperationException("Talliark links custom XML part contains invalid XML.", ex);
            }
        }

        public void SaveLinks(IList<LinkedRectangle> linkedRectangles)
        {
            string xml = TalliarkLinksSerializer.ToXDocument(linkedRectangles)
                .ToString(SaveOptions.DisableFormatting);
            ReplacePart(TalliarkXml.LinksNamespaceUri, xml);
        }

        // ── Combined load/save (used by WorkbookStorageSession) ───────────────

        public TalliarkStorage Load()
        {
            TalliarkContent content = LoadContent();
            IList<LinkedRectangle> links = LoadLinks();
            return new TalliarkStorage(content.Version, content.Folders, content.Pdfs, links);
        }

        public void Save(TalliarkStorage storage)
        {
            if (storage == null) throw new ArgumentNullException(nameof(storage));
            SaveContent(new TalliarkContent(storage.Version, storage.Folders, storage.Pdfs));
            SaveLinks(storage.LinkedRectangles);
        }

        // ── LinkedRectangle helpers ───────────────────────────────────────────

        public bool TryGetLinkedRectangle(string id, out LinkedRectangle linkedRectangle)
        {
            if (string.IsNullOrWhiteSpace(id))
                throw new ArgumentException("LinkedRectangle id must be non-empty.", nameof(id));

            IList<LinkedRectangle> links = LoadLinks();
            linkedRectangle = links.FirstOrDefault(r => string.Equals(r.Id, id, StringComparison.Ordinal));
            return linkedRectangle != null;
        }

        public void UpsertLinkedRectangle(LinkedRectangle linkedRectangle)
        {
            if (linkedRectangle == null) throw new ArgumentNullException(nameof(linkedRectangle));
            if (string.IsNullOrWhiteSpace(linkedRectangle.Id))
                throw new ArgumentException("LinkedRectangle id must be non-empty.", nameof(linkedRectangle));

            List<LinkedRectangle> links = LoadLinks().ToList();
            int index = links.FindIndex(r => string.Equals(r.Id, linkedRectangle.Id, StringComparison.Ordinal));
            if (index >= 0) links[index] = linkedRectangle;
            else links.Add(linkedRectangle);

            SaveLinks(links);
        }

        public bool RemoveLinkedRectangle(string id)
        {
            if (string.IsNullOrWhiteSpace(id))
                throw new ArgumentException("LinkedRectangle id must be non-empty.", nameof(id));

            List<LinkedRectangle> links = LoadLinks().ToList();
            int before = links.Count;
            links = links.Where(r => !string.Equals(r.Id, id, StringComparison.Ordinal)).ToList();
            if (links.Count == before)
                return false;

            SaveLinks(links);
            return true;
        }

        // ── Store cleanup ─────────────────────────────────────────────────────

        public void DeleteStore()
        {
            // Delete all per-PDF binary parts first
            TalliarkContent content = LoadContent();
            foreach (PdfMetadata pdf in content.Pdfs)
                DeletePdfBinary(pdf.Id);

            DeletePart(TalliarkXml.ContentNamespaceUri);
            DeletePart(TalliarkXml.LinksNamespaceUri);
            DeleteReconcileWorkspace();
            DeletePart(TalliarkReconcileReviewSerializer.NamespaceUri);
        }

        // ── Debug snapshots ───────────────────────────────────────────────────

        /// <summary>
        /// Takes a read-only snapshot of every Custom XML part owned by Talliark.
        /// Namespace-prefix matching keeps the debug view useful when another
        /// storage part is introduced without teaching the inspector its schema.
        /// </summary>
        public IList<TalliarkXmlPartSnapshot> LoadXmlPartSnapshots()
        {
            var snapshots = new List<TalliarkXmlPartSnapshot>();
            Office.CustomXMLParts parts = null;

            try
            {
                parts = _workbook.CustomXMLParts;
                for (int index = 1; index <= parts.Count; index++)
                {
                    Office.CustomXMLPart part = null;
                    try
                    {
                        part = (Office.CustomXMLPart)parts[index];
                        string namespaceUri = part.NamespaceURI;
                        if (!TalliarkXml.IsStorageNamespace(namespaceUri))
                            continue;

                        snapshots.Add(new TalliarkXmlPartSnapshot(namespaceUri, part.XML));
                    }
                    finally
                    {
                        if (part != null && Marshal.IsComObject(part))
                            Marshal.ReleaseComObject(part);
                    }
                }
            }
            catch (COMException ex)
            {
                throw new InvalidOperationException(
                    "Talliark could not read the stored XML data in this workbook.", ex);
            }
            finally
            {
                if (parts != null && Marshal.IsComObject(parts))
                    Marshal.ReleaseComObject(parts);
            }

            snapshots.Sort((left, right) => string.CompareOrdinal(
                left.NamespaceUri, right.NamespaceUri));
            return snapshots;
        }

        // ── Private COM helpers ────────────────────────────────────────────────

        private Office.CustomXMLPart FindPartByNamespace(string namespaceUri)
        {
            try
            {
                Office.CustomXMLParts matches = _workbook.CustomXMLParts.SelectByNamespace(namespaceUri);
                if (matches != null && matches.Count > 0)
                    return (Office.CustomXMLPart)matches[1];
            }
            catch (COMException) { }

            return null;
        }

        /// <summary>
        /// Replaces the part carrying <paramref name="namespaceUri"/> with <paramref name="xml"/>.
        ///
        /// The new part is added *before* the old one is deleted, so a failure part-way through
        /// never leaves the workbook with no part at all. Deleting first — the obvious ordering —
        /// loses the entire links table or a whole PDF if the subsequent Add throws, which is a
        /// real risk once a part carries tens of megabytes of base64.
        ///
        /// If the old part cannot be deleted after the new one lands, the new part is removed
        /// again and the call throws: two parts sharing a namespace would make
        /// <see cref="FindPartByNamespace"/> non-deterministic, and silently reading whichever
        /// one it happened to return is worse than a visible failure.
        /// </summary>
        private void ReplacePart(string namespaceUri, string xml)
        {
            // Captured before the Add so we delete the part we meant to, rather than whichever
            // one SelectByNamespace returns once two briefly share the namespace.
            Office.CustomXMLPart existing = FindPartByNamespace(namespaceUri);

            object missing = Type.Missing;
            Office.CustomXMLPart added = _workbook.CustomXMLParts.Add(xml, missing);

            // Every persisted Talliark write makes any earlier rectangle creation cease to
            // be the latest Talliark action. Interactive creation and successful creation
            // undo explicitly re-arm after their full operation completes.
            NotifyPersistedMutation();

            if (existing == null)
                return;

            try
            {
                existing.Delete();
            }
            catch (COMException ex)
            {
                TalliarkLog.Trace(
                    $"ReplacePart could not delete superseded part ns={namespaceUri}: {ex.Message}");

                try { added?.Delete(); }
                catch (COMException rollbackEx)
                {
                    TalliarkLog.Trace(
                        $"ReplacePart rollback also failed ns={namespaceUri}: {rollbackEx.Message}");
                }

                throw new InvalidOperationException(
                    "Talliark could not replace its stored data in this workbook. " +
                    "The previous version has been kept.", ex);
            }
        }

        private void DeletePart(string namespaceUri)
        {
            Office.CustomXMLPart part = FindPartByNamespace(namespaceUri);
            if (part != null)
            {
                part.Delete();
                NotifyPersistedMutation();
            }
        }

        private void NotifyPersistedMutation()
        {
            try
            {
                Globals.ThisAddIn?.DisarmLinkCreationUndo(_workbook);
            }
            catch (Exception ex)
            {
                // Storage already changed, so notification failure cannot be rolled back.
                // The undo service still validates its top entry before touching workbook data.
                TalliarkLog.Trace($"NotifyPersistedMutation failed: {ex.Message}");
            }
        }
    }
}
