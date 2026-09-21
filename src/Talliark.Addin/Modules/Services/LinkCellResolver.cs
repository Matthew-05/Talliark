using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using Talliark.Addin.Modules.CustomXml.Models;
using Excel = Microsoft.Office.Interop.Excel;

namespace Talliark.Addin.Modules.Services
{
    /// <summary>
    /// Resolves the Excel cell bound to a persisted <see cref="LinkedRectangle"/>.
    /// </summary>
    internal static class LinkCellResolver
    {
        /// <summary>A persisted link paired with the Excel cell currently bound to it.</summary>
        internal sealed class SelectedLink
        {
            internal SelectedLink(LinkedRectangle rectangle, Excel.Range cell)
            {
                Rectangle = rectangle;
                Cell = cell;
            }

            internal LinkedRectangle Rectangle { get; }

            internal Excel.Range Cell { get; }
        }

        /// <summary>
        /// Returns every link in <paramref name="links"/> whose tracked cell lies inside
        /// <paramref name="selection"/>, ordered by cell (top-to-bottom then left-to-right)
        /// and, within a cell, in stored order. A Sum cell shares one
        /// <see cref="LinkedCell"/> across its contributing rectangles, so it yields one
        /// entry per rectangle. Resolves the whole selection in a single pass over the
        /// workbook's track names.
        /// </summary>
        internal static IList<SelectedLink> ResolveLinksInSelection(
            IList<LinkedRectangle> links,
            Excel.Range selection)
        {
            var result = new List<SelectedLink>();
            if (links == null || links.Count == 0 || selection == null)
                return result;

            var byTrackIndex = new Dictionary<int, List<LinkedRectangle>>();
            foreach (LinkedRectangle link in links)
            {
                if (link?.LinkedCell == null || link.LinkedCell.TrackIndex <= 0)
                    continue;

                if (!byTrackIndex.TryGetValue(link.LinkedCell.TrackIndex, out List<LinkedRectangle> forCell))
                {
                    forCell = new List<LinkedRectangle>();
                    byTrackIndex[link.LinkedCell.TrackIndex] = forCell;
                }

                forCell.Add(link);
            }

            foreach (LinkCellTracker.TrackedCell tracked in
                     LinkCellTracker.FindTrackedCellsInRange(selection))
            {
                if (!byTrackIndex.TryGetValue(tracked.TrackIndex, out List<LinkedRectangle> forCell))
                    continue;

                foreach (LinkedRectangle link in forCell)
                    result.Add(new SelectedLink(link, tracked.Cell));
            }

            return result;
        }

        internal static Excel.Range TryResolveCell(Excel.Workbook workbook, LinkedRectangle rect)
        {
            // PRIMARY: Resolve the formula reference Excel maintains through structural moves.
            Excel.Range cell = LinkCellTracker.TryResolveCell(
                workbook,
                rect.LinkedCell.TrackIndex,
                out bool bindingExists);
            if (cell != null)
                return cell;

            // A tracker with a broken reference means the linked cell was deleted. Do not
            // silently attach the link to whatever now occupies its last persisted address.
            if (bindingExists)
                return null;

            // FALLBACK: Use stored sheet name + address (for backward compatibility with old workbooks)
            try
            {
                Excel.Worksheet ws = FindWorksheet(workbook, rect.LinkedCell.SheetName);
                if (ws != null)
                {
                    cell = ws.Range[rect.LinkedCell.Address] as Excel.Range;
                    if (cell != null)
                        return cell;
                }
            }
            catch (COMException) { }

            return null;
        }

        internal static Excel.Range TryResolveCellViaTracker(Excel.Workbook workbook, int trackIndex)
        {
            return LinkCellTracker.TryResolveCell(workbook, trackIndex, out _);
        }

        private static Excel.Worksheet FindWorksheet(Excel.Workbook workbook, string sheetName)
        {
            foreach (Excel.Worksheet ws in workbook.Worksheets)
            {
                if (string.Equals(ws.Name, sheetName, StringComparison.OrdinalIgnoreCase))
                    return ws;
            }
            return null;
        }
    }
}
