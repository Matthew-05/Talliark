using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Talliark.Addin;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Threading.Tasks;
using System.Text;
using System.Windows.Forms;
using Talliark.Addin.Modules.Services;
using Talliark.Addin.Modules.Services.Conversion;
using Talliark.Addin.Modules.UI;
using Excel = Microsoft.Office.Interop.Excel;
using Microsoft.Office.Core;

namespace Talliark.Addin.Ribbon
{
    [ComVisible(true)]
    public class TalliarkRibbon : IRibbonExtensibility
    {
        private IRibbonUI _ribbonUi;
        public string GetCustomUI(string ribbonID)
        {
            try
            {
                var xml = LoadRibbonXmlFromResources();
                Modules.TalliarkLog.Trace($"GetCustomUI OK — ribbonID={ribbonID}, xmlLen={xml?.Length}");
                return xml;
            }
            catch (Exception ex)
            {
                // Returning null costs the ribbon; rethrowing lets Office treat the add-in as
                // failed to load and disable it. The log is the diagnostic.
                Modules.TalliarkLog.Trace($"GetCustomUI EXCEPTION: {ex}");
                return null;
            }
        }

        public void OnShowTaskPane(IRibbonControl control)
        {
            Globals.ThisAddIn.ShowTaskPane();
        }

        public void OnShowViewerWindow(IRibbonControl control)
        {
            Globals.ThisAddIn.ShowViewerWindow();
        }

        public bool GetAutoOpenViewerOnCellClick(IRibbonControl control)
        {
            return Globals.ThisAddIn.AutoOpenViewerOnCellClick;
        }

        public string GetAutoOpenViewerLabel(IRibbonControl control)
        {
            return Globals.ThisAddIn.AutoOpenViewerOnCellClick
                ? "Auto-open Viewer (enabled)"
                : "Auto-open Viewer (disabled)";
        }

        public void OnToggleAutoOpenViewerOnCellClick(IRibbonControl control, bool pressed)
        {
            Globals.ThisAddIn.AutoOpenViewerOnCellClick = pressed;
            _ribbonUi?.InvalidateControl(control.Id);
        }

        public void OnManageFiles(IRibbonControl control)
        {
            Globals.ThisAddIn.ShowManageFilesWindow();
        }

        public void OnOpenReconcile(IRibbonControl control)
        {
            Globals.ThisAddIn.ShowReconcileWindow();
        }

        public void OnLinkDocuments(IRibbonControl control)
        {
            Globals.ThisAddIn.ShowDocumentLinkerWindow();
        }

        public void OnOpenSettings(IRibbonControl control)
        {
            using (var dialog = new SettingsDialog())
                dialog.ShowDialog();
        }

        public void OnDeleteLinksInSelection(IRibbonControl control)
        {
            var app = Globals.ThisAddIn.Application;
            if (app?.ActiveWorkbook == null)
            {
                MessageBox.Show(
                    text: "Open a workbook before deleting links.",
                    caption: "Talliark",
                    buttons: MessageBoxButtons.OK,
                    icon: MessageBoxIcon.Information);
                return;
            }

            if (!WorkbookProtectionGuard.TryRequireWritable(app.ActiveWorkbook))
                return;

            var selection = app.Selection as Excel.Range;
            if (selection == null)
            {
                MessageBox.Show(
                    text: "Select one or more cells first.",
                    caption: "Talliark",
                    buttons: MessageBoxButtons.OK,
                    icon: MessageBoxIcon.Information);
                return;
            }

            IList<string> deletedIds;
            using (Globals.ThisAddIn.EnterSelectionNavSuppress())
            {
                bool prevEnableEvents = app.EnableEvents;
                try
                {
                    app.EnableEvents = false;

                    deletedIds = new DeleteLinkService().DeleteLinksInSelection(
                        selection,
                        app.ActiveWorkbook,
                        deleteCellData: false);

                    if (deletedIds.Count > 0)
                        Globals.ThisAddIn.GetActiveViewerHost()?.SendLinkRectanglesRemoved(deletedIds);
                }
                finally
                {
                    app.EnableEvents = prevEnableEvents;
                }
            }

            if (deletedIds.Count == 0)
            {
                MessageBox.Show(
                    text: "No linked cells in the selected range.",
                    caption: "Talliark",
                    buttons: MessageBoxButtons.OK,
                    icon: MessageBoxIcon.Information);
            }
        }

        /// <summary>
        /// Ribbon entry point for adding documents. Async because non-PDF sources may
        /// need conversion, which drives an offscreen WebView2 and therefore has to
        /// keep the UI message pump running.
        /// </summary>
        public async void OnAddPdfDocuments(IRibbonControl control)
        {
            try
            {
                Excel.Workbook workbook = GetWritableWorkbook();
                if (workbook == null)
                    return;

                string[] selectedPaths;
                using (var dialog = new OpenFileDialog())
                {
                    dialog.Title = "Add documents to workbook";
                    dialog.Filter = ConversionFormatCatalog.BuildOpenFileDialogFilter();
                    dialog.Multiselect = true;
                    dialog.CheckFileExists = true;

                    if (dialog.ShowDialog() != DialogResult.OK
                        || dialog.FileNames == null
                        || dialog.FileNames.Length == 0)
                        return;

                    selectedPaths = dialog.FileNames.ToArray();
                }

                await ImportDocumentPathsAsync(workbook, selectedPaths);
            }
            catch (Exception ex)
            {
                ShowImportFailure("OnAddPdfDocuments", ex);
            }
        }

        /// <summary>Adds files, folders or a picture copied to the Windows clipboard.</summary>
        public async void OnImportDocumentsFromClipboard(IRibbonControl control)
        {
            try
            {
                Excel.Workbook workbook = GetWritableWorkbook();
                if (workbook == null)
                    return;

                // Explorer publishes a file-drop list. A screenshot, a browser's
                // "Copy image" and Office's "Copy as picture" publish pixels instead,
                // which the drop list never reports.
                if (Clipboard.ContainsFileDropList())
                {
                    string[] clipboardPaths = Clipboard.GetFileDropList()
                        .Cast<string>()
                        .ToArray();

                    if (clipboardPaths.Length > 0)
                    {
                        await ImportDocumentPathsAsync(workbook, clipboardPaths);
                        return;
                    }
                }

                IList<ImportCandidate> pictureCandidates = CollectClipboardPictureCandidates();
                if (pictureCandidates.Count == 0)
                {
                    MessageBox.Show(
                        text: "Copy one or more files, folders, or a picture, then try again.",
                        caption: "Talliark",
                        buttons: MessageBoxButtons.OK,
                        icon: MessageBoxIcon.Information);
                    return;
                }

                await ImportCandidatesAsync(workbook, pictureCandidates);
            }
            catch (Exception ex)
            {
                ShowImportFailure("OnImportDocumentsFromClipboard", ex);
            }
        }

        /// <summary>
        /// Turns a picture on the clipboard into an import candidate, or returns an
        /// empty list when there is none to read.
        ///
        /// A screenshot, a browser's "Copy image" and Office's "Copy as picture" all
        /// hand over their pixels, and <see cref="Clipboard.GetFileDropList"/> sees
        /// nothing of them. The picture is re-encoded as PNG — the one raster format
        /// every source can produce and the one the image converter reads — and named
        /// from the clock, so a second paste is a second document rather than a repeat
        /// of the first. Transparent pixels are left alone: the conversion engine
        /// already flattens them onto white, which is the right answer for a pasted
        /// screenshot and cheaper to do once.
        /// </summary>
        private static IList<ImportCandidate> CollectClipboardPictureCandidates()
        {
            try
            {
                if (!Clipboard.ContainsImage())
                    return Array.Empty<ImportCandidate>();

                using (System.Drawing.Image picture = Clipboard.GetImage())
                {
                    if (picture == null || picture.Width <= 0 || picture.Height <= 0)
                        return Array.Empty<ImportCandidate>();

                    byte[] png;
                    using (var buffer = new MemoryStream())
                    {
                        picture.Save(buffer, System.Drawing.Imaging.ImageFormat.Png);
                        png = buffer.ToArray();
                    }

                    Modules.TalliarkLog.Trace(
                        $"Clipboard picture: {picture.Width}x{picture.Height} {picture.PixelFormat} " +
                        $"→ {png.Length} PNG bytes.");

                    return new[]
                    {
                        new ImportCandidate
                        {
                            Name = $"Pasted image {DateTime.Now:yyyy-MM-dd HH-mm-ss}.png",
                            Bytes = png,
                        },
                    };
                }
            }
            catch (ExternalException ex)
            {
                // Another process is holding the clipboard open. Nothing to import
                // is a far better outcome than an error dialog over a picture that
                // is still there.
                Modules.TalliarkLog.Trace($"Clipboard picture could not be read: {ex}");
                return Array.Empty<ImportCandidate>();
            }
        }

        /// <summary>Adds every supported document in a user-selected directory tree.</summary>
        public async void OnImportDocumentFolder(IRibbonControl control)
        {
            try
            {
                Excel.Workbook workbook = GetWritableWorkbook();
                if (workbook == null)
                    return;

                string selectedPath = null;
                Microsoft.Office.Core.FileDialog dialog = null;
                FileDialogSelectedItems selectedItems = null;
                try
                {
                    dialog = Globals.ThisAddIn.Application.FileDialog[
                        MsoFileDialogType.msoFileDialogFolderPicker];
                    dialog.Title = "Choose a folder of documents to add";
                    dialog.ButtonName = "Import";
                    dialog.AllowMultiSelect = false;

                    if (dialog.Show() != -1)
                        return;

                    selectedItems = dialog.SelectedItems;
                    if (selectedItems.Count > 0)
                        selectedPath = selectedItems.Item(1);
                }
                finally
                {
                    if (selectedItems != null && Marshal.IsComObject(selectedItems))
                        Marshal.FinalReleaseComObject(selectedItems);
                    if (dialog != null && Marshal.IsComObject(dialog))
                        Marshal.FinalReleaseComObject(dialog);
                }

                if (string.IsNullOrWhiteSpace(selectedPath))
                    return;

                string folderName = new DirectoryInfo(selectedPath).Name;
                if (string.IsNullOrWhiteSpace(folderName))
                    folderName = selectedPath.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);

                await ImportDocumentPathsAsync(
                    workbook,
                    new[] { selectedPath },
                    folderName);
            }
            catch (Exception ex)
            {
                ShowImportFailure("OnImportDocumentFolder", ex);
            }
        }

        private static Excel.Workbook GetWritableWorkbook()
        {
            var app = Globals.ThisAddIn.Application;
            if (app?.ActiveWorkbook == null)
            {
                MessageBox.Show(
                    text: "Open or create a workbook before adding documents.",
                    caption: "Talliark",
                    buttons: MessageBoxButtons.OK,
                    icon: MessageBoxIcon.Information);
                return null;
            }

            return WorkbookProtectionGuard.TryRequireWritable(app.ActiveWorkbook)
                ? app.ActiveWorkbook
                : null;
        }

        private static async Task ImportDocumentPathsAsync(
            Excel.Workbook workbook,
            IEnumerable<string> selectedPaths,
            string importedFolderName = null)
        {
            var candidates = new List<ImportCandidate>();
            foreach (string selectedPath in selectedPaths ?? Enumerable.Empty<string>())
            {
                if (string.IsNullOrWhiteSpace(selectedPath))
                    continue;

                if (Directory.Exists(selectedPath))
                {
                    candidates.AddRange(ImportPathCollector.CollectDirectory(selectedPath));
                    continue;
                }

                if (File.Exists(selectedPath))
                {
                    candidates.Add(new ImportCandidate
                    {
                        Path = selectedPath,
                        Name = Path.GetFileName(selectedPath),
                    });
                }
            }

            await ImportCandidatesAsync(workbook, candidates, importedFolderName);
        }

        /// <summary>
        /// Confirms, converts and embeds a selection of candidates, whether each one
        /// arrived as a path on disk or as bytes. The path-based callers
        /// (<see cref="ImportDocumentPathsAsync"/>) and the clipboard picture
        /// (<see cref="CollectClipboardPictureCandidates"/>) both arrive here, so
        /// neither can drift into its own idea of what confirming an import means.
        /// </summary>
        private static async Task ImportCandidatesAsync(
            Excel.Workbook workbook,
            IList<ImportCandidate> candidates,
            string importedFolderName = null)
        {
            if (candidates == null || candidates.Count == 0)
            {
                MessageBox.Show(
                    text: "No supported documents were found.",
                    caption: "Talliark",
                    buttons: MessageBoxButtons.OK,
                    icon: MessageBoxIcon.Information);
                return;
            }

            // Confirm conversions before anything is read or written.
            ImportSelectionPlan plan = ImportPreparationService.Plan(null, candidates);
            if (plan.Cancelled || plan.IsEmpty)
                return;

            bool createsFolderGroup = !string.IsNullOrWhiteSpace(importedFolderName);
            if (createsFolderGroup)
            {
                string folderId = new ManageFilesService().AddFolder(workbook, importedFolderName);
                foreach (ImportCandidate candidate in plan.PdfCandidates.Concat(plan.ConvertCandidates))
                    candidate.FolderId = folderId;
            }

            PdfImportResult result;
            IList<string> preparationErrors;

            using (var progress = ThreadedProgressController.Show("Importing documents..."))
            using (PreparedImport prepared = await ImportPreparationService.PrepareAsync(plan, progress))
            {
                preparationErrors = prepared.Errors;
                result = new PdfImportService().ImportFilePaths(
                    workbook, prepared.PathRequests, progress);

                if (result.AddedIds.Count > 0)
                {
                    progress.Report(
                        "Refreshing Talliark",
                        "Updating viewer data...",
                        result.AddedIds.Count,
                        result.AddedIds.Count);

                    foreach (string id in result.AddedIds)
                        Globals.ThisAddIn.NotifyViewerPdfAdded(workbook, id);

                    if (createsFolderGroup)
                        Globals.ThisAddIn.NotifyViewerFoldersChanged(workbook);
                }
            }

            ShowImportSummary(result, preparationErrors);
        }

        private static void ShowImportFailure(string operation, Exception ex)
        {
            Modules.TalliarkLog.Trace($"{operation} failed: {ex}");
            MessageBox.Show(
                $"Documents could not be imported.{Environment.NewLine}{Environment.NewLine}{ex.Message}",
                "Talliark",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
        }

        /// <summary>
        /// Reports skipped and failed files once the import finishes. Conversion
        /// problems and unsupported types are surfaced together so the user sees a
        /// single account of everything that did not make it in.
        /// </summary>
        private static void ShowImportSummary(PdfImportResult result, IList<string> preparationErrors)
        {
            var problems = new List<string>();
            if (preparationErrors != null) problems.AddRange(preparationErrors);
            if (result?.Errors != null) problems.AddRange(result.Errors);

            if (problems.Count == 0)
                return;

            var message = new StringBuilder();
            int added = result?.AddedIds.Count ?? 0;
            if (added > 0)
                message.AppendLine($"Added {added} document(s).").AppendLine();

            message.AppendLine("Some files were not added:");
            message.AppendLine(string.Join(Environment.NewLine, problems));

            MessageBox.Show(message.ToString(), "Talliark", MessageBoxButtons.OK, MessageBoxIcon.Warning);
        }

        private static string LoadRibbonXmlFromResources()
        {
            Assembly assembly = typeof(TalliarkRibbon).Assembly;
            foreach (string name in assembly.GetManifestResourceNames())
            {
                if (name.EndsWith("TalliarkRibbon.xml", StringComparison.Ordinal))
                {
                    using (var stream = assembly.GetManifestResourceStream(name))
                    {
                        if (stream == null)
                            break;
                        using (var reader = new System.IO.StreamReader(stream))
                            return reader.ReadToEnd();
                    }
                }
            }

            throw new InvalidOperationException("Embedded resource TalliarkRibbon.xml was not found.");
        }

        /// <summary>Called when the Ribbon extensibility loads; retained for optional IRibbonUI caching.</summary>
        public void Ribbon_Load(IRibbonUI ribbonUi)
        {
            _ribbonUi = ribbonUi;
        }

        public System.Drawing.Bitmap GetViewerMenuImage(IRibbonControl control)
        {
            return LoadEmbeddedSvgAsIcon("icon-viewer.svg");
        }

        public System.Drawing.Bitmap GetTaskPaneImage(IRibbonControl control)
        {
            return LoadEmbeddedSvgAsIcon("icon-viewer-task-pane.svg");
        }

        public System.Drawing.Bitmap GetViewerWindowImage(IRibbonControl control)
        {
            return LoadEmbeddedSvgAsIcon("icon-viewer-window.svg");
        }

        public System.Drawing.Bitmap GetAutoOpenViewerImage(IRibbonControl control)
        {
            return LoadEmbeddedSvgAsIcon("icon-viewer-auto-open.svg");
        }

        public System.Drawing.Bitmap GetAddPdfImage(IRibbonControl control)
        {
            return LoadEmbeddedSvgAsIcon("icon-add-document.svg");
        }

        public System.Drawing.Bitmap GetAddFilesImage(IRibbonControl control)
        {
            return LoadEmbeddedSvgAsIcon("icon-add-files.svg");
        }

        public System.Drawing.Bitmap GetImportClipboardImage(IRibbonControl control)
        {
            return LoadEmbeddedSvgAsIcon("icon-import-clipboard.svg");
        }

        public System.Drawing.Bitmap GetImportFolderImage(IRibbonControl control)
        {
            return LoadEmbeddedSvgAsIcon("icon-import-folder.svg");
        }

        public System.Drawing.Bitmap GetDeleteLinksImage(IRibbonControl control)
        {
            return LoadEmbeddedSvgAsIcon("icon-delete-links.svg");
        }

        public System.Drawing.Bitmap GetManageFilesImage(IRibbonControl control)
        {
            return LoadEmbeddedSvgAsIcon("icon-manage-files.svg");
        }

        public System.Drawing.Bitmap GetReconcileImage(IRibbonControl control)
        {
            return LoadEmbeddedSvgAsIcon("icon-reconcile.svg");
        }

        public System.Drawing.Bitmap GetLinkDocumentsImage(IRibbonControl control)
        {
            return LoadEmbeddedSvgAsIcon("icon-link-documents.svg");
        }

        public System.Drawing.Bitmap GetSettingsImage(IRibbonControl control)
        {
            return LoadEmbeddedSvgAsIcon("icon-settings.svg");
        }

        private static System.Drawing.Bitmap LoadEmbeddedSvgAsIcon(string iconName)
        {
            Assembly assembly = typeof(TalliarkRibbon).Assembly;

            string resourceName = assembly.GetManifestResourceNames()
                .FirstOrDefault(n => n.EndsWith(iconName, StringComparison.OrdinalIgnoreCase));

            if (resourceName != null)
            {
                using (var stream = assembly.GetManifestResourceStream(resourceName))
                {
                    if (stream != null)
                    {
                        using (var reader = new StreamReader(stream))
                        {
                            string svgText = reader.ReadToEnd();
                            return RenderSvgAsIcon(svgText);
                        }
                    }
                }
            }

            return CreatePlaceholderIcon();
        }

        private static System.Drawing.Bitmap RenderSvgAsIcon(string svgText)
        {
            var doc = Svg.SvgDocument.FromSvg<Svg.SvgDocument>(svgText);
            return doc.Draw(32, 32);
        }

        private static System.Drawing.Bitmap CreatePlaceholderIcon()
        {
            var bitmap = new System.Drawing.Bitmap(32, 32);
            using (var graphics = System.Drawing.Graphics.FromImage(bitmap))
            {
                graphics.Clear(System.Drawing.Color.Transparent);
                using (var pen = new System.Drawing.Pen(System.Drawing.Color.LightGray, 1))
                    graphics.DrawRectangle(pen, 2, 2, 28, 28);
            }
            return bitmap;
        }
    }
}
