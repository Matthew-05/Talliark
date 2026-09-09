using System.Collections.Generic;
using System.Globalization;
using System.Text;
using System.Text.RegularExpressions;

namespace Talliark.Addin.Modules.Services
{
    /// <summary>
    /// Converts extracted PDF text to cell values according to the selected link type.
    /// </summary>
    internal static class TextValueFormatter
    {
        /// <summary>
        /// Auto format: converts date-like values to Excel date serials, converts
        /// parenthetical numbers to negatives, converts percentages ("1.15%") to their
        /// decimal fraction (0.0115) as Excel expects for percent-formatted cells, and
        /// strips numeric thousand-separator commas. Returns a <see cref="double"/> when
        /// the entire trimmed text is a date or number; otherwise returns normalized
        /// literal text that Excel cannot reinterpret as a formula.
        /// </summary>
        public static object FormatAuto(string text)
        {
            if (AutoDateParser.TryParse(text, out AutoDateParseResult dateResult))
                return dateResult.Value.ToOADate();

            ParsedNumber number = NumberTextParser.TryParseWhole(text);
            if (number != null)
                return number.Value;

            return FormatLiteralText(NormalizeAutoTextContent(text));
        }

        /// <summary>
        /// Makes extracted document text safe to assign through Excel's <c>Value2</c>
        /// property. Excel interprets text beginning with <c>=</c>, <c>+</c>, <c>-</c>
        /// or <c>@</c> as a formula even when it came from a PDF. Prefixing an apostrophe
        /// is Excel's native literal-text escape; it is not displayed in the cell or
        /// returned by <c>Value2</c>.
        /// </summary>
        public static string FormatLiteralText(string text)
        {
            string value = text ?? string.Empty;
            int firstContent = 0;
            while (firstContent < value.Length && char.IsWhiteSpace(value[firstContent]))
                firstContent++;

            if (firstContent < value.Length
                && (value[firstContent] == '='
                    || value[firstContent] == '+'
                    || value[firstContent] == '-'
                    || value[firstContent] == '@'))
                return "'" + value;

            return value;
        }

        private static string NormalizeAutoTextContent(string text)
        {
            if (string.IsNullOrWhiteSpace(text))
                return string.Empty;

            string withoutLineBreaks = Regex.Replace(text.Trim(), @"\r\n|\r|\n", " ");
            return Regex.Replace(withoutLineBreaks, @"\s+", " ");
        }

        /// <summary>
        /// Builds an Excel formula string that sums all numbers found in <paramref name="text"/>.
        /// Returns <c>null</c> if no numbers are found.
        /// Always produces a formula (e.g. <c>=1234</c> for a single number).
        /// </summary>
        public static string BuildSumFormula(string text)
        {
            var numbers = ExtractNumbers(text);
            if (numbers.Count == 0) return null;
            return BuildFormulaFromNumbers(numbers);
        }

        /// <summary>
        /// Rebuilds a sum formula from multiple source texts (one per contributing rectangle).
        /// Numbers from each text are concatenated in order.
        /// Returns <c>null</c> if no numbers are found across all texts.
        /// </summary>
        public static string RebuildSumFormula(IEnumerable<string> sourceTexts)
        {
            var all = new List<double>();
            foreach (string src in sourceTexts)
            {
                if (!string.IsNullOrEmpty(src))
                    all.AddRange(ExtractNumbers(src));
            }
            if (all.Count == 0) return null;
            return BuildFormulaFromNumbers(all);
        }

        /// <summary>
        /// Returns how many numbers <paramref name="text"/> contributes to a sum — the
        /// count of terms this text adds to the formula. Zero when it holds no numbers.
        /// </summary>
        public static int CountValues(string text)
        {
            return ExtractNumbers(text).Count;
        }

        /// <summary>
        /// Returns the total <paramref name="text"/> contributes to a sum, i.e. the sum of
        /// every number it holds, or <c>null</c> when it holds none.
        /// </summary>
        public static double? SumValues(string text)
        {
            List<double> numbers = ExtractNumbers(text);
            if (numbers.Count == 0) return null;

            double total = 0;
            foreach (double number in numbers)
                total += number;

            return total;
        }

        private static List<double> ExtractNumbers(string text)
        {
            var result = new List<double>();
            if (string.IsNullOrEmpty(text)) return result;

            // Percentages are resolved to their decimal fraction (1.15% -> 0.0115) so they
            // sum correctly alongside plain and parenthetical-negative numbers.
            foreach (ParsedNumber number in NumberTextParser.FindAll(text))
                result.Add(number.Value);

            return result;
        }

        private static string BuildFormulaFromNumbers(List<double> numbers)
        {
            var sb = new StringBuilder("=");
            for (int i = 0; i < numbers.Count; i++)
            {
                if (i > 0) sb.Append("+");
                // Format without trailing zeros but preserves decimals
                sb.Append(numbers[i].ToString("G", CultureInfo.InvariantCulture));
            }
            return sb.ToString();
        }

    }
}
