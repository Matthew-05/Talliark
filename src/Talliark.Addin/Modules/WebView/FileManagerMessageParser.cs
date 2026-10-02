using System;
using System.Collections.Generic;

namespace Talliark.Addin.Modules.WebView
{
    /// <summary>
    /// Parses inbound file-manager→host JSON messages that conform to
    /// contracts/webview-messages-v1.json.
    /// </summary>
    internal static class FileManagerMessageParser
    {
        /// <inheritdoc cref="WebMessageParser.GetMessageType"/>
        public static string GetMessageType(string json) =>
            WebMessageParser.GetMessageType(json);

        public static RenameFileRequest ParseRenameFile(string json)
        {
            var dict = Deserialize(json);
            return new RenameFileRequest
            {
                Id      = GetString(dict, "id"),
                NewName = GetString(dict, "newName"),
            };
        }

        public static RemoveFileRequest ParseRemoveFile(string json)
        {
            var dict = Deserialize(json);
            return new RemoveFileRequest { Id = GetString(dict, "id") };
        }

        public static SelectFileRequest ParseSelectFile(string json)
        {
            var dict = Deserialize(json);
            return new SelectFileRequest { Id = GetString(dict, "id") };
        }

        public static MoveFileRequest ParseMoveFile(string json)
        {
            var dict = Deserialize(json);
            return new MoveFileRequest
            {
                Id       = GetString(dict, "id"),
                FolderId = GetStringOrNull(dict, "folderId"),
            };
        }

        public static OcrPdfsRequest ParseOcrPdfs(string json)
        {
            var dict = Deserialize(json);
            var idsRaw = dict["pdfIds"] as System.Collections.ArrayList;
            if (idsRaw == null)
                throw new FormatException("ocr-pdfs message missing 'pdfIds' array.");

            var ids = new List<string>();
            foreach (var item in idsRaw)
            {
                if (item is string s && !string.IsNullOrWhiteSpace(s))
                    ids.Add(s);
            }
            return new OcrPdfsRequest { PdfIds = ids };
        }

        private static Dictionary<string, object> Deserialize(string json)
        {
            if (string.IsNullOrWhiteSpace(json))
                throw new ArgumentException("JSON must be non-empty.", nameof(json));

            var dict = WebMessageParser.Serializer.Deserialize<Dictionary<string, object>>(json);
            if (dict == null)
                throw new FormatException("Could not parse JSON object.");
            return dict;
        }

        private static string GetString(Dictionary<string, object> dict, string key)
        {
            if (dict.TryGetValue(key, out object val) && val is string s)
                return s;
            throw new FormatException($"Missing or non-string field '{key}'.");
        }

        private static string GetStringOrNull(Dictionary<string, object> dict, string key)
        {
            if (dict.TryGetValue(key, out object val) && val is string s && !string.IsNullOrEmpty(s))
                return s;
            return null;
        }
    }

    // ── Request DTOs ──────────────────────────────────────────────────────────

    internal sealed class RenameFileRequest
    {
        public string Id      { get; set; }
        public string NewName { get; set; }
    }

    internal sealed class RemoveFileRequest
    {
        public string Id { get; set; }
    }

    internal sealed class SelectFileRequest
    {
        public string Id { get; set; }
    }

    internal sealed class MoveFileRequest
    {
        public string Id       { get; set; }
        public string FolderId { get; set; }
    }

    internal sealed class OcrPdfsRequest
    {
        public List<string> PdfIds { get; set; }
    }
}
