using System;
using System.IO;
using System.IO.Compression;
using System.Text;
using System.Text.RegularExpressions;

namespace Talliark.Addin.Modules.Services
{
    /// <summary>
    /// Lightweight C# heuristic to distinguish PDFs with an embedded text layer from scanned image-only PDFs.
    /// Used when a PDF is first added to assign status "text" or "none". Does not invoke the Python worker.
    /// </summary>
    internal static class PdfTextLayerDetector
    {
        private const int ScanWindowBytes = 2 * 1024 * 1024;
        private const int StreamDictionaryLookbackBytes = 512;
        private const int FlateInputCapBytes = 8 * 1024 * 1024;
        private const int FlateOutputCapBytes = 2 * 1024 * 1024;
        private static readonly Encoding Latin1 = Encoding.GetEncoding("ISO-8859-1");
        private static readonly byte[] StreamKeyword =
        {
            (byte)'s', (byte)'t', (byte)'r', (byte)'e', (byte)'a', (byte)'m'
        };

        /// <summary>
        /// A font selection operator, e.g. "/F1 12 Tf". Structured enough that decompressed
        /// image data cannot match it, unlike the bare "BT"/"Tj" tokens it accompanies.
        /// </summary>
        private static readonly Regex FontSelectionOperator = new Regex(
            @"/[A-Za-z0-9._#+-]+\s+[-+0-9.]+\s+Tf\b",
            RegexOptions.Compiled);

        /// <summary>Returns "text" when the PDF likely has selectable text, otherwise "none".</summary>
        public static string ClassifyFromBase64(string base64)
        {
            if (string.IsNullOrWhiteSpace(base64))
                return "none";

            try
            {
                byte[] bytes = Convert.FromBase64String(base64);
                return ClassifyFromBytes(bytes);
            }
            catch
            {
                return "none";
            }
        }

        public static string ClassifyFromBytes(byte[] pdf)
        {
            if (pdf == null || pdf.Length == 0)
                return "none";

            try
            {
                return ContainsExtractableText(pdf) ? "text" : "none";
            }
            catch
            {
                return "none";
            }
        }

        private static bool ContainsExtractableText(byte[] pdf)
        {
            if (HasTextLayerMarkers(GetScanContent(pdf)))
                return true;

            return HasTextLayerMarkersInFlateStreams(pdf);
        }

        /// <summary>
        /// Reads the first and last scan windows. Many PDFs store font dictionaries and the xref table
        /// near the end of the file, so head-only scanning misses WinAnsi/TrueType text layers.
        /// </summary>
        private static string GetScanContent(byte[] pdf)
        {
            if (pdf.Length <= ScanWindowBytes)
                return Latin1.GetString(pdf);

            string head = Latin1.GetString(pdf, 0, ScanWindowBytes);

            if (pdf.Length <= ScanWindowBytes * 2)
                return Latin1.GetString(pdf);

            string tail = Latin1.GetString(pdf, pdf.Length - ScanWindowBytes, ScanWindowBytes);
            return head + tail;
        }

        private static bool HasTextLayerMarkers(string content)
        {
            if (HasToUnicodeFont(content))
                return true;

            if (HasFontWithStandardEncoding(content))
                return true;

            if (HasUncompressedTextOperators(content))
                return true;

            return false;
        }

        private static bool HasToUnicodeFont(string content)
        {
            return content.IndexOf("/ToUnicode", StringComparison.Ordinal) >= 0
                && content.IndexOf("/Font", StringComparison.Ordinal) >= 0;
        }

        /// <summary>
        /// Native text PDFs often use standard font encodings without a ToUnicode CMap
        /// (e.g. TrueType + WinAnsiEncoding).
        /// </summary>
        private static bool HasFontWithStandardEncoding(string content)
        {
            if (content.IndexOf("/Font", StringComparison.Ordinal) < 0)
                return false;

            if (content.IndexOf("/WinAnsiEncoding", StringComparison.Ordinal) >= 0
                || content.IndexOf("/MacRomanEncoding", StringComparison.Ordinal) >= 0
                || content.IndexOf("/PDFDocEncoding", StringComparison.Ordinal) >= 0)
            {
                return true;
            }

            return content.IndexOf("/BaseFont", StringComparison.Ordinal) >= 0
                && (content.IndexOf("/TrueType", StringComparison.Ordinal) >= 0
                    || content.IndexOf("/Type1", StringComparison.Ordinal) >= 0);
        }

        /// <summary>
        /// Fallback for PDFs with uncompressed content streams.
        /// </summary>
        private static bool HasUncompressedTextOperators(string content)
        {
            if (content.IndexOf(" BT", StringComparison.Ordinal) < 0
                && content.IndexOf("\nBT", StringComparison.Ordinal) < 0)
            {
                return false;
            }

            return content.IndexOf(" Tj", StringComparison.Ordinal) >= 0
                || content.IndexOf(" TJ", StringComparison.Ordinal) >= 0
                || content.IndexOf("\nTj", StringComparison.Ordinal) >= 0
                || content.IndexOf("\nTJ", StringComparison.Ordinal) >= 0;
        }

        /// <summary>
        /// Scans FlateDecode streams, which the raw byte scan cannot see into. Modern
        /// producers — including Talliark's own export, which writes object streams —
        /// compress font dictionaries and content streams, so a fully searchable PDF
        /// can carry no plain-text markers at all and would otherwise read as scanned.
        /// </summary>
        private static bool HasTextLayerMarkersInFlateStreams(byte[] pdf)
        {
            int searchFrom = 0;
            while (searchFrom < pdf.Length)
            {
                int keywordIndex = IndexOf(pdf, StreamKeyword, searchFrom);
                if (keywordIndex < 0)
                    return false;
                searchFrom = keywordIndex + StreamKeyword.Length;

                // The keyword inside "endstream" closes a stream; only the leading one opens data.
                if (keywordIndex >= 3
                    && pdf[keywordIndex - 1] == (byte)'d'
                    && pdf[keywordIndex - 2] == (byte)'n'
                    && pdf[keywordIndex - 3] == (byte)'e')
                {
                    continue;
                }

                int lookbackStart = Math.Max(0, keywordIndex - StreamDictionaryLookbackBytes);
                string dictionary = Latin1.GetString(pdf, lookbackStart, keywordIndex - lookbackStart);
                if (dictionary.IndexOf("FlateDecode", StringComparison.Ordinal) < 0)
                    continue;

                // Image XObjects are never content or object streams, and their decompressed
                // bitmaps would only offer noise to the marker scan.
                if (dictionary.IndexOf("/Image", StringComparison.Ordinal) >= 0)
                    continue;

                int dataStart = keywordIndex + StreamKeyword.Length;
                if (dataStart >= pdf.Length)
                    continue;
                dataStart = SkipEndOfLine(pdf, dataStart);

                // PDF FlateDecode data is zlib: a two-byte header, then raw deflate.
                if (dataStart + 2 >= pdf.Length)
                    continue;
                byte compressionByte = pdf[dataStart];
                if ((compressionByte & 0x0F) != 8 || (compressionByte >> 4) > 7)
                    continue;

                string inflated = TryInflate(pdf, dataStart + 2);
                if (inflated.Length > 0 && HasInflatedTextLayerMarkers(inflated))
                    return true;
            }

            return false;
        }

        private static int SkipEndOfLine(byte[] pdf, int index)
        {
            if (pdf[index] == 13 && index + 1 < pdf.Length && pdf[index + 1] == 10)
                return index + 2;
            if (pdf[index] == 10 || pdf[index] == 13 || pdf[index] == 32)
                return index + 1;
            return index;
        }

        /// <summary>
        /// Decodes one deflate stream, bounded on input and output. A truncated or
        /// corrupt stream still yields whatever decoded before the failure, which is
        /// scanned — the caps exist so a large compressed image cannot dominate an import.
        /// </summary>
        private static string TryInflate(byte[] pdf, int deflateStart)
        {
            int inputLength = Math.Min(pdf.Length - deflateStart, FlateInputCapBytes);
            var output = new MemoryStream();
            try
            {
                using (var input = new MemoryStream(pdf, deflateStart, inputLength))
                using (var deflate = new DeflateStream(input, CompressionMode.Decompress))
                {
                    byte[] buffer = new byte[64 * 1024];
                    while (output.Length < FlateOutputCapBytes)
                    {
                        long remaining = FlateOutputCapBytes - output.Length;
                        int toRead = (int)Math.Min((long)buffer.Length, remaining);
                        int read = deflate.Read(buffer, 0, toRead);
                        if (read <= 0)
                            break;
                        output.Write(buffer, 0, read);
                    }
                }
            }
            catch
            {
            }

            return Latin1.GetString(output.GetBuffer(), 0, (int)output.Length);
        }

        /// <summary>
        /// Marker set for inflated streams. Strong signals only: the raw-window set's
        /// short-operator fallback would match decompressed bitmap noise, and any text
        /// PDF must declare fonts somewhere a compressed stream can carry.
        /// </summary>
        private static bool HasInflatedTextLayerMarkers(string content)
        {
            if (content.IndexOf("/ToUnicode", StringComparison.Ordinal) >= 0
                && content.IndexOf("/Font", StringComparison.Ordinal) >= 0)
            {
                return true;
            }

            if (content.IndexOf("/Font", StringComparison.Ordinal) >= 0
                && (content.IndexOf("/WinAnsiEncoding", StringComparison.Ordinal) >= 0
                    || content.IndexOf("/MacRomanEncoding", StringComparison.Ordinal) >= 0
                    || content.IndexOf("/PDFDocEncoding", StringComparison.Ordinal) >= 0))
            {
                return true;
            }

            if (content.IndexOf("/BaseFont", StringComparison.Ordinal) >= 0
                && (content.IndexOf("/TrueType", StringComparison.Ordinal) >= 0
                    || content.IndexOf("/Type1", StringComparison.Ordinal) >= 0))
            {
                return true;
            }

            if (content.IndexOf("BT", StringComparison.Ordinal) >= 0
                && (content.IndexOf("Tj", StringComparison.Ordinal) >= 0
                    || content.IndexOf("TJ", StringComparison.Ordinal) >= 0)
                && FontSelectionOperator.IsMatch(content))
            {
                return true;
            }

            return false;
        }

        private static int IndexOf(byte[] haystack, byte[] needle, int start)
        {
            int last = haystack.Length - needle.Length;
            for (int i = start; i <= last; i++)
            {
                int j = 0;
                while (j < needle.Length && haystack[i + j] == needle[j])
                    j++;
                if (j == needle.Length)
                    return i;
            }
            return -1;
        }
    }
}
