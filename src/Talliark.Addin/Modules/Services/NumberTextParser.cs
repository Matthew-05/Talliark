using System.Collections.Generic;
using System.Globalization;
using System.Text.RegularExpressions;

namespace Talliark.Addin.Modules.Services
{
    /// <summary>
    /// A single number recognized inside extracted PDF text, carrying both the
    /// resolved numeric value (sign and percentage already applied) and the
    /// formatting cues needed to redisplay it the way it originally appeared.
    /// </summary>
    internal sealed class ParsedNumber
    {
        /// <summary>The numeric value, e.g. "1.15%" resolves to 0.0115 and "(4)" resolves to -4.</summary>
        public double Value { get; set; }
        public bool HasThousandsSeparator { get; set; }
        public int DecimalPlaces { get; set; }
        public bool UsesParenthesesForNegative { get; set; }
        public bool IsPercent { get; set; }
        public double Magnitude { get; set; } = 1d;
    }

    /// <summary>
    /// Shared number-text recognition for Auto-link and Sum-link. Understands plain
    /// numbers ("1,234.56"), magnitude-modified numbers ("1.5 million", "2MM"),
    /// parenthetical negatives ("(1,234.56)"), and percentages ("1.15%", "(1.15%)")
    /// so both cell-value derivation (<see cref="TextValueFormatter"/>)
    /// and number-format inference (<see cref="CellFormattingService"/>) agree on what a
    /// piece of source text means.
    /// </summary>
    internal static class NumberTextParser
    {
        private const string MagnitudePattern =
            @"(thousands?|millions?|billions?|trillions?|thou|tril|bln|mln|trn|ths|bil|mil|bn|mn|mm|tn|th|k|m|b|t)";

        private static readonly Regex _wholeParentheticalPercent =
            new Regex(@"^\(([\d,]+(?:\.\d+)?)%\)$", RegexOptions.Compiled | RegexOptions.IgnoreCase);

        private static readonly Regex _wholeParenthetical =
            new Regex(@"^\(([\d,]+(?:\.\d+)?)" + MagnitudePattern + @"?\)$", RegexOptions.Compiled | RegexOptions.IgnoreCase);

        private static readonly Regex _wholePercent =
            new Regex(@"^([+\-]?[\d,]+(?:\.\d+)?)%$", RegexOptions.Compiled | RegexOptions.IgnoreCase);

        private static readonly Regex _wholePlain =
            new Regex(@"^([+\-]?[\d,]+(?:\.\d+)?)" + MagnitudePattern + @"?$", RegexOptions.Compiled | RegexOptions.IgnoreCase);

        private static readonly Regex _anyParentheticalPercent =
            new Regex(@"\(([\d,]+(?:\.\d+)?)%\)", RegexOptions.Compiled);

        private static readonly Regex _anyParenthetical =
            new Regex(@"\(([\d,]+(?:\.\d+)?)\s*" + MagnitudePattern + @"?\)", RegexOptions.Compiled | RegexOptions.IgnoreCase);

        private static readonly Regex _anyPercent =
            new Regex(@"\b([\d,]+(?:\.\d+)?)%", RegexOptions.Compiled);

        private static readonly Regex _anyPlain =
            new Regex(@"\b([\d,]+(?:\.\d+)?)\s*" + MagnitudePattern + @"?\b", RegexOptions.Compiled | RegexOptions.IgnoreCase);

        /// <summary>
        /// Parses <paramref name="text"/> as a single number if the entire (trimmed,
        /// currency-stripped) text is one. Returns <c>null</c> if it is not.
        /// </summary>
        public static ParsedNumber TryParseWhole(string text)
        {
            if (string.IsNullOrEmpty(text)) return null;

            string trimmed = Normalize(text);

            Match match = _wholeParentheticalPercent.Match(trimmed);
            if (match.Success)
                return BuildParsedNumber(match.Groups[1].Value, isParenthetical: true, isPercent: true);

            match = _wholeParenthetical.Match(trimmed);
            if (match.Success)
                return BuildParsedNumber(match.Groups[1].Value, isParenthetical: true, isPercent: false, match.Groups[2].Value);

            match = _wholePercent.Match(trimmed);
            if (match.Success)
                return BuildParsedNumber(match.Groups[1].Value, isParenthetical: false, isPercent: true);

            match = _wholePlain.Match(trimmed);
            if (match.Success)
                return BuildParsedNumber(match.Groups[1].Value, isParenthetical: false, isPercent: false, match.Groups[2].Value);

            return null;
        }

        /// <summary>
        /// Finds every number inside <paramref name="text"/> (parenthetical, percentage,
        /// or plain), in reading order, without double-counting a number matched by more
        /// than one category (e.g. a parenthetical percentage is not also counted as plain).
        /// </summary>
        public static IEnumerable<ParsedNumber> FindAll(string text)
        {
            var results = new List<(int Index, ParsedNumber Number)>();
            if (string.IsNullOrEmpty(text)) return results.ConvertAll(r => r.Number);

            var consumed = new bool[text.Length];

            void Consume(Match m)
            {
                for (int i = m.Index; i < m.Index + m.Length; i++)
                    consumed[i] = true;
            }

            foreach (Match m in _anyParentheticalPercent.Matches(text))
            {
                ParsedNumber n = BuildParsedNumber(m.Groups[1].Value, true, true);
                if (n != null) { results.Add((m.Index, n)); Consume(m); }
            }

            foreach (Match m in _anyParenthetical.Matches(text))
            {
                if (consumed[m.Index]) continue;
                ParsedNumber n = BuildParsedNumber(m.Groups[1].Value, true, false, m.Groups[2].Value);
                if (n != null) { results.Add((m.Index, n)); Consume(m); }
            }

            foreach (Match m in _anyPercent.Matches(text))
            {
                if (consumed[m.Index]) continue;
                ParsedNumber n = BuildParsedNumber(m.Groups[1].Value, false, true);
                if (n != null) { results.Add((m.Index, n)); Consume(m); }
            }

            foreach (Match m in _anyPlain.Matches(text))
            {
                if (consumed[m.Index]) continue;
                ParsedNumber n = BuildParsedNumber(m.Groups[1].Value, false, false, m.Groups[2].Value);
                if (n != null) { results.Add((m.Index, n)); Consume(m); }
            }

            results.Sort((a, b) => a.Index.CompareTo(b.Index));
            return results.ConvertAll(r => r.Number);
        }

        private static ParsedNumber BuildParsedNumber(
            string sourceNumber,
            bool isParenthetical,
            bool isPercent,
            string magnitudeText = null)
        {
            string digits = sourceNumber.Replace(",", "");
            if (!double.TryParse(digits, NumberStyles.Any, CultureInfo.InvariantCulture, out double parsed))
                return null;

            double magnitude = ResolveMagnitude(magnitudeText);
            if (isPercent) parsed /= 100d;
            else parsed *= magnitude;
            if (isParenthetical) parsed = -parsed;

            int decimalIndex = sourceNumber.IndexOf('.');
            return new ParsedNumber
            {
                Value = parsed,
                HasThousandsSeparator = sourceNumber.IndexOf(',') >= 0 || magnitude > 1d,
                DecimalPlaces = decimalIndex >= 0 ? sourceNumber.Length - decimalIndex - 1 : 0,
                UsesParenthesesForNegative = isParenthetical,
                IsPercent = isPercent,
                Magnitude = magnitude,
            };
        }

        private static double ResolveMagnitude(string text)
        {
            switch ((text ?? string.Empty).ToLowerInvariant())
            {
                case "k":
                case "th":
                case "ths":
                case "thou":
                case "thousand":
                case "thousands": return 1_000d;
                case "m":
                case "mm":
                case "mn":
                case "mil":
                case "mln":
                case "million":
                case "millions": return 1_000_000d;
                case "b":
                case "bn":
                case "bln":
                case "bil":
                case "billion":
                case "billions": return 1_000_000_000d;
                case "t":
                case "tn":
                case "trn":
                case "tril":
                case "trillion":
                case "trillions": return 1_000_000_000_000d;
                default: return 1d;
            }
        }

        private static string Normalize(string text)
        {
            string normalized = Regex.Replace(text.Trim(), @"\s+", "");
            return Regex.Replace(
                normalized,
                @"^(\()?([+\-]?)(?:USD|EUR|GBP|JPY|CAD|AUD|CHF|CNY|INR|KRW|[$€£¥₹₩])",
                "$1$2",
                RegexOptions.IgnoreCase);
        }
    }
}
