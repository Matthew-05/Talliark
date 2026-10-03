using System.Collections.Generic;
using System.Drawing;
using System.Globalization;
using System.Runtime.InteropServices;
using System.Text;
using Talliark.Addin.Modules.CustomXml.Models;
using Excel = Microsoft.Office.Interop.Excel;

namespace Talliark.Addin.Modules.Services
{
    /// <summary>Applies visual and number formats to Excel cells that host linked rectangles.</summary>
    internal static class CellFormattingService
    {
        private static readonly int AutoFill = ColorTranslator.ToOle(Color.FromArgb(221, 235, 255));
        private static readonly int RawFill  = ColorTranslator.ToOle(Color.FromArgb(220, 252, 231));
        private static readonly int SumFill  = ColorTranslator.ToOle(Color.FromArgb(254, 243, 199));
        private static readonly int TableFill = ColorTranslator.ToOle(Color.FromArgb(237, 233, 254));

        private static readonly int LinkBorderColor = ColorTranslator.ToOle(Color.FromArgb(150, 150, 150));
        private static readonly int LinkBorderWeight = (int)Excel.XlBorderWeight.xlThin;

        private static readonly Excel.XlBordersIndex[] LinkBorderEdges =
        {
            Excel.XlBordersIndex.xlEdgeTop,
            Excel.XlBordersIndex.xlEdgeBottom,
            Excel.XlBordersIndex.xlEdgeLeft,
            Excel.XlBordersIndex.xlEdgeRight,
        };

        private static readonly IAutoDateFormatPolicy AutoDateFormatPolicy =
            new DefaultAutoDateFormatPolicy();

        /// <summary>
        /// Applies the standard link-rectangle cell style: a fill plus a thin border
        /// on every edge that does not already carry a border, so existing borders
        /// are preserved rather than overwritten.
        /// </summary>
        public static void ApplyLinkStyle(Excel.Range cell, LinkType linkType)
        {
            cell.Interior.Color = GetFillColor(linkType);
            ApplyLinkBorders(cell);
        }

        private static void ApplyLinkBorders(Excel.Range range)
        {
            foreach (Excel.XlBordersIndex edge in LinkBorderEdges)
            {
                Excel.Border border = range.Borders[edge];
                if (HasNoLineStyle(border))
                {
                    border.LineStyle = Excel.XlLineStyle.xlContinuous;
                    border.Weight = (Excel.XlBorderWeight)LinkBorderWeight;
                    border.Color = LinkBorderColor;
                }
            }
        }

        private static void ClearLinkBorders(Excel.Range range)
        {
            foreach (Excel.XlBordersIndex edge in LinkBorderEdges)
            {
                Excel.Border border = range.Borders[edge];
                if (BorderInt(border.LineStyle) == (int)Excel.XlLineStyle.xlContinuous
                    && BorderInt(border.Weight) == LinkBorderWeight
                    && BorderInt(border.Color) == LinkBorderColor)
                {
                    border.LineStyle = Excel.XlLineStyle.xlLineStyleNone;
                }
            }
        }

        /// <summary>True when the edge carries no line at all, meaning the add-in is
        /// free to add its own border there without overwriting anything.</summary>
        private static bool HasNoLineStyle(Excel.Border border)
        {
            return BorderInt(border.LineStyle) == (int)Excel.XlLineStyle.xlLineStyleNone;
        }

        /// <summary>
        /// Border.LineStyle and Border.Color come back from the PIA as object, so
        /// they must be unboxed before comparison — a boxed value compared with
        /// == is a reference comparison and is always false.
        /// </summary>
        private static int BorderInt(object value)
        {
            return value is int i ? i : System.Convert.ToInt32(value);
        }

        /// <summary>Applies Auto-link date or number formatting inferred from <paramref name="sourceText"/>.</summary>
        public static void ApplyAutoNumberFormat(Excel.Range cell, string sourceText)
        {
            cell.NumberFormat = GetAutoNumberFormat(sourceText);
        }

        /// <summary>
        /// Returns the Excel number format used for an Auto-link value. Table writes use
        /// this to calculate formats in memory before applying them to rectangular ranges.
        /// </summary>
        internal static string GetAutoNumberFormat(string sourceText)
        {
            return BuildAutoFormat(sourceText) ?? "General";
        }

        /// <summary>Applies Sum-link number formatting inferred from all contributing source texts.</summary>
        public static void ApplySumNumberFormat(Excel.Range cell, IEnumerable<string> sourceTexts)
        {
            string numberFormat = BuildSumNumberFormat(sourceTexts);
            if (numberFormat != null)
                cell.NumberFormat = numberFormat;
        }

        /// <summary>
        /// Renders <paramref name="value"/> the way <paramref name="cell"/> displays its own
        /// contents, by running Excel's TEXT function with the cell's number format. Keeps
        /// figures shown outside the grid consistent with the sheet. Falls back to the
        /// invariant representation when Excel cannot format the value.
        /// </summary>
        public static string FormatLikeCell(Excel.Range cell, double value)
        {
            try
            {
                string numberFormat = cell?.NumberFormat as string;
                if (!string.IsNullOrEmpty(numberFormat))
                {
                    var app = cell.Application as Excel.Application;
                    string text = app?.WorksheetFunction.Text(value, numberFormat);
                    if (!string.IsNullOrWhiteSpace(text))
                        return text.Trim();
                }
            }
            catch (COMException) { }

            return value.ToString("G", CultureInfo.InvariantCulture);
        }

        /// <summary>Applies Sum-link number formatting inferred from one source text.</summary>
        public static void ApplySumNumberFormat(Excel.Range cell, string sourceText)
        {
            ApplySumNumberFormat(cell, new[] { sourceText });
        }

        /// <summary>
        /// Removes the link-rectangle cell background fill and the borders the
        /// add-in applied. Borders that already existed before the link (or that
        /// do not match the applied thin gray style) are left untouched.
        /// </summary>
        public static void ClearLinkStyle(Excel.Range cell)
        {
            cell.Interior.Pattern = Excel.XlPattern.xlPatternNone;
            ClearLinkBorders(cell);
        }

        /// <summary>
        /// Clears link background fill on all <paramref name="cells"/> in one pass
        /// per worksheet by unioning ranges, so Excel repaints once per sheet.
        /// </summary>
        public static void ClearLinkStyles(IEnumerable<Excel.Range> cells, Excel.Application app)
        {
            if (app == null) return;

            var bySheet = new Dictionary<Excel.Worksheet, List<Excel.Range>>();

            foreach (Excel.Range cell in cells)
            {
                if (cell == null) continue;

                ClearLinkBorders(cell);

                var ws = (Excel.Worksheet)cell.Worksheet;
                if (!bySheet.TryGetValue(ws, out List<Excel.Range> list))
                {
                    list = new List<Excel.Range>();
                    bySheet[ws] = list;
                }

                list.Add(cell);
            }

            foreach (List<Excel.Range> sheetCells in bySheet.Values)
            {
                if (sheetCells.Count == 0) continue;

                Excel.Range batch = sheetCells[0];
                for (int i = 1; i < sheetCells.Count; i++)
                    batch = app.Union(batch, sheetCells[i]);

                batch.Interior.Pattern = Excel.XlPattern.xlPatternNone;
            }
        }

        private static string BuildAutoFormat(string text)
        {
            string dateFormat = BuildAutoDateFormat(text);
            if (dateFormat != null)
                return dateFormat;

            return BuildAutoNumberFormat(text);
        }

        private static string BuildAutoDateFormat(string text)
        {
            if (!AutoDateParser.TryParse(text, out AutoDateParseResult result))
                return null;

            return AutoDateFormatPolicy.GetNumberFormat(result);
        }

        private static string BuildAutoNumberFormat(string text)
        {
            if (!TryReadAutoNumberFormat(text, out AutoNumberFormatInfo info))
                return null;

            return BuildNumberFormat(info);
        }

        private static int GetFillColor(LinkType linkType)
        {
            switch (linkType)
            {
                case LinkType.Raw: return RawFill;
                case LinkType.Sum: return SumFill;
                case LinkType.Table: return TableFill;
                default:           return AutoFill;
            }
        }

        private static string BuildSumNumberFormat(IEnumerable<string> sourceTexts)
        {
            if (!TryReadSumNumberFormat(sourceTexts, out AutoNumberFormatInfo info))
                return null;

            return BuildNumberFormat(info);
        }

        private static string BuildNumberFormat(AutoNumberFormatInfo info)
        {
            var sb = new StringBuilder(info.HasThousandsSeparator ? "#,##0" : "0");
            if (info.DecimalPlaces > 0)
            {
                sb.Append(".");
                sb.Append('0', info.DecimalPlaces);
            }

            if (info.IsPercent)
                sb.Append("%");

            string positiveFormat = sb.ToString();
            return info.UsesParenthesesForNegative
                ? positiveFormat + ";(" + positiveFormat + ")"
                : positiveFormat;
        }

        private static bool TryReadAutoNumberFormat(string text, out AutoNumberFormatInfo formatInfo)
        {
            ParsedNumber number = NumberTextParser.TryParseWhole(text);
            formatInfo = number == null ? null : AutoNumberFormatInfo.From(number);
            return number != null;
        }

        private static bool TryReadSumNumberFormat(
            IEnumerable<string> sourceTexts,
            out AutoNumberFormatInfo formatInfo)
        {
            formatInfo = null;
            if (sourceTexts == null) return false;

            var info = new AutoNumberFormatInfo();
            bool found = false;

            foreach (string text in sourceTexts)
            {
                if (string.IsNullOrEmpty(text)) continue;

                foreach (ParsedNumber number in NumberTextParser.FindAll(text))
                {
                    found = true;
                    info.HasThousandsSeparator |= number.HasThousandsSeparator;
                    info.DecimalPlaces = System.Math.Max(info.DecimalPlaces, number.DecimalPlaces);
                    info.UsesParenthesesForNegative |= number.UsesParenthesesForNegative;
                    info.IsPercent |= number.IsPercent;
                }
            }

            formatInfo = found ? info : null;
            return found;
        }

        private sealed class AutoNumberFormatInfo
        {
            public bool HasThousandsSeparator { get; set; }
            public int DecimalPlaces { get; set; }
            public bool UsesParenthesesForNegative { get; set; }
            public bool IsPercent { get; set; }

            public static AutoNumberFormatInfo From(ParsedNumber number) => new AutoNumberFormatInfo
            {
                HasThousandsSeparator = number.HasThousandsSeparator,
                DecimalPlaces = number.DecimalPlaces,
                UsesParenthesesForNegative = number.UsesParenthesesForNegative,
                IsPercent = number.IsPercent,
            };
        }
    }
}
