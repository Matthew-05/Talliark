using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using Talliark.Addin.Modules.CustomXml.Models;
using Talliark.Addin.Modules.Services;

namespace Talliark.Addin.Modules.WebView
{
    /// <summary>
    /// Serializes outbound host→file-manager messages to JSON strings that conform to
    /// contracts/webview-messages-v1.json (FilesLoadedMessage).
    /// Base64 bytes are never included; only metadata is sent.
    /// </summary>
    internal static class FileManagerMessageSerializer
    {
        public static string BuildFilesLoaded(
            IList<PdfFolder> folders,
            IList<PdfMetadata> pdfs,
            IReadOnlyDictionary<string, int> linkCounts = null)
        {
            var sb = new StringBuilder();
            sb.Append("{\"type\":\"files-loaded\",\"folders\":[");

            for (int i = 0; i < folders.Count; i++)
            {
                if (i > 0) sb.Append(',');
                PdfFolder f = folders[i];
                sb.Append('{');
                sb.Append("\"id\":"); AppendString(sb, f.Id);
                sb.Append(",\"name\":"); AppendString(sb, f.Name ?? string.Empty);
                sb.Append('}');
            }

            sb.Append("],\"files\":[");

            for (int i = 0; i < pdfs.Count; i++)
            {
                if (i > 0) sb.Append(',');
                PdfMetadata p = pdfs[i];

                string dateAdded = p.DateAdded.HasValue
                    ? p.DateAdded.Value.ToUniversalTime().ToString("O", CultureInfo.InvariantCulture)
                    : string.Empty;

                sb.Append('{');
                sb.Append("\"id\":"); AppendString(sb, p.Id);
                sb.Append(",\"name\":"); AppendString(sb, p.Name ?? string.Empty);
                if (!string.IsNullOrEmpty(p.FolderId))
                {
                    sb.Append(",\"folderId\":"); AppendString(sb, p.FolderId);
                }
                sb.Append(",\"status\":"); AppendString(sb, !string.IsNullOrWhiteSpace(p.OcrStatus) ? p.OcrStatus : PdfStatus.None);
                sb.Append(",\"fileSizeBytes\":"); sb.Append(p.FileSizeBytes.ToString(CultureInfo.InvariantCulture));
                sb.Append(",\"dateAdded\":"); AppendString(sb, dateAdded);
                int lc = linkCounts != null && linkCounts.TryGetValue(p.Id, out int cnt) ? cnt : 0;
                sb.Append(",\"linkCount\":"); sb.Append(lc.ToString(CultureInfo.InvariantCulture));
                sb.Append('}');
            }

            sb.Append("]}");
            return sb.ToString();
        }

        /// <summary>
        /// Builds a host→web <c>ocr-status</c> message for a single PDF.
        /// detail is optional — pass null to omit it. A plain string converts to a
        /// message-only detail, which is what terminal updates carry.
        ///
        /// Every field is written from what the caller supplies. Nothing here reads
        /// the message: the stage and the counts arrive as their own fields because
        /// the worker and the host state them, and parsing them back out of prose
        /// is exactly the bug this shape removed.
        /// </summary>
        public static string BuildOcrStatus(string pdfId, string status, OcrStatusDetail detail = null)
        {
            var sb = new StringBuilder();
            sb.Append("{\"type\":\"ocr-status\",\"pdfId\":");
            AppendString(sb, pdfId ?? string.Empty);
            sb.Append(",\"status\":");
            AppendString(sb, status ?? string.Empty);
            if (detail != null && !string.IsNullOrEmpty(detail.Message))
            {
                sb.Append(",\"message\":");
                AppendString(sb, detail.Message);

                if (!string.IsNullOrEmpty(detail.Stage))
                {
                    sb.Append(",\"stage\":");
                    AppendString(sb, detail.Stage);

                    if (detail.Current.HasValue && detail.Total.HasValue && detail.Total.Value > 0)
                    {
                        sb.Append(",\"current\":");
                        sb.Append(Math.Min(Math.Max(detail.Current.Value, 0), detail.Total.Value)
                            .ToString(CultureInfo.InvariantCulture));
                        sb.Append(",\"total\":");
                        sb.Append(detail.Total.Value.ToString(CultureInfo.InvariantCulture));
                        if (!string.IsNullOrEmpty(detail.Unit))
                        {
                            sb.Append(",\"unit\":");
                            AppendString(sb, detail.Unit);
                        }
                    }
                }

                if (detail.FileIndex.HasValue && detail.FileCount.HasValue && detail.FileCount.Value > 0)
                {
                    sb.Append(",\"fileIndex\":");
                    sb.Append(Math.Min(Math.Max(detail.FileIndex.Value, 1), detail.FileCount.Value)
                        .ToString(CultureInfo.InvariantCulture));
                    sb.Append(",\"fileCount\":");
                    sb.Append(detail.FileCount.Value.ToString(CultureInfo.InvariantCulture));
                }
            }
            sb.Append('}');
            return sb.ToString();
        }

        /// <summary>
        /// Builds a host→web <c>folder-selected</c> message. <paramref name="folderId"/> is
        /// null for All Files, and the field is then omitted rather than sent as JSON null,
        /// matching how <c>move-file</c> states "uncategorised".
        /// </summary>
        public static string BuildFolderSelected(string folderId)
        {
            var sb = new StringBuilder();
            sb.Append("{\"type\":\"folder-selected\"");
            if (!string.IsNullOrEmpty(folderId))
            {
                sb.Append(",\"folderId\":");
                AppendString(sb, folderId);
            }
            sb.Append('}');
            return sb.ToString();
        }

        /// <summary>
        /// Builds the host→web <c>row-drag-ended</c> message. It carries no outcome because
        /// the web UI has no use for one: it only needs to know its drag highlight is stale
        /// now, and the file list that says where the files actually went arrives separately.
        /// </summary>
        public static string BuildRowDragEnded()
        {
            return "{\"type\":\"row-drag-ended\"}";
        }

        /// <summary>
        /// Builds the host→web command that dismisses transient menus after a click on the
        /// native sidebar, which sits outside the WebView DOM and cannot raise its document
        /// click handler.
        /// </summary>
        public static string BuildDismissContextMenu()
        {
            return "{\"type\":\"dismiss-context-menu\"}";
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
}
