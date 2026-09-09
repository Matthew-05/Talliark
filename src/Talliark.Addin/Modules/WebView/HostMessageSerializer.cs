using System.Collections.Generic;
using System.Linq;
using System.Text;
using Talliark.Addin.Modules.CustomXml.Models;

namespace Talliark.Addin.Modules.WebView
{
    /// <summary>
    /// Serializes outbound host→viewer messages to JSON strings that conform to
    /// contracts/webview-messages-v1.json.
    /// </summary>
    internal static class HostMessageSerializer
    {
        /// <summary>
        /// Returns the JSON payload for a <c>pdfs-loaded</c> message. The folder list
        /// backs the viewer's folder filter; PDFs with no <c>FolderId</c> are uncategorised.
        /// </summary>
        public static string BuildPdfsLoaded(IList<PdfDocument> pdfs, IList<PdfFolder> folders)
        {
            var sb = new StringBuilder();
            sb.Append("{\"type\":\"pdfs-loaded\",\"folders\":");
            AppendFolders(sb, folders);
            sb.Append(",\"pdfs\":[");

            for (int i = 0; i < pdfs.Count; i++)
            {
                PdfDocument pdf = pdfs[i];
                if (i > 0) sb.Append(',');
                AppendPdfPayloadBody(sb, pdf);
            }

            sb.Append("]}");
            return sb.ToString();
        }

        /// <summary>
        /// Tells an existing viewer that its native host surface has become visible again.
        /// The web app uses this to repaint canvases whose WebView2 backing surface may have
        /// been discarded while hidden, without reloading the workbook catalogue.
        /// </summary>
        public static string BuildViewerSurfaceShown() =>
            "{\"type\":\"viewer-surface-shown\"}";

        /// <summary>
        /// Returns the JSON payload for a <c>viewer-folders-updated</c> message, carrying the
        /// full folder catalogue and every PDF's folder assignment without any PDF bytes.
        /// </summary>
        public static string BuildViewerFoldersUpdated(
            IList<PdfFolder> folders, IList<PdfMetadata> pdfs)
        {
            var sb = new StringBuilder();
            sb.Append("{\"type\":\"viewer-folders-updated\",\"folders\":");
            AppendFolders(sb, folders);
            sb.Append(",\"assignments\":[");

            if (pdfs != null)
            {
                for (int i = 0; i < pdfs.Count; i++)
                {
                    PdfMetadata pdf = pdfs[i];
                    if (i > 0) sb.Append(',');

                    sb.Append("{\"pdfId\":"); AppendString(sb, pdf.Id ?? string.Empty);
                    if (!string.IsNullOrWhiteSpace(pdf.FolderId))
                    {
                        sb.Append(",\"folderId\":"); AppendString(sb, pdf.FolderId);
                    }
                    sb.Append('}');
                }
            }

            sb.Append("]}");
            return sb.ToString();
        }

        /// <summary>Returns the JSON payload for a <c>pdf-updated</c> message.</summary>
        public static string BuildPdfUpdated(PdfDocument pdf)
        {
            var sb = new StringBuilder();
            sb.Append("{\"type\":\"pdf-updated\",\"pdf\":");
            AppendPdfPayloadBody(sb, pdf);
            sb.Append('}');
            return sb.ToString();
        }

        /// <summary>Returns the JSON payload for a <c>pdf-added</c> message.</summary>
        public static string BuildPdfAdded(PdfDocument pdf)
        {
            var sb = new StringBuilder();
            sb.Append("{\"type\":\"pdf-added\",\"pdf\":");
            AppendPdfPayloadBody(sb, pdf);
            sb.Append('}');
            return sb.ToString();
        }

        /// <summary>Returns the JSON payload for a <c>pdf-name-updated</c> message.</summary>
        public static string BuildPdfNameUpdated(string id, string name)
        {
            var sb = new StringBuilder();
            sb.Append("{\"type\":\"pdf-name-updated\"");
            sb.Append(",\"id\":"); AppendString(sb, id ?? string.Empty);
            sb.Append(",\"name\":"); AppendString(sb, name ?? string.Empty);
            sb.Append('}');
            return sb.ToString();
        }

        /// <summary>Returns the JSON payload for a <c>pdf-removed</c> message.</summary>
        public static string BuildPdfRemoved(string id)
        {
            var sb = new StringBuilder();
            sb.Append("{\"type\":\"pdf-removed\"");
            sb.Append(",\"id\":"); AppendString(sb, id ?? string.Empty);
            sb.Append('}');
            return sb.ToString();
        }

        /// <summary>
        /// Returns the JSON payload for a <c>linked-rectangles-loaded</c> message.
        /// </summary>
        public static string BuildLinkedRectanglesLoaded(IList<LinkedRectangle> rects)
        {
            var sb = new StringBuilder();
            sb.Append("{\"type\":\"linked-rectangles-loaded\",\"rectangles\":[");

            for (int i = 0; i < rects.Count; i++)
            {
                if (i > 0) sb.Append(',');
                AppendLinkedRectangle(sb, rects[i]);
            }

            sb.Append("]}");
            return sb.ToString();
        }

        /// <summary>Returns the JSON payload for a newly persisted linked rectangle.</summary>
        public static string BuildLinkedRectangleAdded(LinkedRectangle rect)
        {
            var sb = new StringBuilder();
            sb.Append("{\"type\":\"linked-rectangle-added\",\"rectangle\":");
            AppendLinkedRectangle(sb, rect);
            sb.Append('}');
            return sb.ToString();
        }

        private static void AppendLinkedRectangle(StringBuilder sb, LinkedRectangle rect)
        {
            sb.Append('{');
            sb.Append("\"id\":"); AppendString(sb, rect.Id);
            sb.Append(",\"pdfId\":"); AppendString(sb, rect.PdfId);
            sb.Append(",\"page\":"); sb.Append(rect.Rectangle.PageIndex);
            sb.Append(",\"rect\":{");
            sb.Append("\"x\":"); AppendDouble(sb, rect.Rectangle.X);
            sb.Append(",\"y\":"); AppendDouble(sb, rect.Rectangle.Y);
            sb.Append(",\"width\":"); AppendDouble(sb, rect.Rectangle.Width);
            sb.Append(",\"height\":"); AppendDouble(sb, rect.Rectangle.Height);
            sb.Append("}");
            sb.Append(",\"linkType\":"); AppendString(sb, SerializeLinkType(rect.LinkType));
            if (rect.LinkType == LinkType.Table)
            {
                sb.Append(",\"table\":");
                AppendTableGrid(sb, rect.TableGrid);
            }
            sb.Append('}');
        }

        /// <summary>Returns the JSON payload for a <c>clear-rectangle-highlight</c> message.</summary>
        public static string BuildClearRectangleHighlight() =>
            "{\"type\":\"clear-rectangle-highlight\"}";

        /// <summary>
        /// Returns the JSON payload for a <c>link-rectangles-removed</c> message.
        /// </summary>
        public static string BuildLinkRectanglesRemoved(IList<string> ids)
        {
            var sb = new StringBuilder();
            sb.Append("{\"type\":\"link-rectangles-removed\",\"ids\":[");

            for (int i = 0; i < ids.Count; i++)
            {
                if (i > 0) sb.Append(',');
                AppendString(sb, ids[i] ?? string.Empty);
            }

            sb.Append("]}");
            return sb.ToString();
        }

        /// <summary>Returns the JSON payload for a <c>reset-ui</c> message.</summary>
        public static string BuildResetUi() =>
            "{\"type\":\"reset-ui\"}";

        /// <summary>
        /// Returns the JSON payload for a <c>navigate-to-rectangle</c> message.
        /// </summary>
        public static string BuildNavigateToRectangle(string id, string pdfId, int page)
        {
            var sb = new StringBuilder();
            sb.Append("{\"type\":\"navigate-to-rectangle\"");
            sb.Append(",\"id\":"); AppendString(sb, id ?? string.Empty);
            sb.Append(",\"pdfId\":"); AppendString(sb, pdfId ?? string.Empty);
            sb.Append(",\"page\":"); sb.Append(page);
            sb.Append('}');
            return sb.ToString();
        }

        /// <summary>Returns the JSON payload for a <c>show-pdf</c> message.</summary>
        public static string BuildShowPdf(string pdfId)
        {
            var sb = new StringBuilder();
            sb.Append("{\"type\":\"show-pdf\"");
            sb.Append(",\"pdfId\":"); AppendString(sb, pdfId ?? string.Empty);
            sb.Append('}');
            return sb.ToString();
        }

        /// <summary>Returns the JSON payload for a <c>highlight-rectangle</c> message.</summary>
        public static string BuildHighlightRectangle(string id)
        {
            var sb = new StringBuilder();
            sb.Append("{\"type\":\"highlight-rectangle\"");
            sb.Append(",\"id\":"); AppendString(sb, id ?? string.Empty);
            sb.Append('}');
            return sb.ToString();
        }

        /// <summary>
        /// Returns the JSON payload for a <c>link-selection-changed</c> message.
        /// An empty list is valid and tells the viewer to hide the selection panel.
        /// </summary>
        public static string BuildLinkSelectionChanged(IList<LinkSelectionEntry> entries)
        {
            var sb = new StringBuilder();
            sb.Append("{\"type\":\"link-selection-changed\",\"entries\":[");

            if (entries != null)
            {
                for (int i = 0; i < entries.Count; i++)
                {
                    LinkSelectionEntry entry = entries[i];
                    if (i > 0) sb.Append(',');

                    sb.Append('{');
                    sb.Append("\"id\":"); AppendString(sb, entry.Id ?? string.Empty);
                    sb.Append(",\"pdfId\":"); AppendString(sb, entry.PdfId ?? string.Empty);
                    sb.Append(",\"pdfName\":"); AppendString(sb, entry.PdfName ?? string.Empty);
                    sb.Append(",\"page\":"); sb.Append(entry.Page);
                    sb.Append(",\"value\":"); AppendString(sb, entry.Value ?? string.Empty);
                    sb.Append(",\"valueCount\":"); sb.Append(entry.ValueCount);
                    sb.Append(",\"cellAddress\":"); AppendString(sb, entry.CellAddress ?? string.Empty);
                    sb.Append(",\"cellValue\":"); AppendString(sb, entry.CellValue ?? string.Empty);
                    sb.Append('}');
                }
            }

            sb.Append("]}");
            return sb.ToString();
        }

        /// <summary>Returns the JSON payload for a <c>set-search-query</c> message.</summary>
        public static string BuildSetSearchQuery(string query)
        {
            var sb = new StringBuilder();
            sb.Append("{\"type\":\"set-search-query\",\"query\":");
            AppendString(sb, query ?? string.Empty);
            sb.Append('}');
            return sb.ToString();
        }

        /// <summary>
        /// Returns the JSON payload for a <c>set-char-bboxes-visible</c> message, which
        /// shows or hides the viewer's per-character bounding-box debug overlay.
        /// </summary>
        public static string BuildSetCharBboxesVisible(bool visible)
        {
            return visible
                ? "{\"type\":\"set-char-bboxes-visible\",\"visible\":true}"
                : "{\"type\":\"set-char-bboxes-visible\",\"visible\":false}";
        }

        public static string BuildSetTableDetectionEnabled(bool enabled)
        {
            return enabled
                ? "{\"type\":\"set-table-detection-enabled\",\"enabled\":true}"
                : "{\"type\":\"set-table-detection-enabled\",\"enabled\":false}";
        }

        public static string BuildSetValuesVisible(bool visible)
        {
            return visible
                ? "{\"type\":\"set-values-visible\",\"visible\":true}"
                : "{\"type\":\"set-values-visible\",\"visible\":false}";
        }

        public static string BuildSetReferencesVisible(bool visible)
        {
            return visible
                ? "{\"type\":\"set-references-visible\",\"visible\":true}"
                : "{\"type\":\"set-references-visible\",\"visible\":false}";
        }

        public static string BuildSetStructureVisible(bool visible)
        {
            return visible
                ? "{\"type\":\"set-structure-visible\",\"visible\":true}"
                : "{\"type\":\"set-structure-visible\",\"visible\":false}";
        }

        public static string BuildSetValueNoiseVisible(bool visible)
        {
            return visible
                ? "{\"type\":\"set-value-noise-visible\",\"visible\":true}"
                : "{\"type\":\"set-value-noise-visible\",\"visible\":false}";
        }

        /// <summary>Returns the JSON payload for a <c>page-rotations-updated</c> message.</summary>
        public static string BuildPageRotationsUpdated(string pdfId, Dictionary<int, int> rotations)
        {
            var sb = new StringBuilder();
            sb.Append("{\"type\":\"page-rotations-updated\"");
            sb.Append(",\"pdfId\":"); AppendString(sb, pdfId ?? string.Empty);
            sb.Append(",\"rotations\":{");
            bool first = true;
            if (rotations != null)
            {
                foreach (var kvp in rotations.OrderBy(k => k.Key))
                {
                    if (!first) sb.Append(',');
                    first = false;
                    sb.Append('"'); sb.Append(kvp.Key); sb.Append("\":");
                    sb.Append(kvp.Value);
                }
            }
            sb.Append("}}");
            return sb.ToString();
        }

        /// <summary>Writes a single <c>PdfPayload</c> object, braces included.</summary>
        private static void AppendPdfPayloadBody(StringBuilder sb, PdfDocument pdf)
        {
            sb.Append('{');
            sb.Append("\"id\":"); AppendString(sb, pdf.Id);
            sb.Append(",\"name\":"); AppendString(sb, pdf.Name ?? string.Empty);
            sb.Append(",\"base64\":"); AppendString(sb, pdf.Base64 ?? string.Empty);
            if (!string.IsNullOrWhiteSpace(pdf.FolderId))
            {
                sb.Append(",\"folderId\":"); AppendString(sb, pdf.FolderId);
            }
            if (!string.IsNullOrWhiteSpace(pdf.GeometryBase64))
            {
                sb.Append(",\"geometryBase64\":"); AppendString(sb, pdf.GeometryBase64);
            }
            if (!string.IsNullOrWhiteSpace(pdf.TableStructureBase64))
            {
                sb.Append(",\"tableStructureBase64\":");
                AppendString(sb, pdf.TableStructureBase64);
            }
            if (!string.IsNullOrWhiteSpace(pdf.DocumentValuesBase64))
            {
                sb.Append(",\"documentValuesBase64\":");
                AppendString(sb, pdf.DocumentValuesBase64);
            }
            if (!string.IsNullOrWhiteSpace(pdf.FinancialStructureBase64))
            {
                sb.Append(",\"financialStructureBase64\":");
                AppendString(sb, pdf.FinancialStructureBase64);
            }
            AppendPageRotations(sb, pdf.PageRotations);
            sb.Append('}');
        }

        /// <summary>Writes a <c>FolderPayload</c> array, brackets included.</summary>
        private static void AppendFolders(StringBuilder sb, IList<PdfFolder> folders)
        {
            sb.Append('[');
            if (folders != null)
            {
                for (int i = 0; i < folders.Count; i++)
                {
                    PdfFolder folder = folders[i];
                    if (i > 0) sb.Append(',');
                    sb.Append("{\"id\":"); AppendString(sb, folder.Id ?? string.Empty);
                    sb.Append(",\"name\":"); AppendString(sb, folder.Name ?? string.Empty);
                    sb.Append('}');
                }
            }
            sb.Append(']');
        }

        private static void AppendPageRotations(StringBuilder sb, Dictionary<int, int> pageRotations)
        {
            if (pageRotations == null || pageRotations.Count == 0) return;
            var nonZero = pageRotations.Where(kvp => kvp.Value != 0).ToList();
            if (nonZero.Count == 0) return;
            sb.Append(",\"pageRotations\":{");
            for (int i = 0; i < nonZero.Count; i++)
            {
                if (i > 0) sb.Append(',');
                sb.Append('"'); sb.Append(nonZero[i].Key); sb.Append("\":");
                sb.Append(nonZero[i].Value);
            }
            sb.Append('}');
        }

        private static string SerializeLinkType(LinkType linkType)
        {
            switch (linkType)
            {
                case LinkType.Raw: return "raw";
                case LinkType.Sum: return "sum";
                case LinkType.Table: return "table";
                default:           return "auto";
            }
        }

        private static void AppendTableGrid(StringBuilder sb, TableGrid tableGrid)
        {
            tableGrid = tableGrid ?? new TableGrid();
            sb.Append("{\"columnBoundaries\":[");
            AppendBoundaries(sb, tableGrid.ColumnBoundaries);
            sb.Append("],\"rowBoundaries\":[");
            AppendBoundaries(sb, tableGrid.RowBoundaries);
            sb.Append("]}");
        }

        private static void AppendBoundaries(StringBuilder sb, IList<double> boundaries)
        {
            if (boundaries == null) return;
            bool first = true;
            foreach (double position in boundaries
                .Where(value => !double.IsNaN(value)
                    && !double.IsInfinity(value)
                    && value > 0
                    && value < 1)
                .Distinct()
                .OrderBy(value => value))
            {
                if (!first) sb.Append(',');
                AppendDouble(sb, position);
                first = false;
            }
        }

        private static void AppendDouble(StringBuilder sb, double value)
        {
            sb.Append(value.ToString("G", System.Globalization.CultureInfo.InvariantCulture));
        }

        private static void AppendString(StringBuilder sb, string value)
        {
            sb.Append('"');
            foreach (char c in value)
            {
                switch (c)
                {
                    case '"':  sb.Append("\\\""); break;
                    case '\\': sb.Append("\\\\"); break;
                    case '\n': sb.Append("\\n");  break;
                    case '\r': sb.Append("\\r");  break;
                    case '\t': sb.Append("\\t");  break;
                    default:
                        if (c < 0x20)
                            sb.Append($"\\u{(int)c:x4}");
                        else
                            sb.Append(c);
                        break;
                }
            }
            sb.Append('"');
        }
    }

    /// <summary>
    /// One linked rectangle inside the current Excel selection, as sent to the viewer in a
    /// <c>link-selection-changed</c> message. A Sum cell contributes one entry per
    /// contributing rectangle; those entries share <see cref="CellAddress"/> and
    /// <see cref="CellValue"/>.
    /// </summary>
    public sealed class LinkSelectionEntry
    {
        public LinkSelectionEntry(
            string id,
            string pdfId,
            string pdfName,
            int page,
            string value,
            int valueCount,
            string cellAddress,
            string cellValue)
        {
            Id = id;
            PdfId = pdfId;
            PdfName = pdfName;
            Page = page;
            Value = value;
            ValueCount = valueCount;
            CellAddress = cellAddress;
            CellValue = cellValue;
        }

        public string Id { get; }

        public string PdfId { get; }

        public string PdfName { get; }

        /// <summary>0-based page index of the rectangle.</summary>
        public int Page { get; }

        /// <summary>
        /// Value attributable to this rectangle: its captured source text for Sum links,
        /// otherwise the displayed text of the linked cell.
        /// </summary>
        public string Value { get; }

        /// <summary>
        /// How many numbers this rectangle contributes to its Sum cell. Greater than 1 when a
        /// single rectangle captured several numbers, which the viewer shows as a count.
        /// </summary>
        public int ValueCount { get; }

        /// <summary>Sheet-qualified address of the linked cell, e.g. "Sheet1!B4".</summary>
        public string CellAddress { get; }

        /// <summary>Displayed text of the linked cell — the computed total for a Sum cell.</summary>
        public string CellValue { get; }
    }
}
