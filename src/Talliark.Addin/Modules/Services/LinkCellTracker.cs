using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Runtime.InteropServices;
using Talliark.Addin.Modules;
using Talliark.Addin.Modules.CustomXml;
using Talliark.Addin.Modules.CustomXml.Models;
using Excel = Microsoft.Office.Interop.Excel;

namespace Talliark.Addin.Modules.Services
{
    /// <summary>
    /// Tracks linked cells through hidden workbook defined names whose formulas are
    /// direct references (<c>='Sheet'!$A$1</c>). Excel rewrites those references for
    /// cut/paste moves, inserted or deleted rows and columns, and worksheet renames.
    /// </summary>
    /// <remarks>
    /// The previous design kept the references on an <c>xlSheetVeryHidden</c> worksheet.
    /// Creating that worksheet during link insertion added a sheet to the workbook and had
    /// to activate another sheet to hide it again, which CCH Engagement's ePace add-in
    /// reacted to by switching the active tab. A defined name carries the same structural
    /// tracking with no sheet-set change and no activation, and a hidden name is absent
    /// from the Name Box and from formula autocomplete.
    /// </remarks>
    internal static class LinkCellTracker
    {
        private const string TrackNamePrefix = "TalliarkTrack_";
        private const int MaximumTrackIndex = 1048574;

        public static int NextTrackIndex(IEnumerable<LinkedRectangle> linkedRectangles)
        {
            if (linkedRectangles == null) throw new ArgumentNullException(nameof(linkedRectangles));

            int max = 0;
            foreach (LinkedRectangle rectangle in linkedRectangles)
                if (rectangle.LinkedCell.TrackIndex > max)
                    max = rectangle.LinkedCell.TrackIndex;

            return checked(max + 1);
        }

        public static void BindCell(Excel.Workbook workbook, Excel.Range cell, int trackIndex)
        {
            ValidateBindingArguments(workbook, cell, trackIndex);
            WorkbookProtectionGuard.ThrowIfStructureProtected(workbook);

            WriteTrackName(workbook, cell, trackIndex);
        }

        public static void UnbindCell(Excel.Workbook workbook, Excel.Range cell, int trackIndex)
        {
            if (trackIndex <= 0) return;
            if (workbook != null)
                WorkbookProtectionGuard.ThrowIfStructureProtected(workbook);
            if (workbook == null) return;

            DeleteTrackName(workbook, trackIndex);
        }

        /// <summary>
        /// Creates track names for persisted links that do not have one and removes names whose
        /// link is gone. Existing broken references are not rebuilt from their stored address
        /// because <c>#REF!</c> means the tracked cell was deleted.
        /// </summary>
        public static void EnsureBindings(
            Excel.Workbook workbook,
            IEnumerable<LinkedRectangle> linkedRectangles)
        {
            if (workbook == null) throw new ArgumentNullException(nameof(workbook));
            if (linkedRectangles == null) throw new ArgumentNullException(nameof(linkedRectangles));

            List<LinkedRectangle> distinctLinks = linkedRectangles
                .Where(link => link?.LinkedCell != null && link.LinkedCell.TrackIndex > 0)
                .GroupBy(link => link.LinkedCell.TrackIndex)
                .Select(group => group.First())
                .ToList();
            var liveTrackIndexes = new HashSet<int>(
                distinctLinks.Select(link => link.LinkedCell.TrackIndex));
            ISet<int> boundTrackIndexes = EnumerateTrackNameIndexes(workbook);

            bool hasMissingBindings = distinctLinks.Any(link =>
                !boundTrackIndexes.Contains(link.LinkedCell.TrackIndex));
            bool hasOrphanBindings = boundTrackIndexes.Any(index => !liveTrackIndexes.Contains(index));

            if (!hasMissingBindings && !hasOrphanBindings)
                return;

            WorkbookProtectionGuard.ThrowIfStructureProtected(workbook);

            foreach (LinkedRectangle link in distinctLinks)
            {
                int trackIndex = link.LinkedCell.TrackIndex;
                if (TrackNameExists(workbook, trackIndex))
                    continue;

                Excel.Range target = ResolveStoredCell(workbook, link.LinkedCell);
                if (target == null)
                    continue;

                WriteTrackName(workbook, target, trackIndex);
            }

            foreach (int orphanTrackIndex in EnumerateTrackNameIndexes(workbook)
                .Where(index => !liveTrackIndexes.Contains(index))
                .ToList())
            {
                DeleteTrackName(workbook, orphanTrackIndex);
            }
        }

        public sealed class TrackedCell
        {
            public TrackedCell(int trackIndex, Excel.Range cell)
            {
                TrackIndex = trackIndex;
                Cell = cell;
            }

            public int TrackIndex { get; }

            public Excel.Range Cell { get; }
        }

        public static IList<TrackedCell> FindTrackedCellsInRange(Excel.Range target)
        {
            var found = new List<TrackedCell>();
            if (target == null) return found;

            Excel.Workbook workbook;
            Excel.Application application;
            try
            {
                var worksheet = target.Worksheet as Excel.Worksheet;
                workbook = worksheet?.Parent as Excel.Workbook;
                application = workbook?.Application as Excel.Application;
                if (workbook == null || application == null) return found;
            }
            catch (COMException)
            {
                return found;
            }

            foreach (int trackIndex in EnumerateTrackNameIndexes(workbook))
            {
                Excel.Range trackedCell = TryResolveCell(workbook, trackIndex, out _);
                if (trackedCell == null) continue;

                try
                {
                    if (application.Intersect(trackedCell, target) == null) continue;
                }
                catch (COMException)
                {
                    continue;
                }

                found.Add(new TrackedCell(trackIndex, trackedCell));
            }

            found.Sort((left, right) =>
            {
                try
                {
                    int rowCompare = left.Cell.Row.CompareTo(right.Cell.Row);
                    return rowCompare != 0
                        ? rowCompare
                        : left.Cell.Column.CompareTo(right.Cell.Column);
                }
                catch (COMException)
                {
                    return 0;
                }
            });

            return found;
        }

        internal static Excel.Range TryResolveCell(
            Excel.Workbook workbook,
            int trackIndex,
            out bool bindingExists)
        {
            bindingExists = false;
            if (workbook == null || trackIndex <= 0 || trackIndex > MaximumTrackIndex)
                return null;

            Excel.Name trackName = FindTrackName(workbook, TrackNameFor(trackIndex));
            if (trackName == null) return null;

            string refersTo;
            try
            {
                refersTo = Convert.ToString(trackName.RefersTo, CultureInfo.InvariantCulture);
            }
            catch (COMException)
            {
                return null;
            }

            bindingExists = !string.IsNullOrWhiteSpace(refersTo);
            if (!bindingExists) return null;

            return ResolveReferenceFormula(workbook, refersTo);
        }

        /// <summary>
        /// Returns the track indexes whose names contain <c>#REF!</c>. This is deliberately
        /// narrower than "could not resolve": a missing name or a temporarily unavailable COM
        /// object is not proof that the user's cell was deleted and must never cost them a
        /// persisted rectangle.
        /// </summary>
        private static ISet<int> FindBrokenReferenceTrackIndexes(Excel.Workbook workbook)
        {
            var broken = new HashSet<int>();
            if (workbook == null) return broken;

            foreach (int trackIndex in EnumerateTrackNameIndexes(workbook))
            {
                Excel.Name trackName = FindTrackName(workbook, TrackNameFor(trackIndex));
                if (trackName == null) continue;

                try
                {
                    string refersTo = Convert.ToString(
                        trackName.RefersTo, CultureInfo.InvariantCulture);

                    if (refersTo != null
                        && refersTo.IndexOf("#REF!", StringComparison.OrdinalIgnoreCase) >= 0)
                        broken.Add(trackIndex);
                }
                catch (COMException)
                {
                    // An unreadable name is uncertain, not stale. A later save can retry.
                }
            }

            return broken;
        }

        /// <summary>
        /// Synchronizes persisted cell addresses and prunes rectangles whose name reference
        /// definitively became <c>#REF!</c> after a structural worksheet deletion.
        /// </summary>
        /// <returns>The ids of rectangles removed as stale.</returns>
        public static IList<string> SyncAllPositions(Excel.Workbook workbook)
        {
            if (workbook == null)
            {
                TalliarkLog.Trace("ENTER workbook=(null) - return");
                return Array.Empty<string>();
            }

            TalliarkLog.Trace($"ENTER workbook={GetWorkbookDebugName(workbook)}");

            var prunedIds = new List<string>();

            using (TalliarkLog.Time("SyncAllPositions total"))
            {
                WorkbookProtectionGuard.ThrowIfStructureProtected(workbook);

                WorkbookStorageSession session = Globals.ThisAddIn.GetStorageSession(workbook);
                IList<LinkedRectangle> links = session.GetLinks();
                EnsureBindings(workbook, links);
                ISet<int> brokenTrackIndexes = FindBrokenReferenceTrackIndexes(workbook);

                bool anyChanged = false;
                int scanned = 0;
                int missingBindings = 0;
                int brokenReferences = 0;
                int changed = 0;

                foreach (LinkedRectangle linkedRectangle in links)
                {
                    scanned++;
                    if (brokenTrackIndexes.Contains(linkedRectangle.LinkedCell.TrackIndex))
                    {
                        brokenReferences++;
                        prunedIds.Add(linkedRectangle.Id);
                        continue;
                    }

                    Excel.Range foundRange = TryResolveCell(
                        workbook,
                        linkedRectangle.LinkedCell.TrackIndex,
                        out _);
                    if (foundRange == null)
                    {
                        // A name that exists but cannot currently be resolved is not enough
                        // evidence to delete user data. Only the explicit #REF! set above prunes.
                        missingBindings++;
                        continue;
                    }

                    string newSheet = ((Excel.Worksheet)foundRange.Worksheet).Name;
                    string newAddress = foundRange.Address;
                    if (string.Equals(newSheet, linkedRectangle.LinkedCell.SheetName, StringComparison.Ordinal)
                        && string.Equals(newAddress, linkedRectangle.LinkedCell.Address, StringComparison.Ordinal))
                        continue;

                    linkedRectangle.LinkedCell.SheetName = newSheet;
                    linkedRectangle.LinkedCell.Address = newAddress;
                    anyChanged = true;
                    changed++;
                }

                TalliarkLog.Trace(
                    $"scan done scanned={scanned} changed={changed} "
                    + $"missingOrUnresolvedBindings={missingBindings} "
                    + $"prunedBrokenReferences={brokenReferences}");

                if (anyChanged || prunedIds.Count > 0)
                {
                    List<LinkedRectangle> remaining = links
                        .Where(link => !brokenTrackIndexes.Contains(link.LinkedCell.TrackIndex))
                        .ToList();
                    session.SetLinks(remaining);

                    if (prunedIds.Count > 0)
                    {
                        try
                        {
                            // The records are already safely persisted as removed. Name
                            // cleanup is best-effort and will be retried by EnsureBindings.
                            EnsureBindings(workbook, remaining);
                        }
                        catch (Exception ex)
                        {
                            TalliarkLog.Trace(
                                $"stale name cleanup deferred: {ex.GetType().FullName}: {ex.Message}");
                        }
                    }
                }
            }

            TalliarkLog.Trace("EXIT");
            return prunedIds;
        }

        private static void ValidateBindingArguments(
            Excel.Workbook workbook,
            Excel.Range cell,
            int trackIndex)
        {
            if (workbook == null) throw new ArgumentNullException(nameof(workbook));
            if (cell == null) throw new ArgumentNullException(nameof(cell));
            if (trackIndex <= 0 || trackIndex > MaximumTrackIndex)
                throw new ArgumentOutOfRangeException(nameof(trackIndex));
        }

        private static string TrackNameFor(int trackIndex)
        {
            return TrackNamePrefix + trackIndex.ToString(CultureInfo.InvariantCulture);
        }

        /// <summary>
        /// Writes the hidden name that binds <paramref name="target"/> to
        /// <paramref name="trackIndex"/>, replacing any name already carrying that index.
        /// </summary>
        private static void WriteTrackName(Excel.Workbook workbook, Excel.Range target, int trackIndex)
        {
            string name = TrackNameFor(trackIndex);
            Excel.Name existing = FindTrackName(workbook, name);
            if (existing != null)
            {
                try { existing.Delete(); }
                catch (COMException) { }
            }

            workbook.Names.Add(name, BuildReferenceFormula(target), false);
        }

        private static void DeleteTrackName(Excel.Workbook workbook, int trackIndex)
        {
            Excel.Name trackName = FindTrackName(workbook, TrackNameFor(trackIndex));
            if (trackName == null) return;

            try { trackName.Delete(); }
            catch (COMException) { }
        }

        private static bool TrackNameExists(Excel.Workbook workbook, int trackIndex)
        {
            return FindTrackName(workbook, TrackNameFor(trackIndex)) != null;
        }

        /// <summary>
        /// Finds the workbook-scoped track name called <paramref name="name"/>. The scope is
        /// verified because <c>Names.Item</c> can resolve a worksheet-scoped name that happens
        /// to share the name ahead of the workbook-scoped one.
        /// </summary>
        private static Excel.Name FindTrackName(Excel.Workbook workbook, string name)
        {
            try
            {
                Excel.Name direct = workbook.Names.Item(name);
                if (direct != null && direct.Parent is Excel.Workbook)
                    return direct;
            }
            catch (COMException)
            {
            }

            try
            {
                Excel.Names names = workbook.Names;
                if (names == null) return null;

                int count = names.Count;
                for (int index = 1; index <= count; index++)
                {
                    try
                    {
                        Excel.Name candidate = names.Item(index);
                        if (candidate != null
                            && candidate.Parent is Excel.Workbook
                            && string.Equals(candidate.Name, name, StringComparison.Ordinal))
                            return candidate;
                    }
                    catch (COMException)
                    {
                    }
                }
            }
            catch (COMException)
            {
            }

            return null;
        }

        /// <summary>
        /// Enumerates the track indexes currently bound by a hidden name. Sheet-scoped names
        /// are ignored: the tracker only ever creates workbook-scoped ones.
        /// </summary>
        private static ISet<int> EnumerateTrackNameIndexes(Excel.Workbook workbook)
        {
            var indexes = new HashSet<int>();
            if (workbook == null) return indexes;

            Excel.Names names;
            int count;
            try
            {
                names = workbook.Names;
                if (names == null) return indexes;
                count = names.Count;
            }
            catch (COMException)
            {
                return indexes;
            }

            for (int index = 1; index <= count; index++)
            {
                try
                {
                    Excel.Name name = names.Item(index);
                    if (name == null || !(name.Parent is Excel.Workbook)) continue;

                    string fullName = name.Name;
                    if (fullName == null
                        || !fullName.StartsWith(TrackNamePrefix, StringComparison.Ordinal))
                        continue;

                    if (!int.TryParse(
                            fullName.Substring(TrackNamePrefix.Length),
                            NumberStyles.None,
                            CultureInfo.InvariantCulture,
                            out int trackIndex))
                        continue;

                    if (trackIndex > 0 && trackIndex <= MaximumTrackIndex)
                        indexes.Add(trackIndex);
                }
                catch (COMException)
                {
                }
            }

            return indexes;
        }

        private static string BuildReferenceFormula(Excel.Range target)
        {
            string sheetName = ((Excel.Worksheet)target.Worksheet).Name;
            string escapedSheetName = sheetName.Replace("'", "''");
            return $"='{escapedSheetName}'!{target.Address}";
        }

        private static Excel.Range ResolveReferenceFormula(
            Excel.Workbook workbook,
            string formula)
        {
            if (string.IsNullOrWhiteSpace(formula)
                || !formula.StartsWith("=", StringComparison.Ordinal)
                || formula.IndexOf("#REF!", StringComparison.OrdinalIgnoreCase) >= 0)
                return null;

            string reference = formula.Substring(1).Trim();
            int separator = reference.LastIndexOf('!');
            if (separator <= 0 || separator >= reference.Length - 1)
                return null;

            string sheetToken = reference.Substring(0, separator).Trim();
            string address = reference.Substring(separator + 1).Trim();
            if (address.StartsWith("@", StringComparison.Ordinal))
                address = address.Substring(1);

            if (sheetToken.StartsWith("'", StringComparison.Ordinal)
                && sheetToken.EndsWith("'", StringComparison.Ordinal)
                && sheetToken.Length >= 2)
            {
                sheetToken = sheetToken.Substring(1, sheetToken.Length - 2)
                    .Replace("''", "'");
            }

            if (sheetToken.IndexOf("[", StringComparison.Ordinal) >= 0
                || sheetToken.IndexOf("]", StringComparison.Ordinal) >= 0)
                return null;

            Excel.Worksheet worksheet = FindWorksheet(workbook, sheetToken);
            if (worksheet == null) return null;

            try
            {
                return worksheet.Range[address] as Excel.Range;
            }
            catch (COMException)
            {
                return null;
            }
        }

        private static Excel.Range ResolveStoredCell(
            Excel.Workbook workbook,
            LinkedCell linkedCell)
        {
            if (linkedCell == null) return null;

            Excel.Worksheet worksheet = FindWorksheet(workbook, linkedCell.SheetName);
            if (worksheet == null) return null;

            try
            {
                return worksheet.Range[linkedCell.Address] as Excel.Range;
            }
            catch (COMException)
            {
                return null;
            }
        }

        private static Excel.Worksheet FindWorksheet(
            Excel.Workbook workbook,
            string sheetName)
        {
            if (string.IsNullOrWhiteSpace(sheetName)) return null;

            foreach (Excel.Worksheet worksheet in workbook.Worksheets)
            {
                try
                {
                    if (string.Equals(worksheet.Name, sheetName, StringComparison.OrdinalIgnoreCase))
                        return worksheet;
                }
                catch (COMException)
                {
                }
            }

            return null;
        }

        private static string GetWorkbookDebugName(Excel.Workbook workbook)
        {
            try
            {
                if (!string.IsNullOrEmpty(workbook.FullName))
                    return workbook.FullName;
            }
            catch (COMException ex)
            {
                TalliarkLog.Trace($"FullName unavailable: {ex.Message}");
            }

            try
            {
                return workbook.Name ?? "(unnamed)";
            }
            catch (COMException ex)
            {
                TalliarkLog.Trace($"Name unavailable: {ex.Message}");
                return "(workbook COM unavailable)";
            }
        }
    }
}
