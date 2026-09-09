using System;
using System.Collections.Generic;
using System.Linq;
using System.Runtime.InteropServices;
using Talliark.Addin.Modules.CustomXml;
using Talliark.Addin.Modules.CustomXml.Models;
using Excel = Microsoft.Office.Interop.Excel;
using System.Windows.Forms;

namespace Talliark.Addin.Modules.Services
{
    /// <summary>
    /// Updates an existing link rectangle's geometry and replaces the linked
    /// Excel cell text with re-extracted content from the viewer.
    /// </summary>
    internal sealed class UpdateLinkService
    {
        public bool UpdateLink(
            string rectId,
            int page,
            double x, double y, double width, double height,
            string text,
            TableGrid tableGrid,
            IList<IList<string>> tableCells,
            IWin32Window owner,
            Excel.Workbook workbook)
        {
            if (string.IsNullOrWhiteSpace(rectId) || workbook == null)
                return false;

            WorkbookProtectionGuard.ThrowIfStructureProtected(workbook);

            try
            {
                WorkbookStorageSession session = Globals.ThisAddIn.GetStorageSession(workbook);
                if (!session.TryGetLink(rectId, out LinkedRectangle existing))
                    return false;

                Excel.Range cell = LinkCellResolver.TryResolveCell(workbook, existing);
                if (existing.LinkType == LinkType.Table)
                {
                    if (cell == null || tableGrid == null || tableCells == null)
                        return false;

                    var tableWriter = new TableExcelWriteService();
                    if (!tableWriter.ConfirmUpdate(
                        cell, existing.TableGrid ?? new TableGrid(), tableGrid, tableCells, owner))
                        return false;

                    tableWriter.Rewrite(
                        cell, existing.TableGrid ?? new TableGrid(), tableGrid, tableCells);
                }
                else if (cell != null)
                {
                    try
                    {
                        WriteToCellForUpdate(cell, text, existing, session, workbook);
                    }
                    catch (COMException ex)
                    {
                        System.Diagnostics.Debug.WriteLine(
                            $"[Talliark] UpdateLink cell write failed: {ex.Message}");
                    }
                }

                var rect    = new PdfRectangle(page, x, y, width, height, RectangleCoordinateSpace.Normalized);
                var updated = new LinkedRectangle(existing.Id, existing.PdfId, existing.LinkedCell, rect)
                {
                    LinkType   = existing.LinkType,
                    SourceText = existing.LinkType == LinkType.Sum ? text : null,
                    TableGrid  = existing.LinkType == LinkType.Table ? tableGrid : existing.TableGrid,
                };
                return session.UpdateLink(updated);
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine(
                    $"[Talliark] UpdateLinkService.UpdateLink failed: {ex.Message}");
                return false;
            }
        }

        private static void WriteToCellForUpdate(
            Excel.Range cell,
            string text,
            LinkedRectangle existing,
            WorkbookStorageSession session,
            Excel.Workbook workbook)
        {
            switch (existing.LinkType)
            {
                case LinkType.Raw:
                    cell.Value2 = TextValueFormatter.FormatLiteralText(text);
                    break;

                case LinkType.Sum:
                    // Gather sourceTexts from all sum rects pointing to this cell,
                    // replacing the updated rect's contribution with the new text.
                    IList<LinkedRectangle> allLinks = session.GetLinks();
                    var sourceTexts = allLinks
                        .Where(r => r.LinkType == LinkType.Sum && SameCell(r, existing))
                        .Select(r => string.Equals(r.Id, existing.Id, StringComparison.Ordinal)
                            ? text                // use the new text for the rect being updated
                            : r.SourceText)
                        .ToList();

                    string formula = TextValueFormatter.RebuildSumFormula(sourceTexts);
                    if (formula != null)
                    {
                        CellFormattingService.ApplySumNumberFormat(cell, sourceTexts);
                        cell.Formula = formula;
                        cell.Calculate();
                    }
                    else
                        cell.Value2 = TextValueFormatter.FormatLiteralText(text);
                    break;

                default: // Auto
                    cell.Value2 = TextValueFormatter.FormatAuto(text);
                    CellFormattingService.ApplyAutoNumberFormat(cell, text);
                    break;
            }
        }

        private static bool SameCell(LinkedRectangle a, LinkedRectangle b)
        {
            return a.LinkedCell.TrackIndex == b.LinkedCell.TrackIndex
                || (string.Equals(a.LinkedCell.SheetName, b.LinkedCell.SheetName, StringComparison.OrdinalIgnoreCase)
                    && string.Equals(a.LinkedCell.Address, b.LinkedCell.Address, StringComparison.Ordinal));
        }
    }
}
