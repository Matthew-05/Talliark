using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using Excel = Microsoft.Office.Interop.Excel;

{
    /// <summary>
    /// Remembers the cell the user last selected in each workbook, so a link created from
    /// the viewer targets the cell the user chose rather than whichever sheet Excel reports
    /// as active when the message is processed.
    /// </summary>
    /// <remarks>
    /// <see cref="Excel.Application.Selection"/> answers for the active window, and a
    /// workbook with more than one window keeps an active sheet per window. Activating the
    /// window that hosts the pane therefore changes that answer without raising
    /// <c>Application.SheetSelectionChange</c>, and reading the live selection at message
    /// time wrote the link onto the pane window's sheet — typically the workbook's first
    /// tab — while the user's last real selection was on a later one. The recorded sheet
    /// name and addresses are resolved against the owning workbook when they are needed; a
    /// sheet that was renamed or deleted since simply fails to resolve and the caller falls
    /// back.
    /// <para>
    /// Events alone are not enough to keep the record aligned with what the user sees:
    /// Excel raises no selection event for a select that does not change the selection, and
    /// a sheet activation restores a remembered selection the same way. Navigation the
    /// viewer drives itself therefore records its target explicitly, and
    /// <c>ThisAddIn.Application_SheetActivate</c> records activations.
    /// </para>
    /// <para>
    /// Window activation is deliberately not recorded. Clicking the task pane activates the
    /// window that hosts it, and that is precisely the transition that must not overwrite
    /// the user's last real selection — the failure this tracker exists to prevent.
    /// </para>
    /// </remarks>
    internal sealed class SelectionTargetTracker
    {
        private sealed class Snapshot
        {
            public string SheetName { get; set; }

            public string SelectionAddress { get; set; }

            public string ActiveCellAddress { get; set; }
        }

        private readonly Dictionary<string, Snapshot> _byWorkbook =
            new Dictionary<string, Snapshot>(StringComparer.OrdinalIgnoreCase);

        internal int Count => _byWorkbook.Count;

        /// <summary>
        /// Records <paramref name="selection"/> as the workbook's current link target.
        /// Failures are swallowed: remembering a target is a convenience, never a reason to
        /// fail the selection change that triggered it.
        /// </summary>
        internal void Note(
            string workbookKey,
            Excel.Worksheet sheet,
            Excel.Range selection,
            string activeCellAddress)
        {
            if (string.IsNullOrEmpty(workbookKey) || sheet == null || selection == null)
                return;

            try
            {
                string sheetName = sheet.Name;
                string address = selection.Address;
                if (string.IsNullOrEmpty(sheetName) || string.IsNullOrEmpty(address))
                    return;

                _byWorkbook[workbookKey] = new Snapshot
                {
                    SheetName = sheetName,
                    SelectionAddress = address,
                    ActiveCellAddress = string.IsNullOrEmpty(activeCellAddress)
                        ? null
                        : activeCellAddress,
                };
            }
            catch (COMException)
            {
                // The sheet went away between the event and this read. The previous snapshot
                // remains the best answer, so leave it in place.
            }
        }

        /// <summary>
        /// Resolves the recorded selection against <paramref name="workbook"/>. Returns
        /// <c>false</c> when nothing was recorded, or when the sheet or address no longer
        /// resolves — callers treat that as "no recorded target" and fall back.
        /// </summary>
        internal bool TryResolve(
            Excel.Workbook workbook,
            string workbookKey,
            out Excel.Range selection,
            out Excel.Range activeCell)
        {
            selection = null;
            activeCell = null;
            if (workbook == null || string.IsNullOrEmpty(workbookKey)) return false;
            if (!_byWorkbook.TryGetValue(workbookKey, out Snapshot snapshot)) return false;

            Excel.Worksheet sheet = FindWorksheet(workbook, snapshot.SheetName);
            if (sheet == null) return false;

            // A hidden sheet cannot be the cell the user is looking at, and the add-in's own
            // formula tracker is very hidden — never let a stale record target one.
            try
            {
                if (sheet.Visible != Excel.XlSheetVisibility.xlSheetVisible) return false;
            }
            catch (COMException)
            {
                return false;
            }

            try
            {
                selection = sheet.Range[snapshot.SelectionAddress] as Excel.Range;
                if (selection == null) return false;

                // Resolved against the same sheet, so an active cell address that belonged
                // to another sheet cannot point the caller at a foreign workbook.
                if (snapshot.ActiveCellAddress != null)
                    activeCell = sheet.Range[snapshot.ActiveCellAddress] as Excel.Range;
            }
            catch (COMException)
            {
                selection = null;
                activeCell = null;
                return false;
            }

            return true;
        }

        /// <summary>
        /// Drops every record whose workbook key is not in <paramref name="liveKeys"/>.
        /// Selection changes are recorded for every workbook the user touches, including ones
        /// with no storage session, so cleanup cannot ride on the storage-session sweep.
        /// </summary>
        internal void PruneTo(ICollection<string> liveKeys)
        {
            if (liveKeys == null) return;

            var stale = new List<string>();
            foreach (string key in _byWorkbook.Keys)
            {
                if (!liveKeys.Contains(key))
                    stale.Add(key);
            }

            foreach (string key in stale)
                _byWorkbook.Remove(key);
        }

        internal void Clear() => _byWorkbook.Clear();

        private static Excel.Worksheet FindWorksheet(Excel.Workbook workbook, string sheetName)
        {
            if (string.IsNullOrEmpty(sheetName)) return null;

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
    }
}
