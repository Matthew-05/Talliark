using System;
using System.Collections.Generic;

namespace Talliark.Addin.Modules.WebView
{
    internal static class ReconcileMessageParser
    {
        public static string GetMessageType(string json) => WebMessageParser.GetMessageType(json);

        public static string ParsePdfId(string json)
        {
            var value = WebMessageParser.Serializer.Deserialize<Dictionary<string, object>>(json);
            if (value != null && value.TryGetValue("pdfId", out object item)
                && item is string id && !string.IsNullOrWhiteSpace(id))
                return id;
            throw new FormatException("Reconcile message missing 'pdfId'.");
        }

        public static string ParseSourcePdfId(string json)
        {
            var value = WebMessageParser.Serializer.Deserialize<Dictionary<string, object>>(json);
            if (value != null && value.TryGetValue("sourcePdfId", out object item)
                && item is string id && !string.IsNullOrWhiteSpace(id)) return id;
            throw new FormatException("Reconcile message missing 'sourcePdfId'.");
        }
    }
}
