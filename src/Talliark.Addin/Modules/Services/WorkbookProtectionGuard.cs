using System;
using System.Runtime.InteropServices;
using System.Windows.Forms;
using Excel = Microsoft.Office.Interop.Excel;

namespace Talliark.Addin.Modules.Services
{
    internal static class WorkbookProtectionGuard
    {
        internal const string ProtectedStructureMessage =
            "This workbook's structure is protected. Unprotect workbook structure before changing Talliark data.";

        internal static bool IsStructureProtected(Excel.Workbook workbook)
        {
            if (workbook == null) return false;

            try
            {
                return workbook.ProtectStructure;
            }
            catch (COMException ex)
            {
                System.Diagnostics.Debug.WriteLine(
                    $"[Talliark] Failed to read Workbook.ProtectStructure: {ex.Message}");
                return false;
            }
        }

        internal static void ThrowIfStructureProtected(Excel.Workbook workbook)
        {
            if (IsStructureProtected(workbook))
                throw new InvalidOperationException(ProtectedStructureMessage);
        }

        internal static bool TryRequireWritable(Excel.Workbook workbook, IWin32Window owner = null)
        {
            if (!IsStructureProtected(workbook))
                return true;

            ShowProtectedStructureMessage(owner);
            return false;
        }

        internal static void ShowProtectedStructureMessage(IWin32Window owner = null)
        {
            if (owner == null)
            {
                MessageBox.Show(
                    ProtectedStructureMessage,
                    "Talliark",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Information);
                return;
            }

            MessageBox.Show(
                owner,
                ProtectedStructureMessage,
                "Talliark",
                MessageBoxButtons.OK,
                MessageBoxIcon.Information);
        }

        internal static string BuildProtectedSheetMessage(string sheetName)
        {
            string subject = string.IsNullOrEmpty(sheetName)
                ? "This worksheet is"
                : $"The worksheet '{sheetName}' is";

            return $"{subject} protected. Unprotect the sheet before creating a Talliark link on it.";
        }

        internal static void ShowProtectedSheetMessage(IWin32Window owner, string sheetName)
        {
            string message = BuildProtectedSheetMessage(sheetName);

            if (owner == null)
            {
                MessageBox.Show(
                    message,
                    "Talliark",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Information);
                return;
            }

            MessageBox.Show(
                owner,
                message,
                "Talliark",
                MessageBoxButtons.OK,
                MessageBoxIcon.Information);
        }

        /// <summary>
        /// True when Excel will refuse a write to <paramref name="range"/> because its
        /// worksheet protects contents against the object model and the range is not fully
        /// unlocked.
        /// </summary>
        /// <remarks>
        /// Verified against Excel: a sheet protected with UserInterfaceOnly accepts COM
        /// writes (<c>ProtectionMode</c> is true), and an unlocked cell accepts a write on
        /// any protected sheet. Only contents protection without object-model permission on
        /// a locked range is refused. <c>Range.Locked</c> returns <see cref="DBNull"/> for a
        /// mixed range, which counts as blocked: the write would only partly land.
        /// </remarks>
        internal static bool IsWriteBlocked(Excel.Range range)
        {
            if (range == null) return false;

            try
            {
                var sheet = range.Worksheet as Excel.Worksheet;
                if (sheet == null || !sheet.ProtectContents || sheet.ProtectionMode)
                    return false;

                object locked = range.Locked;
                return !(locked is bool isLocked && !isLocked);
            }
            catch (COMException ex)
            {
                // An unreadable protection state is not proof of a refusal; let the write
                // itself decide and report its own failure.
                System.Diagnostics.Debug.WriteLine(
                    $"[Talliark] Failed to read sheet protection: {ex.Message}");
                return false;
            }
        }

        internal static string GetSheetName(Excel.Range range)
        {
            try { return (range?.Worksheet as Excel.Worksheet)?.Name; }
            catch (COMException) { return null; }
        }

        /// <summary>
        /// Thrown when a write is refused because the target worksheet is protected. Callers
        /// catch this to explain the refusal rather than report a generic failure.
        /// </summary>
        internal sealed class ProtectedSheetException : InvalidOperationException
        {
            internal ProtectedSheetException(string sheetName)
                : base(BuildProtectedSheetMessage(sheetName))
            {
                SheetName = sheetName;
            }

            internal string SheetName { get; }
        }
    }
}
