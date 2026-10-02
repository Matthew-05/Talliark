using System.Collections.Generic;
using System.Web.Script.Serialization;

namespace Talliark.Addin.Modules.WebView
{
    /// <summary>
    /// Shared JSON parsing helpers used by all WebView2 message parsers.
    /// </summary>
    internal static class WebMessageParser
    {
        /// <summary>
        /// Serializer for inbound WebView messages. Uses <see cref="int.MaxValue"/> for
        /// <see cref="JavaScriptSerializer.MaxJsonLength"/> because the default (2097152) rejects
        /// <c>ocr-status</c> and viewer messages carrying gzipped base64 OCR payloads, which
        /// run well past it.
        /// </summary>
        internal static readonly JavaScriptSerializer Serializer = CreateSerializer();

        private static JavaScriptSerializer CreateSerializer()
        {
            var j = new JavaScriptSerializer();
            j.MaxJsonLength = int.MaxValue;
            return j;
        }

        /// <summary>
        /// Returns the value of the <c>type</c> property in the JSON object, or
        /// <c>null</c> if the string is not a valid JSON object or has no <c>type</c>.
        /// </summary>
        public static string GetMessageType(string json)
        {
            if (string.IsNullOrWhiteSpace(json))
                return null;

            try
            {
                var obj = Serializer.Deserialize<Dictionary<string, object>>(json);
                if (obj != null && obj.TryGetValue("type", out object typeValue))
                    return typeValue as string;
            }
            catch
            {
                // Malformed JSON — not a recognised message.
            }

            return null;
        }
    }
}
