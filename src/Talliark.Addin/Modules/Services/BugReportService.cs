using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Windows.Forms;
using Talliark.Addin.Modules.Infrastructure;
using Excel = Microsoft.Office.Interop.Excel;

namespace Talliark.Addin.Modules.Services
{
    internal static class BugReportTypes
    {
        internal const string General = "General";
        internal const string ExcelWorkbook = "Excel or workbook";
        internal const string DocumentImport = "Document import";
        internal const string ViewerLinking = "Viewer or linking";
        internal const string Ocr = "OCR";
        internal const string Reconcile = "Reconcile";
        internal const string Performance = "Performance";
        internal const string Other = "Other";

        internal const string ReconcileCreation = "Reconcile creation";
        internal const string AutoFoot = "Auto foot";

        internal static readonly string[] All =
        {
            General,
            ExcelWorkbook,
            DocumentImport,
            ViewerLinking,
            Ocr,
            Reconcile,
            Performance,
            Other
        };

        internal static readonly string[] ReconcileSubtypes =
        {
            ReconcileCreation,
            AutoFoot
        };
    }

    internal sealed class BugReportDraft
    {
        internal string BugType { get; set; }
        internal string Description { get; set; }
        internal bool IncludeWorkbook { get; set; } = true;
        internal bool IncludeLog { get; set; } = true;
        internal string ReconcileSubtype { get; set; } = BugReportTypes.ReconcileCreation;
        internal IList<string> AdditionalFiles { get; } = new List<string>();
        internal IList<string> OriginalFilePaths { get; } = new List<string>();
    }

    internal enum BugReportPreparationResult
    {
        Ready,
        ReturnToReport
    }

    internal sealed class PreparedBugReport
    {
        internal string Subject { get; set; }
        internal string Body { get; set; }
        internal IList<string> Attachments { get; } = new List<string>();
    }

    /// <summary>
    /// Performs the consent and save checks between the report form and the
    /// default mail client. Nothing is transmitted here: the user receives an
    /// ordinary editable draft and chooses whether to send it.
    /// </summary>
    internal static class BugReportService
    {
        private const long LargeAttachmentWarningThresholdBytes = 20L * 1024L * 1024L;

        internal static BugReportPreparationResult Prepare(
            IWin32Window owner,
            BugReportDraft draft,
            Excel.Workbook workbook,
            out PreparedBugReport report)
        {
            report = null;

            IList<string> additionalFiles;
            IList<string> originalFilePaths;
            if (!TryResolveSelectedFiles(
                owner,
                draft,
                out additionalFiles,
                out originalFilePaths))
            {
                return BugReportPreparationResult.ReturnToReport;
            }

            if (draft.IncludeWorkbook
                || draft.IncludeLog
                || additionalFiles.Count > 0
                || originalFilePaths.Count > 0)
            {
                DialogResult consent = MessageBox.Show(
                    owner,
                    "The selected files may contain confidential or sensitive information. "
                    + "They will be attached to an email draft and transmitted only if you send it."
                    + Environment.NewLine + Environment.NewLine
                    + "Continue?",
                    "Talliark Bug Report",
                    MessageBoxButtons.OKCancel,
                    MessageBoxIcon.Warning,
                    MessageBoxDefaultButton.Button2);

                if (consent != DialogResult.OK)
                    return BugReportPreparationResult.ReturnToReport;
            }

            string workbookPath = null;
            if (draft.IncludeWorkbook)
            {
                if (workbook == null)
                {
                    draft.IncludeWorkbook = false;
                }
                else if (!TryGetSavedWorkbookPath(owner, workbook, out workbookPath))
                {
                    DialogResult exclude = MessageBox.Show(
                        owner,
                        "An unsaved workbook cannot be included. Exclude the workbook and continue?",
                        "Talliark Bug Report",
                        MessageBoxButtons.YesNo,
                        MessageBoxIcon.Information,
                        MessageBoxDefaultButton.Button1);

                    if (exclude == DialogResult.Yes)
                        draft.IncludeWorkbook = false;
                    else
                        return BugReportPreparationResult.ReturnToReport;
                }
            }

            TalliarkLog.Trace("Preparing bug-report email draft.");
            string logPath = draft.IncludeLog && File.Exists(TalliarkLog.CurrentFilePath)
                ? TalliarkLog.CurrentFilePath
                : null;

            report = BuildReport(
                draft,
                workbookPath,
                logPath,
                additionalFiles,
                originalFilePaths);

            if (!ConfirmLargeAttachments(owner, report.Attachments))
            {
                report = null;
                return BugReportPreparationResult.ReturnToReport;
            }

            return BugReportPreparationResult.Ready;
        }

        internal static bool OpenEmail(IWin32Window owner, PreparedBugReport report)
        {
            DefaultMailComposer.Result result = DefaultMailComposer.ShowDraft(
                BuildConfiguration.BugReportEmail,
                report.Subject,
                report.Body,
                report.Attachments);

            if (!result.Opened)
            {
                MessageBox.Show(
                    owner,
                    result.Error ?? "The default email app could not be opened.",
                    "Talliark Bug Report",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error);
                return false;
            }

            if (result.AttachmentsNeedManualAddition)
            {
                MessageBox.Show(
                    owner,
                    "Your email app opened the draft but did not accept automatic attachments. "
                    + "Please attach the selected file or files manually before sending."
                    + Environment.NewLine + Environment.NewLine
                    + string.Join(Environment.NewLine, report.Attachments),
                    "Talliark Bug Report",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Information);
            }

            return true;
        }

        private static bool TryGetSavedWorkbookPath(
            IWin32Window owner,
            Excel.Workbook workbook,
            out string workbookPath)
        {
            workbookPath = GetExistingWorkbookPath(workbook);
            bool needsSave;
            try
            {
                needsSave = !workbook.Saved || workbookPath == null;
            }
            catch (Exception ex)
            {
                TalliarkLog.Trace($"Could not inspect workbook save state: {ex}");
                return false;
            }

            if (!needsSave)
                return true;

            DialogResult save = MessageBox.Show(
                owner,
                "Save the active workbook now so its current contents can be attached?",
                "Talliark Bug Report",
                MessageBoxButtons.YesNo,
                MessageBoxIcon.Question,
                MessageBoxDefaultButton.Button1);

            if (save != DialogResult.Yes)
                return false;

            try
            {
                // For a new workbook Excel presents its normal Save As dialog. A user
                // cancellation leaves Saved false (and may surface as a COM error),
                // which follows the same exclude-or-return path as choosing No above.
                workbook.Save();
                workbookPath = GetExistingWorkbookPath(workbook);
                return workbook.Saved && workbookPath != null;
            }
            catch (Exception ex)
            {
                TalliarkLog.Trace($"Workbook save for bug report did not complete: {ex.Message}");
                workbookPath = null;
                return false;
            }
        }

        private static string GetExistingWorkbookPath(Excel.Workbook workbook)
        {
            try
            {
                string path = workbook.FullName;
                return !string.IsNullOrWhiteSpace(path) && File.Exists(path)
                    ? Path.GetFullPath(path)
                    : null;
            }
            catch
            {
                return null;
            }
        }

        private static PreparedBugReport BuildReport(
            BugReportDraft draft,
            string workbookPath,
            string logPath,
            IList<string> additionalFiles,
            IList<string> originalFilePaths)
        {
            string subjectDetail = draft.BugType == BugReportTypes.Reconcile
                ? draft.BugType + " - " + draft.ReconcileSubtype
                : draft.BugType;
            var report = new PreparedBugReport
            {
                Subject = "Talliark bug report: " + subjectDetail
            };

            if (draft.IncludeWorkbook && workbookPath != null)
                AddAttachment(report, workbookPath);
            if (draft.IncludeLog && logPath != null)
                AddAttachment(report, logPath);
            foreach (string path in additionalFiles)
                AddAttachment(report, path);
            foreach (string path in originalFilePaths)
                AddAttachment(report, path);

            string excelVersion = "Unknown";
            try
            {
                excelVersion = Globals.ThisAddIn?.Application?.Version ?? "Unknown";
            }
            catch { }

            var body = new StringBuilder();
            body.AppendLine("Bug type: " + draft.BugType);
            if (draft.BugType == BugReportTypes.Reconcile)
                body.AppendLine("Reconcile subtype: " + draft.ReconcileSubtype);
            body.AppendLine("Talliark version: " + AppVersion.Current);
            body.AppendLine("Excel version: " + excelVersion);
            body.AppendLine("Workbook attached: " + (draft.IncludeWorkbook && workbookPath != null ? "Yes" : "No"));
            body.AppendLine("Log attached: " + (draft.IncludeLog && logPath != null ? "Yes" : "No"));
            body.AppendLine("Additional files attached: " + additionalFiles.Count);
            if (draft.BugType == BugReportTypes.Ocr)
                body.AppendLine("Original files attached: " + originalFilePaths.Count);
            body.AppendLine();
            body.AppendLine("Description:");
            body.AppendLine(draft.Description?.Trim() ?? string.Empty);
            report.Body = body.ToString();

            return report;
        }

        private static bool TryResolveSelectedFiles(
            IWin32Window owner,
            BugReportDraft draft,
            out IList<string> additionalFiles,
            out IList<string> originalFilePaths)
        {
            var missing = new List<string>();
            additionalFiles = ResolveExistingFiles(draft.AdditionalFiles, missing);
            originalFilePaths = draft.BugType == BugReportTypes.Ocr
                ? ResolveExistingFiles(draft.OriginalFilePaths, missing)
                : new List<string>();

            if (missing.Count == 0)
                return true;

            MessageBox.Show(
                owner,
                "One or more selected attachments can no longer be found. "
                + "Remove or replace them before continuing."
                + Environment.NewLine + Environment.NewLine
                + string.Join(Environment.NewLine, missing),
                "Talliark Bug Report",
                MessageBoxButtons.OK,
                MessageBoxIcon.Warning);
            return false;
        }

        private static IList<string> ResolveExistingFiles(
            IEnumerable<string> paths,
            IList<string> missing)
        {
            var resolved = new List<string>();
            var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (string selectedPath in paths ?? new string[0])
            {
                if (string.IsNullOrWhiteSpace(selectedPath)) continue;

                string fullPath;
                try { fullPath = Path.GetFullPath(selectedPath); }
                catch { fullPath = selectedPath; }

                if (!File.Exists(fullPath))
                {
                    missing.Add(selectedPath);
                    continue;
                }

                if (seen.Add(fullPath))
                    resolved.Add(fullPath);
            }
            return resolved;
        }

        private static void AddAttachment(PreparedBugReport report, string path)
        {
            foreach (string existing in report.Attachments)
            {
                if (string.Equals(existing, path, StringComparison.OrdinalIgnoreCase))
                    return;
            }
            report.Attachments.Add(path);
        }

        private static bool ConfirmLargeAttachments(
            IWin32Window owner,
            IEnumerable<string> attachmentPaths)
        {
            var largeFiles = new List<string>();
            foreach (string path in attachmentPaths ?? new string[0])
            {
                try
                {
                    long length = new FileInfo(path).Length;
                    if (length <= LargeAttachmentWarningThresholdBytes) continue;

                    double sizeInMegabytes = length / (1024d * 1024d);
                    largeFiles.Add(
                        Path.GetFileName(path) + " (" + sizeInMegabytes.ToString("N1") + " MB)");
                }
                catch (Exception ex)
                {
                    TalliarkLog.Trace($"Could not inspect bug-report attachment size for '{path}': {ex.Message}");
                }
            }

            if (largeFiles.Count == 0)
                return true;

            DialogResult result = MessageBox.Show(
                owner,
                "The following files are larger than 20 MB and may be too large for your "
                + "email provider to send:"
                + Environment.NewLine + Environment.NewLine
                + string.Join(Environment.NewLine, largeFiles)
                + Environment.NewLine + Environment.NewLine
                + "Open the email draft anyway?",
                "Talliark Bug Report",
                MessageBoxButtons.YesNo,
                MessageBoxIcon.Warning,
                MessageBoxDefaultButton.Button2);

            return result == DialogResult.Yes;
        }
    }
}
