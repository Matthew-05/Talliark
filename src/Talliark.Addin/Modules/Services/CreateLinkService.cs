using System;
using System.Collections.Generic;
using System.Linq;
using Talliark.Addin.Modules.CustomXml;
using Talliark.Addin.Modules.CustomXml.Models;
using Excel = Microsoft.Office.Interop.Excel;
using System.Windows.Forms;
using static Talliark.Addin.Modules.TalliarkLog;

namespace Talliark.Addin.Modules.Services
{
    /// <summary>
    /// Handles the full link-rectangle creation flow on the C# side:
    /// finds an empty target cell, inserts the extracted text (formatted
    /// according to the chosen <see cref="LinkType"/>), applies the link style,
    /// and persists the <see cref="LinkedRectangle"/> to storage.
    /// </summary>
    internal sealed class CreateLinkService
    {
        private const int MaxSearchColumns = 100;

        /// <summary>
        /// Returns the new <see cref="LinkedRectangle"/> and the complete updated list of
        /// all linked rectangles so callers can propagate the data to the viewer without a
        /// second round-trip to storage.
        /// </summary>
        /// <remarks>
        /// The target cells are supplied by the caller rather than read from
        /// <c>Application.Selection</c> here: that property answers for the active window,
        /// and activating the pane's window changes it without a selection event, which
        /// wrote links onto the pane window's sheet while the user worked on another. See
        /// <see cref="ThisAddIn.TryGetLinkTargetCell"/>.
        /// </remarks>
        public (LinkedRectangle LinkedRect, IList<LinkedRectangle> AllRects) CreateLink(
            string pdfId,
            int page,
            double x, double y, double width, double height,
            string text,
            LinkType linkType,
            bool appendToActiveSum,
            TableGrid tableGrid,
            IList<IList<string>> tableCells,
            Excel.Range startCell,
            Excel.Range activeCell,
            IWin32Window owner,
            Excel.Workbook workbook)
        {
            if (workbook == null) throw new ArgumentNullException(nameof(workbook));
            if (startCell == null) throw new ArgumentNullException(nameof(startCell));
            WorkbookProtectionGuard.ThrowIfStructureProtected(workbook);

            Trace($"startCell={((Excel.Worksheet)startCell.Worksheet).Name}!{startCell.Address}");

            WorkbookStorageSession session = Globals.ThisAddIn.GetStorageSession(workbook);

            if (linkType == LinkType.Table)
            {
                Excel.Range tableAnchor = activeCell ?? startCell;
                return CreateTableLink(
                    tableAnchor, pdfId, page, x, y, width, height,
                    tableGrid, tableCells, owner, session, workbook);
            }

            // Sum-append: if the currently active cell is already a Sum link cell, add
            // this rectangle's numbers to its formula instead of targeting a new cell.
            if (linkType == LinkType.Sum && appendToActiveSum)
            {
                using (Time("SumAppendCheck"))
                {
                    var (appendRect, appendAll) = TryAppendToSumCell(
                        startCell, text, pdfId, page, x, y, width, height, session, workbook);
                    if (appendRect != null)
                        return (appendRect, appendAll);
                }
            }

            IList<LinkedRectangle> links = session.GetLinks();
            Excel.Range cell = FindAvailableCell(startCell, links);
            if (cell == null)
            {
                Trace($"no available target cell found within {MaxSearchColumns} columns");
                return (null, links);
            }
            Trace($"target cell={cell.Address} (moved={cell.Column != startCell.Column})");

            // Refuse before any side effect: the write, the binding and the cursor move are
            // all pointless on a cell Excel will not accept, and the caller explains why.
            EnsureCellWritable(cell);

            if (cell.Row != startCell.Row || cell.Column != startCell.Column)
            {
                Trace("cell moved right – calling Activate+Select");
                ((Excel.Worksheet)cell.Worksheet).Activate();
                cell.Select();
                Trace("Activate+Select done");
            }
            else
            {
                Trace("cell did NOT move – no Select called");
            }

            string sheetName = ((Excel.Worksheet)cell.Worksheet).Name;
            string address   = cell.Address;

            int trackIndex;
            using (Time("NextTrackIndex"))
            {
                trackIndex = LinkCellTracker.NextTrackIndex(links);
            }

            var linkedCell = new LinkedCell(sheetName, address, trackIndex);
            var rect       = new PdfRectangle(page, x, y, width, height, RectangleCoordinateSpace.Normalized);
            var linkedRect = new LinkedRectangle(Guid.NewGuid().ToString("D"), pdfId, linkedCell, rect)
            {
                LinkType   = linkType,
                SourceText = linkType == LinkType.Sum ? text : null,
            };

            Trace("calling BindCell");
            LinkCellTracker.BindCell(workbook, cell, trackIndex);
            Trace("BindCell done");

            string previousNumberFormat = LinkCreationUndoStack.TryReadNumberFormat(cell);

            try
            {
                WriteToCell(cell, text, linkType);
                CellFormattingService.ApplyLinkStyle(cell, linkType);
                Trace("style applied");
            }
            catch
            {
                LinkCellTracker.UnbindCell(workbook, cell, trackIndex);
                throw;
            }

            using (Time("SaveLinks"))
            {
                session.AddLink(linkedRect);
            }

            RecordUndoEntry(workbook, linkedRect.Id, trackIndex, cell, previousNumberFormat);
            Trace("returning");

            return (linkedRect, session.GetLinks());
        }

        /// <summary>
        /// Finds the first cell at or to the right of <paramref name="startCell"/> that has
        /// neither Excel content nor an existing Talliark link binding.
        /// </summary>
        /// <remarks>
        /// A Table link is bound to its top-left cell even when that table cell is empty.
        /// Looking only at <c>Value2</c> therefore mistakes the anchor for an available cell
        /// and lets the next interactive link overwrite the table binding. Reading
        /// <c>Formula</c> also preserves formulas whose displayed result is an empty string.
        /// </remarks>
        private static Excel.Range FindAvailableCell(
            Excel.Range startCell,
            IList<LinkedRectangle> links)
        {
            var worksheet = (Excel.Worksheet)startCell.Worksheet;
            int candidateCount = Math.Min(
                MaxSearchColumns,
                worksheet.Columns.Count - startCell.Column + 1);
            Excel.Range lastCandidate = startCell.get_Offset(0, candidateCount - 1);
            Excel.Range searchRange = worksheet.Range[startCell, lastCandidate];

            var linkedColumns = new HashSet<int>(
                LinkCellResolver.ResolveLinksInSelection(links, searchRange)
                    .Select(entry => entry.Cell.Column));

            Excel.Range candidate = startCell;
            for (int offset = 0; offset < candidateCount; offset++)
            {
                object formula = candidate.Formula;
                bool hasContent = formula != null
                    && !string.IsNullOrWhiteSpace(formula.ToString());
                if (!hasContent && !linkedColumns.Contains(candidate.Column))
                    return candidate;

                if (offset + 1 < candidateCount)
                    candidate = candidate.get_Offset(0, 1);
            }

            return null;
        }

        /// <summary>
        /// Throws <see cref="WorkbookProtectionGuard.ProtectedSheetException"/> when Excel
        /// would refuse the write, so callers can explain the refusal instead of reporting a
        /// generic failure after partial work.
        /// </summary>
        private static void EnsureCellWritable(Excel.Range cell)
        {
            if (!WorkbookProtectionGuard.IsWriteBlocked(cell)) return;

            throw new WorkbookProtectionGuard.ProtectedSheetException(
                WorkbookProtectionGuard.GetSheetName(cell));
        }

        private (LinkedRectangle LinkedRect, IList<LinkedRectangle> AllRects) CreateTableLink(
            Excel.Range startCell,
            string pdfId,
            int page,
            double x, double y, double width, double height,
            TableGrid tableGrid,
            IList<IList<string>> tableCells,
            IWin32Window owner,
            WorkbookStorageSession session,
            Excel.Workbook workbook)
        {
            if (tableGrid == null || tableCells == null)
                return (null, session.GetLinks());

            // The whole footprint must accept the write. Checked before the overwrite prompt:
            // there is no point asking the user to confirm a table that cannot land.
            Excel.Range footprint = TableExcelWriteService.GetFootprint(startCell, tableGrid);
            EnsureCellWritable(footprint);

            var tableWriter = new TableExcelWriteService();
            if (!tableWriter.ConfirmCreate(startCell, tableGrid, tableCells, owner))
                return (null, session.GetLinks());

            // Continuing through a conflict must not leave an older rectangle bound to a
            // cell whose value this table is about to replace.
            IList<string> replacedIds =
                new DeleteLinkService().DeleteLinksInSelection(footprint, workbook);
            Globals.ThisAddIn.GetLinkUndoStack(workbook)?.DropEntriesFor(replacedIds);

            string sheetName = ((Excel.Worksheet)startCell.Worksheet).Name;
            string address = startCell.Address;
            int trackIndex = LinkCellTracker.NextTrackIndex(session.GetLinks());
            var linkedCell = new LinkedCell(sheetName, address, trackIndex);
            var rect = new PdfRectangle(
                page, x, y, width, height, RectangleCoordinateSpace.Normalized);
            var linkedRect = new LinkedRectangle(
                Guid.NewGuid().ToString("D"), pdfId, linkedCell, rect)
            {
                LinkType = LinkType.Table,
                TableGrid = tableGrid,
            };

            string previousNumberFormat = LinkCreationUndoStack.TryReadNumberFormat(startCell);
            LinkCellTracker.BindCell(workbook, startCell, trackIndex);
            try
            {
                tableWriter.WriteCreate(startCell, tableGrid, tableCells);
                CellFormattingService.ApplyLinkStyle(startCell, LinkType.Table);
            }
            catch
            {
                LinkCellTracker.UnbindCell(workbook, startCell, trackIndex);
                throw;
            }

            session.AddLink(linkedRect);
            RecordUndoEntry(workbook, linkedRect.Id, trackIndex, startCell, previousNumberFormat);
            return (linkedRect, session.GetLinks());
        }

        /// <summary>
        /// Records how to reverse a creation, on the workbook's in-memory undo stack.
        /// </summary>
        /// <remarks>
        /// Captured here rather than by the caller because only this service knows which cell
        /// the rightward scan settled on, and it is the only place that sees the cell's number
        /// format before the write replaces it. Deliberately not called from
        /// <see cref="CreateLinkAtCell"/>: the document-linker's batch flow is outside the
        /// scope of keystroke undo.
        /// </remarks>
        private static void RecordUndoEntry(
            Excel.Workbook workbook,
            string rectId,
            int trackIndex,
            Excel.Range cell,
            string previousNumberFormat)
        {
            LinkCreationUndoEntry entry =
                LinkCreationUndoStack.TryCapture(rectId, trackIndex, cell, previousNumberFormat);
            if (entry == null) return;

            Globals.ThisAddIn.GetLinkUndoStack(workbook)?.Push(entry);
            Globals.ThisAddIn.ArmExcelUndoForLinkCreation();
        }

        /// <summary>
        /// Creates a link writing directly to <paramref name="targetCell"/>, bypassing the
        /// "search rightward for empty cell" logic used in the interactive flow.
        /// Used by the document-linker batch workflow.
        /// </summary>
        public (LinkedRectangle LinkedRect, IList<LinkedRectangle> AllRects) CreateLinkAtCell(
            string pdfId,
            int page,
            double x, double y, double width, double height,
            string text,
            LinkType linkType,
            Excel.Range targetCell,
            Excel.Workbook workbook)
        {
            if (workbook == null)   throw new ArgumentNullException(nameof(workbook));
            if (targetCell == null) throw new ArgumentNullException(nameof(targetCell));
            WorkbookProtectionGuard.ThrowIfStructureProtected(workbook);

            Trace($"CreateLinkAtCell cell={targetCell.Address}");

            IList<string> replacedIds = new DeleteLinkService().DeleteLinksInSelection(targetCell, workbook);
            if (replacedIds.Count > 0)
                Trace($"CreateLinkAtCell replaced existing links count={replacedIds.Count}");

            WorkbookStorageSession session = Globals.ThisAddIn.GetStorageSession(workbook);

            string sheetName = ((Excel.Worksheet)targetCell.Worksheet).Name;
            string address   = targetCell.Address;

            IList<LinkedRectangle> links;
            int trackIndex;
            using (Time("GetLinks + NextTrackIndex"))
            {
                links = session.GetLinks();
                trackIndex = LinkCellTracker.NextTrackIndex(links);
            }

            var linkedCell = new LinkedCell(sheetName, address, trackIndex);
            var rect       = new PdfRectangle(page, x, y, width, height, RectangleCoordinateSpace.Normalized);
            var linkedRect = new LinkedRectangle(Guid.NewGuid().ToString("D"), pdfId, linkedCell, rect)
            {
                LinkType   = linkType,
                SourceText = linkType == LinkType.Sum ? text : null,
            };

            Trace("calling BindCell");
            LinkCellTracker.BindCell(workbook, targetCell, trackIndex);
            Trace("BindCell done");

            try
            {
                WriteToCell(targetCell, text, linkType);
                CellFormattingService.ApplyLinkStyle(targetCell, linkType);
                Trace("style applied");
            }
            catch
            {
                LinkCellTracker.UnbindCell(workbook, targetCell, trackIndex);
                throw;
            }

            using (Time("SaveLinks"))
            {
                session.AddLink(linkedRect);
            }
            Trace("returning");

            return (linkedRect, session.GetLinks());
        }

        /// <summary>
        /// If <paramref name="startCell"/> is already a Talliark Sum link cell (verified via
        /// storage lookup), appends the new rectangle's numbers to the existing formula and
        /// adds a new <see cref="LinkedRectangle"/> pointing to the same cell.
        /// Returns <c>(null, null)</c> when the append condition is not met.
        /// </summary>
        private (LinkedRectangle, IList<LinkedRectangle>) TryAppendToSumCell(
            Excel.Range startCell,
            string text,
            string pdfId,
            int page,
            double x, double y, double width, double height,
            WorkbookStorageSession session,
            Excel.Workbook workbook)
        {
            string startAddress = startCell.Address;
            string startSheet   = ((Excel.Worksheet)startCell.Worksheet).Name;

            IList<LinkedRectangle> links = session.GetLinks();

            // Find sum rects whose resolved cell matches the active cell
            LinkedRectangle existingSum = null;
            foreach (var link in links)
            {
                if (link.LinkType != LinkType.Sum) continue;
                Excel.Range resolved = LinkCellResolver.TryResolveCell(workbook, link);
                if (resolved == null) continue;

                string resolvedSheet = ((Excel.Worksheet)resolved.Worksheet).Name;
                if (string.Equals(resolvedSheet, startSheet, StringComparison.OrdinalIgnoreCase)
                    && string.Equals(resolved.Address, startAddress, StringComparison.Ordinal))
                {
                    existingSum = link;
                    break;
                }
            }

            if (existingSum == null) return (null, null);

            Trace($"Sum append: existing rect id={existingSum.Id}, cell={startAddress}");

            // Gather all sum rects for this cell (the ones we just found + the new one)
            var sumRectsForCell = links
                .Where(r => r.LinkType == LinkType.Sum && SameCell(r, existingSum))
                .Select(r => r.SourceText)
                .ToList();
            sumRectsForCell.Add(text);

            string formula = TextValueFormatter.RebuildSumFormula(sumRectsForCell);
            if (formula == null) formula = "0";

            // The append writes the same cell the sum already occupies; a protected sheet
            // would swallow the update inside the catch below.
            EnsureCellWritable(startCell);

            string previousNumberFormat = LinkCreationUndoStack.TryReadNumberFormat(startCell);

            try
            {
                CellFormattingService.ApplySumNumberFormat(startCell, sumRectsForCell);
                CellFormattingService.ApplyLinkStyle(startCell, LinkType.Sum);
                startCell.Formula = formula;
                startCell.Calculate();
            }
            catch (Exception ex) { Trace($"Sum append formula write failed: {ex.Message}"); }

            // New LinkedRectangle shares the same LinkedCell (same sheet/address/trackIndex)
            var rect       = new PdfRectangle(page, x, y, width, height, RectangleCoordinateSpace.Normalized);
            var linkedRect = new LinkedRectangle(Guid.NewGuid().ToString("D"), pdfId, existingSum.LinkedCell, rect)
            {
                LinkType   = LinkType.Sum,
                SourceText = text,
            };

            using (Time("SaveLinks (sum append)"))
            {
                session.AddLink(linkedRect);
            }

            RecordUndoEntry(
                workbook, linkedRect.Id, existingSum.LinkedCell.TrackIndex, startCell, previousNumberFormat);

            return (linkedRect, session.GetLinks());
        }

        private static bool SameCell(LinkedRectangle a, LinkedRectangle b)
        {
            return a.LinkedCell.TrackIndex == b.LinkedCell.TrackIndex
                || (string.Equals(a.LinkedCell.SheetName, b.LinkedCell.SheetName, StringComparison.OrdinalIgnoreCase)
                    && string.Equals(a.LinkedCell.Address, b.LinkedCell.Address, StringComparison.Ordinal));
        }

        private static void WriteToCell(Excel.Range cell, string text, LinkType linkType)
        {
            Trace($"WriteToCell linkType={linkType} text='{text}'");
            switch (linkType)
            {
                case LinkType.Raw:
                    cell.Value2 = TextValueFormatter.FormatLiteralText(text);
                    break;

                case LinkType.Sum:
                    string formula = TextValueFormatter.BuildSumFormula(text);
                    if (formula != null)
                    {
                        CellFormattingService.ApplySumNumberFormat(cell, text);
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
    }
}
