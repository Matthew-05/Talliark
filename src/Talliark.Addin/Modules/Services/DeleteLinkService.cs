using System;
using System.Collections.Generic;
using System.Linq;
using System.Runtime.InteropServices;
using Talliark.Addin.Modules.CustomXml;
using Talliark.Addin.Modules.CustomXml.Models;
using Excel = Microsoft.Office.Interop.Excel;

namespace Talliark.Addin.Modules.Services
{
    /// <summary>
    /// Removes persisted link rectangles and cleans up the associated Excel tracking.
    /// Full deletion also removes the Excel data owned by the link; unlink-only deletion
    /// preserves values and formulas.
    /// </summary>
    internal sealed class DeleteLinkService
    {
        public bool DeleteLink(
            string rectId,
            Excel.Workbook workbook,
            bool deleteCellData = false)
        {
            if (string.IsNullOrWhiteSpace(rectId) || workbook == null)
                return false;

            WorkbookProtectionGuard.ThrowIfStructureProtected(workbook);

            using (Globals.ThisAddIn.EnterSelectionNavSuppress())
            {
                return DeleteLinkCore(rectId, workbook, deleteCellData);
            }
        }

        private bool DeleteLinkCore(
            string rectId,
            Excel.Workbook workbook,
            bool deleteCellData)
        {
            try
            {
                WorkbookStorageSession session = Globals.ThisAddIn.GetStorageSession(workbook);
                if (!session.TryGetLink(rectId, out LinkedRectangle rect))
                    return false;

                int trackIndex = rect.LinkedCell.TrackIndex;
                IList<LinkedRectangle> allLinks = session.GetLinks();
                var siblings = allLinks
                    .Where(r => !string.Equals(r.Id, rectId, StringComparison.Ordinal)
                             && r.LinkedCell.TrackIndex == trackIndex)
                    .ToList();

                if (deleteCellData && rect.LinkType == LinkType.Sum)
                {
                    if (siblings.Count > 0)
                        return DeleteSumRectPartial(rectId, rect, siblings, workbook, session);
                }

                // When another rectangle still owns the same tracked cell, unlinking this
                // rectangle must leave the shared binding and styling intact.
                if (!deleteCellData && siblings.Count > 0)
                    return session.RemoveLink(rectId);

                Excel.Range cell = LinkCellResolver.TryResolveCell(workbook, rect);

                if (cell != null)
                {
                    if (deleteCellData)
                        ClearLinkedData(cell, rect);
                    try { CellFormattingService.ClearLinkStyle(cell); }
                    catch (COMException ex)
                    {
                        System.Diagnostics.Debug.WriteLine(
                            $"[Talliark] DeleteLink ClearLinkStyle failed: {ex.Message}");
                    }
                }

                LinkCellTracker.UnbindCell(workbook, cell, trackIndex);
                return session.RemoveLink(rectId);
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine(
                    $"[Talliark] DeleteLinkService.DeleteLink failed: {ex.Message}");
                return false;
            }
        }

        private static bool DeleteSumRectPartial(
            string rectId,
            LinkedRectangle rect,
            IList<LinkedRectangle> siblings,
            Excel.Workbook workbook,
            WorkbookStorageSession session)
        {
            try
            {
                var remainingTexts = siblings.Select(r => r.SourceText).ToList();
                string formula = TextValueFormatter.RebuildSumFormula(remainingTexts);

                Excel.Range cell = LinkCellResolver.TryResolveCell(workbook, rect);
                if (cell != null)
                {
                    if (formula != null)
                    {
                        CellFormattingService.ApplySumNumberFormat(cell, remainingTexts);
                        cell.Formula = formula;
                        cell.Calculate();
                    }
                    else
                    {
                        ClearCellContents(cell);
                    }
                }

                return session.RemoveLink(rectId);
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine(
                    $"[Talliark] DeleteSumRectPartial failed: {ex.Message}");
                return false;
            }
        }

        private static void ClearLinkedData(Excel.Range cell, LinkedRectangle rect)
        {
            if (rect.LinkType == LinkType.Table && rect.TableGrid != null)
            {
                new TableExcelWriteService().Clear(cell, rect.TableGrid);
                return;
            }

            ClearCellContents(cell);
        }

        private static void ClearCellContents(Excel.Range cell)
        {
            Excel.Application app = cell.Application as Excel.Application;
            bool previousEvents = app?.EnableEvents ?? true;
            try
            {
                if (app != null) app.EnableEvents = false;
                cell.ClearContents();
            }
            finally
            {
                if (app != null) app.EnableEvents = previousEvents;
            }
        }

        public IList<string> DeleteLinksInSelection(
            Excel.Range selection,
            Excel.Workbook workbook,
            bool deleteCellData = false)
        {
            if (selection == null || workbook == null)
                return Array.Empty<string>();

            WorkbookProtectionGuard.ThrowIfStructureProtected(workbook);

            WorkbookStorageSession session = Globals.ThisAddIn.GetStorageSession(workbook);
            IList<LinkedRectangle> links = session.GetLinks();
            var idsToDelete = new HashSet<string>(StringComparer.Ordinal);
            var orphanBindings = new List<(Excel.Range cell, int trackIndex)>();
            var byTrackIndex = new Dictionary<int, List<LinkedRectangle>>();

            foreach (LinkedRectangle link in links)
            {
                int trackIndex = link?.LinkedCell?.TrackIndex ?? 0;
                if (trackIndex <= 0) continue;
                if (!byTrackIndex.TryGetValue(trackIndex, out List<LinkedRectangle> forCell))
                {
                    forCell = new List<LinkedRectangle>();
                    byTrackIndex[trackIndex] = forCell;
                }
                forCell.Add(link);
            }

            // Resolve the track names once for the complete range. The old path walked
            // every destination cell, and every one-cell lookup scanned the bindings again;
            // multi-page table footprints made that cost grow explosively.
            foreach (LinkCellTracker.TrackedCell tracked in
                     LinkCellTracker.FindTrackedCellsInRange(selection))
            {
                if (byTrackIndex.TryGetValue(tracked.TrackIndex, out List<LinkedRectangle> forCell))
                {
                    foreach (LinkedRectangle link in forCell)
                        idsToDelete.Add(link.Id);
                }
                else
                {
                    orphanBindings.Add((tracked.Cell, tracked.TrackIndex));
                }
            }

            if (idsToDelete.Count == 0 && orphanBindings.Count == 0)
                return Array.Empty<string>();

            using (Globals.ThisAddIn.EnterSelectionNavSuppress())
            {
                return DeleteLinksByIdCore(
                    workbook,
                    session,
                    links,
                    idsToDelete,
                    orphanBindings,
                    clearTableCellData: deleteCellData,
                    operationName: "DeleteLinksInSelection");
            }
        }

        public IList<string> DeleteLinksForPdf(string pdfId, Excel.Workbook workbook)
        {
            if (string.IsNullOrWhiteSpace(pdfId) || workbook == null)
                return Array.Empty<string>();

            WorkbookProtectionGuard.ThrowIfStructureProtected(workbook);

            WorkbookStorageSession session = Globals.ThisAddIn.GetStorageSession(workbook);
            IList<LinkedRectangle> links = session.GetLinks();
            var idsToDelete = new HashSet<string>(
                links
                    .Where(r => string.Equals(r.PdfId, pdfId, StringComparison.Ordinal))
                    .Select(r => r.Id),
                StringComparer.Ordinal);

            if (idsToDelete.Count == 0)
                return Array.Empty<string>();

            using (Globals.ThisAddIn.EnterSelectionNavSuppress())
            {
                return DeleteLinksByIdCore(
                    workbook,
                    session,
                    links,
                    idsToDelete,
                    new List<(Excel.Range cell, int trackIndex)>(),
                    clearTableCellData: false,
                    operationName: "DeleteLinksForPdf");
            }
        }

        private static IList<string> DeleteLinksByIdCore(
            Excel.Workbook workbook,
            WorkbookStorageSession session,
            IList<LinkedRectangle> links,
            HashSet<string> idsToDelete,
            IList<(Excel.Range cell, int trackIndex)> orphanBindings,
            bool clearTableCellData,
            string operationName)
        {
            var cellsToClear = new List<Excel.Range>();
            var unbindTargets = new List<(Excel.Range cell, int trackIndex)>();
            var deletedIds = new List<string>();

            foreach (string id in idsToDelete)
            {
                LinkedRectangle rect = links.FirstOrDefault(
                    r => string.Equals(r.Id, id, StringComparison.Ordinal));
                if (rect == null)
                    continue;

                Excel.Range cell = LinkCellResolver.TryResolveCell(workbook, rect);
                if (cell != null)
                {
                    cellsToClear.Add(cell);
                    if (clearTableCellData && rect.LinkType == LinkType.Table)
                        new TableExcelWriteService().Clear(cell, rect.TableGrid);
                }

                unbindTargets.Add((cell, rect.LinkedCell.TrackIndex));
                deletedIds.Add(id);
            }

            if (deletedIds.Count == 0 && (orphanBindings == null || orphanBindings.Count == 0))
                return Array.Empty<string>();

            if (orphanBindings != null)
            {
                foreach ((Excel.Range cell, int _) in orphanBindings)
                    if (cell != null)
                        cellsToClear.Add(cell);
            }

            Excel.Application app = Globals.ThisAddIn.Application;
            bool prevEnableEvents = app?.EnableEvents ?? true;

            try
            {
                if (app != null)
                    app.EnableEvents = false;

                try
                {
                    CellFormattingService.ClearLinkStyles(cellsToClear, app);
                }
                catch (COMException ex)
                {
                    System.Diagnostics.Debug.WriteLine(
                        $"[Talliark] {operationName} batch clear failed: {ex.Message}");
                }

                foreach ((Excel.Range cell, int trackIndex) in unbindTargets)
                {
                    try { LinkCellTracker.UnbindCell(workbook, cell, trackIndex); }
                    catch (COMException ex)
                    {
                        System.Diagnostics.Debug.WriteLine(
                            $"[Talliark] {operationName} UnbindCell failed: {ex.Message}");
                    }
                }

                if (orphanBindings != null)
                {
                    foreach ((Excel.Range cell, int trackIndex) in orphanBindings)
                    {
                        try { LinkCellTracker.UnbindCell(workbook, cell, trackIndex); }
                        catch (COMException ex)
                        {
                            System.Diagnostics.Debug.WriteLine(
                                $"[Talliark] {operationName} orphan UnbindCell failed: {ex.Message}");
                        }
                    }
                }
            }
            finally
            {
                if (app != null)
                    app.EnableEvents = prevEnableEvents;
            }

            if (deletedIds.Count == 0)
                return Array.Empty<string>();

            var remaining = links
                .Where(r => !idsToDelete.Contains(r.Id))
                .ToList();

            if (remaining.Count == links.Count)
                return Array.Empty<string>();

            session.SetLinks(remaining);
            return deletedIds;
        }
    }
}
