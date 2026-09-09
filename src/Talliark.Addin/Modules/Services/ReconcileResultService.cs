using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Web.Script.Serialization;
using Talliark.Addin.Modules.CustomXml;
using Talliark.Addin.Modules.CustomXml.Models;
using Excel = Microsoft.Office.Interop.Excel;

namespace Talliark.Addin.Modules.Services
{
    /// <summary>
    /// Reads stored Reconcile envelopes and decides current/stale/none against the
    /// artifacts in the same workbook-scoped snapshot. A mismatch is reported, never rebuilt.
    /// </summary>
    internal sealed class ReconcileResultService
    {
        public IList<ReconcileDocumentInfo> LoadDocuments(Excel.Workbook workbook)
        {
            var store = new TalliarkCustomXmlPartStore(workbook);
            ReconcileWorkspace workspace = store.LoadReconcileWorkspace();
            return workspace.Primary == null
                ? new List<ReconcileDocumentInfo>()
                : new List<ReconcileDocumentInfo> { ReadDocument(workspace.Primary) };
        }

        public ReconcileStoredResult LoadResult(Excel.Workbook workbook, string pdfId)
        {
            var store = new TalliarkCustomXmlPartStore(workbook);
            ReconcileDocument document = store.LoadReconcileWorkspace().Documents.FirstOrDefault(d =>
                string.Equals(d.Id, pdfId, StringComparison.Ordinal));
            if (document?.Version == null)
                return new ReconcileStoredResult { PdfId = pdfId, Staleness = "none" };
            ReconcileDocumentInfo info = ReadDocument(document);
            return new ReconcileStoredResult
            {
                PdfId = pdfId,
                PdfBase64 = document.Version.Base64,
                PageRotations = document.Version.PageRotations,
                ReconcileBase64 = document.Version.ReconcileBase64,
                Staleness = info.Staleness,
            };
        }

        private static ReconcileDocumentInfo ReadDocument(ReconcileDocument document)
        {
            ReconcileDocumentVersion parts = document.Version;
            var info = new ReconcileDocumentInfo
            {
                Id = document.Id,
                VersionId = parts.Id,
                Name = document.DisplayName ?? string.Empty,
                Staleness = "none",
            };

            Dictionary<string, object> geometry = DecodeObject(parts.GeometryBase64);
            info.PageCount = CountArray(geometry, "pages");

            Dictionary<string, object> model = DecodeObject(parts.ReconcileBase64);
            if (model == null) return info;

            Dictionary<string, object> source = GetObject(model, "source");
            Dictionary<string, object> summary = GetObject(model, "summary");
            info.ScannedAt = GetString(source, "scannedAt");
            info.Summary = summary == null ? null : new ReconcileSummaryInfo
            {
                TablesExamined = GetInt(summary, "tablesExamined"),
                TotalsNominated = GetInt(summary, "totalsNominated"),
                Confirmed = GetInt(summary, "confirmed"),
                Breaks = GetInt(summary, "breaks"),
                Unresolved = GetInt(summary, "unresolved"),
            };

            bool current = source != null
                && string.Equals(GetString(source, "documentId"), document.Id, StringComparison.Ordinal)
                && string.Equals(GetString(source, "versionId"), parts.Id, StringComparison.Ordinal)
                && string.Equals(GetString(source, "geometryFingerprint"),
                    GeometryFingerprint(parts.GeometryBase64), StringComparison.Ordinal)
                && VersionMatches(source, "tableDetectorVersion", parts.TableStructureBase64)
                && VersionMatches(source, "valueDetectorVersion", parts.DocumentValuesBase64)
                && OptionalVersionMatches(
                    source, "financialStructureDetectorVersion", parts.FinancialStructureBase64);
            info.Staleness = current ? "current" : "stale";
            return info;
        }

        private static bool VersionMatches(
            Dictionary<string, object> source, string sourceKey, string artifactBase64)
        {
            Dictionary<string, object> artifact = DecodeObject(artifactBase64);
            return artifact != null && string.Equals(
                GetString(source, sourceKey), GetString(artifact, "detectorVersion"),
                StringComparison.Ordinal);
        }

        private static bool OptionalVersionMatches(
            Dictionary<string, object> source, string sourceKey, string artifactBase64)
        {
            string expected = GetString(source, sourceKey);
            if (string.IsNullOrEmpty(expected))
                return string.IsNullOrEmpty(artifactBase64);
            return VersionMatches(source, sourceKey, artifactBase64);
        }

        private static string GeometryFingerprint(string geometryBase64)
        {
            string json = DecodeJson(geometryBase64);
            if (json == null) return string.Empty;

            string canonical = JsonCanonicalizer.Canonicalize(json);
            using (SHA256 sha = SHA256.Create())
            {
                byte[] digest = sha.ComputeHash(Encoding.UTF8.GetBytes(canonical));
                var value = new StringBuilder(16);
                for (int i = 0; i < 8; i++) value.Append(digest[i].ToString("x2"));
                return value.ToString();
            }
        }

        private static Dictionary<string, object> DecodeObject(string base64)
        {
            string json = DecodeJson(base64);
            if (json == null) return null;
            try
            {
                var serializer = new JavaScriptSerializer { MaxJsonLength = int.MaxValue };
                return serializer.Deserialize<Dictionary<string, object>>(json);
            }
            catch { return null; }
        }

        private static string DecodeJson(string base64)
        {
            if (string.IsNullOrEmpty(base64)) return null;
            try
            {
                byte[] compressed = Convert.FromBase64String(base64);
                using (var input = new MemoryStream(compressed))
                using (var gzip = new GZipStream(input, CompressionMode.Decompress))
                using (var reader = new StreamReader(gzip, Encoding.UTF8))
                    return reader.ReadToEnd();
            }
            catch { return null; }
        }

        private static Dictionary<string, object> GetObject(
            Dictionary<string, object> value, string key)
        {
            return value != null && value.TryGetValue(key, out object item)
                ? item as Dictionary<string, object> : null;
        }

        private static string GetString(Dictionary<string, object> value, string key)
        {
            return value != null && value.TryGetValue(key, out object item)
                ? item?.ToString() ?? string.Empty : string.Empty;
        }

        private static int GetInt(Dictionary<string, object> value, string key)
        {
            return value != null && value.TryGetValue(key, out object item)
                && int.TryParse(item?.ToString(), NumberStyles.Integer,
                    CultureInfo.InvariantCulture, out int parsed) ? parsed : 0;
        }

        private static int? CountArray(Dictionary<string, object> value, string key)
        {
            if (value == null || !value.TryGetValue(key, out object item)) return null;
            return item is ICollection collection ? (int?)collection.Count : null;
        }

        /// <summary>
        /// Reorders object members while preserving every scalar token exactly. This
        /// matches Python json.dumps(sort_keys=True,separators=(",",":")) without
        /// converting geometry doubles through a different runtime's formatter.
        /// </summary>
        private sealed class JsonCanonicalizer
        {
            private readonly string _json;
            private int _position;

            private JsonCanonicalizer(string json) { _json = json; }

            public static string Canonicalize(string json)
            {
                var parser = new JsonCanonicalizer(json ?? string.Empty);
                string value = parser.ReadValue();
                parser.SkipWhitespace();
                if (parser._position != parser._json.Length)
                    throw new FormatException("Unexpected data after JSON value.");
                return value;
            }

            private string ReadValue()
            {
                SkipWhitespace();
                if (_position >= _json.Length) throw new FormatException("Unexpected end of JSON.");
                char c = _json[_position];
                if (c == '{') return ReadObject();
                if (c == '[') return ReadArray();
                if (c == '"') return ReadStringToken();
                return ReadScalar();
            }

            private string ReadObject()
            {
                _position++;
                var members = new List<KeyValuePair<string, string>>();
                SkipWhitespace();
                if (Take('}')) return "{}";
                while (true)
                {
                    SkipWhitespace();
                    string keyToken = ReadStringToken();
                    SkipWhitespace();
                    Require(':');
                    members.Add(new KeyValuePair<string, string>(keyToken, ReadValue()));
                    SkipWhitespace();
                    if (Take('}')) break;
                    Require(',');
                }
                // text-geometry-v1 property names are closed ASCII identifiers, so
                // their JSON string tokens have the same order as Python's decoded keys.
                members.Sort((a, b) => StringComparer.Ordinal.Compare(a.Key, b.Key));
                return "{" + string.Join(",", members.Select(m => m.Key + ":" + m.Value)) + "}";
            }

            private string ReadArray()
            {
                _position++;
                var items = new List<string>();
                SkipWhitespace();
                if (Take(']')) return "[]";
                while (true)
                {
                    items.Add(ReadValue());
                    SkipWhitespace();
                    if (Take(']')) break;
                    Require(',');
                }
                return "[" + string.Join(",", items) + "]";
            }

            private string ReadStringToken()
            {
                int start = _position++;
                bool escaped = false;
                while (_position < _json.Length)
                {
                    char c = _json[_position++];
                    if (c == '"' && !escaped)
                        return _json.Substring(start, _position - start);
                    if (c == '\\' && !escaped) escaped = true;
                    else escaped = false;
                }
                throw new FormatException("Unterminated JSON string.");
            }

            private string ReadScalar()
            {
                int start = _position;
                while (_position < _json.Length
                    && ",]} \t\r\n".IndexOf(_json[_position]) < 0) _position++;
                if (_position == start) throw new FormatException("Invalid JSON value.");
                return _json.Substring(start, _position - start);
            }

            private void SkipWhitespace()
            {
                while (_position < _json.Length && char.IsWhiteSpace(_json[_position])) _position++;
            }

            private bool Take(char expected)
            {
                if (_position < _json.Length && _json[_position] == expected)
                {
                    _position++;
                    return true;
                }
                return false;
            }

            private void Require(char expected)
            {
                if (!Take(expected)) throw new FormatException("Expected '" + expected + "'.");
            }
        }
    }

    internal sealed class ReconcileDocumentInfo
    {
        public string Id { get; set; }
        public string Name { get; set; }
        public string VersionId { get; set; }
        public string FolderId { get; set; }
        public int? PageCount { get; set; }
        public string Staleness { get; set; }
        public string ScannedAt { get; set; }
        public ReconcileSummaryInfo Summary { get; set; }
    }

    internal sealed class ReconcileSummaryInfo
    {
        public int TablesExamined { get; set; }
        public int TotalsNominated { get; set; }
        public int Confirmed { get; set; }
        public int Breaks { get; set; }
        public int Unresolved { get; set; }
    }

    internal sealed class ReconcileStoredResult
    {
        public string PdfId { get; set; }
        public string PdfBase64 { get; set; }
        public Dictionary<int, int> PageRotations { get; set; }
        public string ReconcileBase64 { get; set; }
        public string Staleness { get; set; }
    }
}
