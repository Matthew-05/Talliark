using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading.Tasks;
using System.Windows.Forms;
using Talliark.Addin.Modules;
using Talliark.Addin.Modules.CustomXml;
using Talliark.Addin.Modules.CustomXml.Models;
using Talliark.Addin.Modules.Services;
using Talliark.Addin.Modules.Services.Conversion;
using Talliark.Addin.Modules.UI;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;
using Excel = Microsoft.Office.Interop.Excel;

namespace Talliark.Addin.Modules.WebView
{
    /// <summary>
    /// Layout constants for native dropzone panel positioning and sizing.
    /// MUST match corresponding CSS values in src/web/apps/file-manager/src/styles/:
    /// - SidebarWidth (260) ↔ grid-template-columns: 260px 1fr (layout.css:5)
    /// - DropZoneHeight (122) ↔ .native-dropzone-spacer { height: 122px; } (folder-panel.css:5)
    /// - Gap (12) ↔ margin-top/bottom: 12px (folder-panel.css:6-7)
    /// </summary>
    internal static class DropzoneLayout
    {
        public const int SidebarWidth = 260;
        public const int Gap = 12;
        public const int DropZoneHeight = 122;
        public const int MinWidth = 160;
    }

    /// <summary>
    /// Hosts the file-manager web UI in a standalone non-modal window bound to one workbook.
    /// </summary>
    /// <remarks>
    /// Explorer → WebView2 file drops often do not surface HTML5 drop events inside Office/WinForms;
    /// Chromium may navigate to file:/// URLs instead. This host disables WebView2 external
    /// drops so WinForms can receive real <see cref="DataFormats.FileDrop"/> paths and imports
    /// them via <see cref="PdfImportService"/>. The navigation-cancellation handler (CoreWebView2_NavigationStarting)
    /// remains active to catch any file:/// URLs that slip through as an extra safeguard.
    /// </remarks>
    public sealed class FileManagerHost : Form
    {
        private readonly Excel.Workbook _workbook;
        private readonly WebView2 _webView = new WebView2();
        private WebViewStartupSurface _startup;
        private readonly NativeDropZonePanel _nativeDropZone = new NativeDropZonePanel();
        private readonly ManageFilesService _service = new ManageFilesService();
        private OcrService _ocrService;

        /// <summary>PDF IDs currently being processed by OCR. Populated before the await, cleared in finally.</summary>
        private readonly HashSet<string> _activeOcrIds = new HashSet<string>(StringComparer.Ordinal);

        /// <summary>The folder GUID currently selected in the web UI (<c>null</c> for All Files).</summary>
        private string _selectedFolderId;

        /// <summary>
        /// True for the whole OCR run. All file-add entry points (dropzone click, OS drag-drop,
        /// file:/// navigation fallback, web-originated add-files) are refused while set.
        /// Keyed off <see cref="_activeOcrIds"/> rather than <see cref="OcrService.IsRunning"/>,
        /// which only flips once the worker starts and so leaves the job-loading phase unguarded.
        /// </summary>
        private bool IsOcrLocked => _activeOcrIds.Count > 0;

        private bool _webViewReady;
        private bool _disposed;
        private Task _initTask;

        private readonly object _osImportLock = new object();
        private readonly Dictionary<string, long> _recentOsImportTicks =
            new Dictionary<string, long>(StringComparer.OrdinalIgnoreCase);

        public FileManagerHost(Excel.Workbook workbook)
        {
            _workbook = workbook ?? throw new ArgumentNullException(nameof(workbook));

            string workbookName;
            try
            {
                workbookName = workbook.Name;
            }
            catch
            {
                workbookName = null;
            }

            Text = string.IsNullOrWhiteSpace(workbookName)
                ? "Talliark – Manage Files"
                : $"Talliark – Manage Files – {workbookName}";
            // OcrService needs a Control reference for UI-thread marshalling. The handle is
            // not created yet at this point; warm-up realises it moments later, and nothing
            // marshals through it before then — an OCR run is only reachable from the shown
            // window.
            _ocrService = new OcrService(this);
            Width = 1100;
            Height = 620;
            MinimumSize = new System.Drawing.Size(700, 480);
            StartPosition = FormStartPosition.CenterScreen;
            AllowDrop = true;

            _webView.Dock = DockStyle.Fill;
            _webView.DragEnter += NativeFileDrop_DragEnter;
            _webView.DragOver += NativeFileDrop_DragEnter;
            _webView.DragDrop += NativeFileDrop_DragDrop;
            Controls.Add(_webView);

            _nativeDropZone.AllowDrop = true;
            _nativeDropZone.Click += (sender, args) => ShowPdfFilePicker();
            _nativeDropZone.DragEnter += NativeFileDrop_DragEnter;
            _nativeDropZone.DragOver += NativeFileDrop_DragEnter;
            _nativeDropZone.DragLeave += NativeFileDrop_DragLeave;
            _nativeDropZone.DragDrop += NativeFileDrop_DragDrop;
            Controls.Add(_nativeDropZone);

            // Last in, so it covers both the web view and the drop zone until the manager's
            // own UI is up. Nothing behind it is interactive while it is showing.
            _startup = new WebViewStartupSurface(this, _webView);

            DragEnter += NativeFileDrop_DragEnter;
            DragOver += NativeFileDrop_DragEnter;
            DragLeave += NativeFileDrop_DragLeave;
            DragDrop += NativeFileDrop_DragDrop;

            _initTask = InitAsync();
        }

        private async Task InitAsync()
        {
            TalliarkLog.Trace("ENTER file manager init");            try
            {
                if (_disposed) return;

                // One environment per process, shared by every host: they all point at the
                // same user data folder, and the runtime refuses a second environment over
                // one folder. Already warm by the time any window opens.
                var environment = await WebViewEagerLoader.GetEnvironmentAsync();
                if (_disposed) return;

                await _webView.EnsureCoreWebView2Async(environment);
                if (_disposed) return;

                WebViewContextMenu.Apply(_webView);
                _webView.AllowExternalDrop = false;

                string uiPath = GetWebUiPath();
                if (!Directory.Exists(uiPath))
                    throw new DirectoryNotFoundException(
                        $"Web UI folder not found: {uiPath}\n\nRun 'npm run build' in src/web to generate it.");

                // Reuse the same virtual host as TaskPaneHost; the mapping is per-process and
                // idempotent — setting it again with the same folder is harmless.
                _webView.CoreWebView2.SetVirtualHostNameToFolderMapping(
                    "talliark.local",
                    uiPath,
                    CoreWebView2HostResourceAccessKind.Allow);

                _webView.CoreWebView2.WebMessageReceived += OnWebMessageReceived;
                _webView.CoreWebView2.NavigationStarting += CoreWebView2_NavigationStarting;
                _webView.CoreWebView2.NavigationCompleted += CoreWebView2_NavigationCompleted;

                _webView.CoreWebView2.Navigate("https://talliark.local/file-manager/index.html");
                TalliarkLog.Trace("EXIT file manager initialized");
            }
            catch (Exception ex)
            {
                // Closing a workbook disposes its eagerly-created file manager. If WebView2
                // is still starting, disposal completes EnsureCoreWebView2Async with E_ABORT.
                // That cancellation is expected and belongs to a window that no longer exists;
                // reporting it as a load failure produces a spurious dialog while another
                // workbook is opening.
                if (_disposed)
                {
                    TalliarkLog.Trace(
                        $"CANCEL file manager init after dispose {ex.GetType().FullName}: {ex.Message}");
                    return;
                }

                TalliarkLog.Trace($"EXCEPTION file manager init {ex.GetType().FullName}: {ex.Message}");

                // In the window rather than a message box: this host is warmed invisibly on
                // workbook open, so a modal here can fire with no window on screen to own it.
                _startup?.ShowFailure(ex.Message);
            }
        }

        private void CoreWebView2_NavigationStarting(object sender, CoreWebView2NavigationStartingEventArgs e)
        {
            if (_disposed) return;

            if (string.IsNullOrEmpty(e.Uri))
                return;
            if (!e.Uri.StartsWith("file:", StringComparison.OrdinalIgnoreCase))
                return;

            e.Cancel = true;

            string localPath;
            try
            {
                localPath = new Uri(e.Uri).LocalPath;
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"[Talliark] file: navigation parse failed: {ex.Message}");
                return;
            }

            if (string.IsNullOrWhiteSpace(localPath))
                return;

            ProcessOsPaths(new[] { localPath });
        }

        private void CoreWebView2_NavigationCompleted(object sender, CoreWebView2NavigationCompletedEventArgs e)
        {
            if (_disposed) return;
            PositionNativeDropZone();
        }

        private void NativeFileDrop_DragEnter(object sender, DragEventArgs e)
        {
            if (_disposed) return;

            if (IsOcrLocked)
            {
                e.Effect = DragDropEffects.None;
                return;
            }

            if (GetDroppedPaths(e.Data).Length == 0)
            {
                e.Effect = DragDropEffects.None;
                return;
            }

            e.Effect = DragDropEffects.Copy;
            _nativeDropZone.SetDragOver(true);
        }

        private void NativeFileDrop_DragLeave(object sender, EventArgs e)
        {
            if (_disposed) return;
            _nativeDropZone.SetDragOver(false);
        }

        private void NativeFileDrop_DragDrop(object sender, DragEventArgs e)
        {
            if (_disposed || IsOcrLocked) return;

            _nativeDropZone.SetDragOver(false);

            string[] paths = GetDroppedPaths(e.Data);
            if (paths.Length == 0)
                return;

            BeginInvoke(new Action(() => ProcessOsPaths(paths)));
        }

        private static string[] GetDroppedPaths(IDataObject data)
        {
            if (data == null || !data.GetDataPresent(DataFormats.FileDrop))
                return Array.Empty<string>();

            return data.GetData(DataFormats.FileDrop) as string[] ?? Array.Empty<string>();
        }

        /// <summary>
        /// Enables/disables every OS-level file-add affordance while OCR runs: the native
        /// dropzone (click + drop) and form/WebView drop targets. The panel repaints itself
        /// in a muted "paused" state so the block is visible, not just silent.
        /// </summary>
        private void SetFileAddLocked(bool locked)
        {
            if (_disposed || IsDisposed) return;

            if (InvokeRequired)
            {
                BeginInvoke(new Action<bool>(SetFileAddLocked), locked);
                return;
            }

            AllowDrop = !locked;
            _nativeDropZone.AllowDrop = !locked;
            _nativeDropZone.SetLocked(locked);
        }

        private void PositionNativeDropZone()
        {
            if (_disposed || IsDisposed) return;

            int width = Math.Max(DropzoneLayout.MinWidth, Math.Min(DropzoneLayout.SidebarWidth - DropzoneLayout.Gap * 2, ClientSize.Width - DropzoneLayout.Gap * 2));
            width = Math.Min(width, Math.Max(0, ClientSize.Width - DropzoneLayout.Gap * 2));

            _nativeDropZone.SetBounds(
                DropzoneLayout.Gap,
                ClientSize.Height - DropzoneLayout.Gap - DropzoneLayout.DropZoneHeight,
                width,
                DropzoneLayout.DropZoneHeight);
            _nativeDropZone.BringToFront();
        }

        /// <summary>
        /// Imports documents (or folders of documents) dropped from the OS or chosen
        /// in the file picker. De-duplicates rapid double delivery (NavigationStarting
        /// + DragDrop). Non-PDF files are confirmed with the user and converted before
        /// anything is embedded.
        /// </summary>
        private async void ProcessOsPaths(string[] paths)
        {
            if (paths == null || paths.Length == 0)
                return;
            if (IsOcrLocked)
            {
                System.Diagnostics.Debug.WriteLine("[Talliark] OS drop ignored: OCR in progress.");
                return;
            }

            Excel.Workbook wb = _workbook;
            if (wb == null)
            {
                System.Diagnostics.Debug.WriteLine("[Talliark] OS drop ignored: owning workbook unavailable.");
                return;
            }
            if (!RequireWritable(wb))
                return;

            try
            {
                var folderIdCache = new Dictionary<string, string>(StringComparer.Ordinal);
                var candidates = new List<ImportCandidate>();

                foreach (string raw in paths)
                {
                    if (string.IsNullOrWhiteSpace(raw))
                        continue;

                    string path;
                    try
                    {
                        path = Path.GetFullPath(raw.Trim());
                    }
                    catch
                    {
                        continue;
                    }

                    if (ShouldSkipDuplicateOsImport(path))
                        continue;

                    try
                    {
                        FileAttributes attr = File.GetAttributes(path);
                        if ((attr & FileAttributes.Directory) == FileAttributes.Directory)
                            AddDirectoryCandidates(wb, path, folderIdCache, candidates);
                        else
                            candidates.Add(new ImportCandidate
                            {
                                Path = path,
                                Name = Path.GetFileName(path),
                                FolderId = _selectedFolderId,
                            });
                    }
                    catch (Exception ex)
                    {
                        System.Diagnostics.Debug.WriteLine($"[Talliark] OS import failed for '{path}': {ex.Message}");
                    }
                }

                // Ask before touching anything, then import what the user agreed to.
                ImportSelectionPlan plan = ImportPreparationService.Plan(this, candidates);
                if (plan.Cancelled || plan.IsEmpty)
                    return;

                await ImportPreparedAsync(wb, plan);
            }
            catch (Exception ex)
            {
                TalliarkLog.Trace($"ProcessOsPaths failed: {ex}");
                ShowImportFailure(ex);
            }
        }

        /// <summary>
        /// Collects every importable file in a dropped directory tree. All file types
        /// are collected here; the confirmation dialog is what filters them, so the
        /// counts it shows reflect what the folder actually contains.
        /// </summary>
        private void AddDirectoryCandidates(
            Excel.Workbook wb,
            string dirPath,
            Dictionary<string, string> folderIdCache,
            List<ImportCandidate> candidates)
        {
            string folderName;
            try
            {
                folderName = new DirectoryInfo(dirPath).Name;
            }
            catch
            {
                return;
            }

            string sentinel = "__new__:" + folderName;
            string folderId = ResolveFolderId(wb, sentinel, folderIdCache);

            foreach (ImportCandidate candidate in ImportPathCollector.CollectDirectory(dirPath, folderId))
            {
                if (!ShouldSkipDuplicateOsImport(candidate.Path))
                    candidates.Add(candidate);
            }
        }

        /// <summary>
        /// Converts, embeds and refreshes for an already-confirmed plan. Shared by the
        /// OS drop/picker path and the web dropzone path.
        /// </summary>
        private async Task ImportPreparedAsync(Excel.Workbook wb, ImportSelectionPlan plan)
        {
            var problems = new List<string>();
            int addedCount = 0;

            using (var progress = ThreadedProgressController.Show("Importing documents..."))
            using (PreparedImport prepared = await ImportPreparationService.PrepareAsync(plan, progress))
            {
                problems.AddRange(prepared.Errors);

                var addedIds = new List<string>();
                var importService = new PdfImportService();

                if (prepared.PathRequests.Count > 0)
                {
                    PdfImportResult pathResult = importService.ImportFilePaths(wb, prepared.PathRequests, progress);
                    addedIds.AddRange(pathResult.AddedIds);
                    problems.AddRange(pathResult.Errors);
                }

                if (prepared.Base64Requests.Count > 0)
                {
                    PdfImportResult base64Result = importService.ImportBase64(wb, prepared.Base64Requests, progress);
                    addedIds.AddRange(base64Result.AddedIds);
                    problems.AddRange(base64Result.Errors);
                }

                addedCount = addedIds.Count;

                if (addedCount > 0)
                {
                    progress.Report(
                        "Refreshing Talliark",
                        "Updating file list and viewer data...",
                        addedCount,
                        addedCount);

                    SendFilesToWebView();
                    foreach (string id in addedIds)
                        Globals.ThisAddIn.NotifyViewerPdfAdded(_workbook, id);

                    // An import can create folders on the fly, so refresh the viewer's
                    // folder filter after the new documents have landed.
                    Globals.ThisAddIn.NotifyViewerFoldersChanged(_workbook);
                }
            }

            ShowImportProblems(addedCount, problems);
        }

        /// <summary>Reports files that were skipped or failed, once the import has finished.</summary>
        private void ShowImportProblems(int addedCount, IList<string> problems)
        {
            if (problems == null || problems.Count == 0)
                return;

            var message = new System.Text.StringBuilder();
            if (addedCount > 0)
                message.AppendLine($"Added {addedCount} document(s).").AppendLine();

            message.AppendLine("Some files were not added:");
            message.AppendLine(string.Join(Environment.NewLine, problems));

            MessageBox.Show(this, message.ToString(), "Talliark", MessageBoxButtons.OK, MessageBoxIcon.Warning);
        }

        private void ShowImportFailure(Exception ex)
        {
            MessageBox.Show(
                this,
                $"Documents could not be imported.{Environment.NewLine}{Environment.NewLine}{ex.Message}",
                "Talliark",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
        }

        private bool ShouldSkipDuplicateOsImport(string fullPath)
        {
            lock (_osImportLock)
            {
                long now = DateTime.UtcNow.Ticks;
                const long window = 750 * TimeSpan.TicksPerMillisecond;

                if (_recentOsImportTicks.TryGetValue(fullPath, out long prev) && (now - prev) < window)
                    return true;

                _recentOsImportTicks[fullPath] = now;

                if (_recentOsImportTicks.Count > 96)
                {
                    long cutoff = now - 2 * TimeSpan.TicksPerSecond;
                    foreach (string key in _recentOsImportTicks.Where(kv => kv.Value < cutoff).Select(kv => kv.Key).ToList())
                        _recentOsImportTicks.Remove(key);
                }

                return false;
            }
        }

        private void OnWebMessageReceived(object sender, CoreWebView2WebMessageReceivedEventArgs e)
        {
            if (_disposed) return;

            try
            {
                string raw = e.TryGetWebMessageAsString();
                if (string.IsNullOrWhiteSpace(raw))
                    return;

                string type = FileManagerMessageParser.GetMessageType(raw);
                switch (type)
                {
                    case "manager-ready":
                        _startup?.Reveal();
                        _webViewReady = true;
                        SendFilesToWebView();
                        break;

                    case "set-selected-folder":
                        _selectedFolderId = FileManagerMessageParser.ParseSetSelectedFolder(raw);
                        break;

                    case "add-files":
                        HandleAddFiles(FileManagerMessageParser.ParseAddFiles(raw));
                        break;

                    case "browse-pdf-files":
                        ShowPdfFilePicker();
                        break;

                    case "rename-file":
                        HandleRenameFile(FileManagerMessageParser.ParseRenameFile(raw));
                        break;

                    case "remove-file":
                        HandleRemoveFile(FileManagerMessageParser.ParseRemoveFile(raw));
                        break;

                    case "select-file":
                        HandleSelectFile(FileManagerMessageParser.ParseSelectFile(raw));
                        break;

                    case "move-file":
                        HandleMoveFile(FileManagerMessageParser.ParseMoveFile(raw));
                        break;

                    case "add-folder":
                        HandleAddFolder(FileManagerMessageParser.ParseAddFolder(raw));
                        break;

                    case "rename-folder":
                        HandleRenameFolder(FileManagerMessageParser.ParseRenameFolder(raw));
                        break;

                    case "remove-folder":
                        HandleRemoveFolder(FileManagerMessageParser.ParseRemoveFolder(raw));
                        break;

                    case "ocr-pdfs":
                        _ = HandleOcrPdfsAsync(FileManagerMessageParser.ParseOcrPdfs(raw));
                        break;

                    case "cancel-ocr":
                        _ocrService.Cancel();
                        break;
                }
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"[Talliark] FileManagerHost.OnWebMessageReceived failed: {ex.Message}");
            }
        }

        private async System.Threading.Tasks.Task HandleOcrPdfsAsync(OcrPdfsRequest req)
        {
            if (req?.PdfIds == null || req.PdfIds.Count == 0) return;
            if (_ocrService.IsRunning) return;

            Excel.Workbook wb = _workbook;
            if (wb == null) return;
            if (!RequireWritable(wb)) return;

            foreach (string id in req.PdfIds)
                _activeOcrIds.Add(id);

            bool anyComplete = false;
            var pendingIds = new HashSet<string>(req.PdfIds, StringComparer.Ordinal);
            var errorMessages = new Dictionary<string, string>(StringComparer.Ordinal);

            SetFileAddLocked(true);

            try
            {
                await _ocrService.RunOcrAsync(
                    req.PdfIds,
                    wb,
                    onStatusUpdate: (pdfId, status, detail) =>
                    {
                        string json = FileManagerMessageSerializer.BuildOcrStatus(pdfId, status, detail);
                        PostToWebView(json);

                        if (status != "queued" && status != "processing")
                            pendingIds.Remove(pdfId);

                        if (status == "error")
                            errorMessages[pdfId] = detail?.Message ?? "OCR failed.";

                        if (status == PdfStatus.Ocr)
                        {
                            anyComplete = true;
                            Globals.ThisAddIn.RefreshTaskPanePdf(_workbook, pdfId);
                        }
                    });

                // A requested ID can disappear between the web selection and the
                // workbook read. The service has no job to report in that case, so
                // close the optimistic UI state here instead of leaving the manager
                // locked with no active status rows.
                foreach (string pdfId in pendingIds.ToArray())
                {
                    const string message =
                        "OCR could not process this document because it could not be loaded from the workbook.";
                    errorMessages[pdfId] = message;
                    PostToWebView(FileManagerMessageSerializer.BuildOcrStatus(
                        pdfId, "error", message));
                    pendingIds.Remove(pdfId);
                }
            }
            catch (Exception ex)
            {
                // RunOcrAsync reports normal worker/job failures through its callback.
                // An exception outside that path (for example runtime extraction) used
                // to leave every queued row spinning forever because this handler is
                // launched fire-and-forget. Give each unfinished row a terminal state.
                string message = "OCR could not continue: " + ex.Message;
                TalliarkLog.Trace($"HandleOcrPdfsAsync failed: {ex}");
                foreach (string pdfId in pendingIds.ToArray())
                {
                    errorMessages[pdfId] = message;
                    PostToWebView(FileManagerMessageSerializer.BuildOcrStatus(
                        pdfId, "error", message));
                    pendingIds.Remove(pdfId);
                }
            }
            finally
            {
                _activeOcrIds.Clear();
                SetFileAddLocked(false);
            }

            if (anyComplete)
            {
                SendFilesToWebView();

                // Refreshing metadata after successful files restores persisted status
                // for the whole table. Replay transient failures so mixed-result batches
                // still show which files need attention and why.
                foreach (KeyValuePair<string, string> error in errorMessages)
                {
                    PostToWebView(FileManagerMessageSerializer.BuildOcrStatus(
                        error.Key, "error", error.Value));
                }
            }
        }

        private async void HandleAddFiles(AddFilesRequest req)
        {
            if (IsOcrLocked) return;

            Excel.Workbook wb = _workbook;
            if (wb == null) return;
            if (!RequireWritable(wb)) return;

            try
            {
                // Cache resolved folder ids so that a dropped directory only creates one folder
                // even if it contains many files.
                var resolvedFolderIds = new Dictionary<string, string>(StringComparer.Ordinal);
                var candidates = new List<ImportCandidate>();

                foreach (var file in req.Files)
                {
                    try
                    {
                        string folderId = ResolveFolderId(wb, file.FolderId, resolvedFolderIds);
                        candidates.Add(new ImportCandidate
                        {
                            Name = file.Name,
                            Base64 = file.Base64,
                            FolderId = folderId,
                        });
                    }
                    catch (Exception ex)
                    {
                        System.Diagnostics.Debug.WriteLine($"[Talliark] AddPdf failed for '{file.Name}': {ex.Message}");
                    }
                }

                ImportSelectionPlan plan = ImportPreparationService.Plan(this, candidates);
                if (plan.Cancelled || plan.IsEmpty)
                    return;

                await ImportPreparedAsync(wb, plan);
            }
            catch (Exception ex)
            {
                TalliarkLog.Trace($"HandleAddFiles failed: {ex}");
                ShowImportFailure(ex);
            }
        }

        private void ShowPdfFilePicker()
        {
            if (IsOcrLocked) return;

            Excel.Workbook wb = _workbook;
            if (wb == null) return;
            if (!RequireWritable(wb)) return;

            string[] selectedPaths;
            using (var dialog = new OpenFileDialog())
            {
                dialog.Title = "Add documents to workbook";
                dialog.Filter = ConversionFormatCatalog.BuildOpenFileDialogFilter();
                dialog.Multiselect = true;
                dialog.CheckFileExists = true;

                if (dialog.ShowDialog(this) != DialogResult.OK || dialog.FileNames == null || dialog.FileNames.Length == 0)
                    return;

                selectedPaths = dialog.FileNames.ToArray();
            }

            BeginInvoke(new Action(() => ProcessOsPaths(selectedPaths)));
        }

        /// <summary>
        /// Resolves the folderId for a file. Handles the "__new__:FolderName" sentinel that
        /// the dropzone sends when a directory is dropped — the folder is created on first
        /// encounter and its new GUID is cached for subsequent files in the same batch.
        /// </summary>
        private string ResolveFolderId(
            Excel.Workbook wb,
            string folderId,
            Dictionary<string, string> cache)
        {
            if (string.IsNullOrEmpty(folderId))
                return null;

            const string newPrefix = "__new__:";
            if (!folderId.StartsWith(newPrefix, StringComparison.Ordinal))
                return folderId;

            if (cache.TryGetValue(folderId, out string cached))
                return cached;

            string folderName = folderId.Substring(newPrefix.Length);
            string newId = _service.AddFolder(wb, folderName);
            cache[folderId] = newId;
            return newId;
        }

        private void HandleRenameFile(RenameFileRequest req)
        {
            Excel.Workbook wb = _workbook;
            if (wb == null) return;
            if (!RequireWritable(wb)) return;

            var sw = System.Diagnostics.Stopwatch.StartNew();
            TalliarkContent content = _service.RenamePdf(wb, req.Id, req.NewName);
            System.Diagnostics.Debug.WriteLine($"[Talliark] RenamePdf: {sw.ElapsedMilliseconds}ms");

            sw.Restart();
            SendFilesToWebView(content);
            System.Diagnostics.Debug.WriteLine($"[Talliark] SendFilesToWebView: {sw.ElapsedMilliseconds}ms");

            sw.Restart();
            Globals.ThisAddIn.NotifyViewerPdfRenamed(_workbook, req.Id, req.NewName);
            System.Diagnostics.Debug.WriteLine($"[Talliark] NotifyViewerPdfRenamed: {sw.ElapsedMilliseconds}ms");
        }

        private void HandleRemoveFile(RemoveFileRequest req)
        {
            if (_activeOcrIds.Contains(req.Id)) return;
            Excel.Workbook wb = _workbook;
            if (wb == null) return;
            if (!RequireWritable(wb)) return;
            _service.RemovePdf(wb, req.Id);
            SendFilesToWebView();
            Globals.ThisAddIn.NotifyViewerPdfRemoved(_workbook, req.Id);
        }

        /// <summary>
        /// Swaps an open document-viewer to the file the user just selected.
        /// Read-only: no workbook mutation, so no writability check, and it is
        /// silently dropped when no viewer surface is open.
        /// </summary>
        private void HandleSelectFile(SelectFileRequest req)
        {
            if (string.IsNullOrWhiteSpace(req?.Id)) return;
            Globals.ThisAddIn.NotifyViewerShowPdf(_workbook, req.Id);
        }

        private void HandleMoveFile(MoveFileRequest req)
        {
            if (_activeOcrIds.Contains(req.Id)) return;
            Excel.Workbook wb = _workbook;
            if (wb == null) return;
            if (!RequireWritable(wb)) return;
            TalliarkContent content = _service.MoveFile(wb, req.Id, req.FolderId);
            SendFilesToWebView(content);
            Globals.ThisAddIn.NotifyViewerFoldersChanged(_workbook);
        }

        private void HandleAddFolder(AddFolderRequest req)
        {
            Excel.Workbook wb = _workbook;
            if (wb == null) return;
            if (!RequireWritable(wb)) return;
            _service.AddFolder(wb, req.Name);
            SendFilesToWebView();
            Globals.ThisAddIn.NotifyViewerFoldersChanged(_workbook);
        }

        private void HandleRenameFolder(RenameFolderRequest req)
        {
            Excel.Workbook wb = _workbook;
            if (wb == null) return;
            if (!RequireWritable(wb)) return;
            _service.RenameFolder(wb, req.Id, req.NewName);
            SendFilesToWebView();
            Globals.ThisAddIn.NotifyViewerFoldersChanged(_workbook);
        }

        private void HandleRemoveFolder(RemoveFolderRequest req)
        {
            Excel.Workbook wb = _workbook;
            if (wb == null) return;
            if (!RequireWritable(wb)) return;
            _service.RemoveFolder(wb, req.Id);
            SendFilesToWebView();
            Globals.ThisAddIn.NotifyViewerFoldersChanged(_workbook);
        }

        /// <summary>
        /// Pushes the owning workbook's file list to the web UI, but only if the web app
        /// has already signalled <c>manager-ready</c>. Call this whenever the window is
        /// shown after a warm-load so the web UI receives data unavailable at pre-init time.
        /// </summary>
        public void RefreshDataIfReady()
        {
            if (_disposed) return;
            if (!_webViewReady) return;
            SendFilesToWebView();
        }

        /// <summary>Reads the owning workbook's file list and pushes it to the web UI.</summary>
        public void SendFilesToWebView(TalliarkContent preloaded = null)
        {
            if (InvokeRequired)
            {
                if (!IsHandleCreated || IsDisposed) return;
                try
                {
                    BeginInvoke(new Action(() => SendFilesToWebView(preloaded)));
                }
                catch (InvalidOperationException ex)
                {
                    TalliarkLog.Trace($"SendFilesToWebView marshal failed: {ex.Message}");
                }
                return;
            }

            using (TalliarkLog.Time("SendFilesToWebView file manager"))
            {
            if (_disposed) return;
            try
            {
                TalliarkContent content = preloaded;
                if (content == null)
                {
                    var store = new TalliarkCustomXmlPartStore(_workbook);
                    content = store.LoadContent();
                    if (content == null)
                    {
                        System.Diagnostics.Debug.WriteLine("[Talliark] Failed to load workbook content");
                        return;
                    }
                }

                IReadOnlyDictionary<string, int> linkCounts = null;
                try
                {
                    var links = Globals.ThisAddIn.GetStorageSession(_workbook).GetLinks();
                    var counts = new Dictionary<string, int>(StringComparer.Ordinal);
                    foreach (var link in links)
                        counts[link.PdfId] = counts.TryGetValue(link.PdfId, out int n) ? n + 1 : 1;
                    linkCounts = counts;
                }
                catch { /* non-fatal; link counts default to 0 */ }

                string json = FileManagerMessageSerializer.BuildFilesLoaded(content.Folders, content.Pdfs, linkCounts);
                PostToWebView(json);
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"[Talliark] SendFilesToWebView failed: {ex.Message}");
                TalliarkLog.Trace($"EXCEPTION {ex.GetType().FullName}: {ex.Message}");
            }
            }
        }

        private bool RequireWritable(Excel.Workbook workbook)
        {
            return WorkbookProtectionGuard.TryRequireWritable(workbook, this);
        }

        private void PostToWebView(string json)
        {
            if (_disposed || string.IsNullOrEmpty(json)) return;

            if (InvokeRequired)
            {
                if (!IsHandleCreated || IsDisposed) return;
                try
                {
                    BeginInvoke(new Action(() => PostToWebView(json)));
                }
                catch (InvalidOperationException ex)
                {
                    TalliarkLog.Trace($"PostToWebView marshal failed: {ex.Message}");
                }
                return;
            }

            try
            {
                _webView.CoreWebView2?.PostWebMessageAsString(json);
            }
            catch (Exception ex)
            {
                TalliarkLog.Trace($"PostToWebView failed: {ex.GetType().FullName}: {ex.Message}");
            }
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            TalliarkLog.Trace($"ENTER file manager reason={e.CloseReason} cancel={e.Cancel} ocrLocked={IsOcrLocked}");
            if (e.CloseReason == CloseReason.UserClosing)
            {
                // OCR keeps running after the window hides, leaving the user with no
                // way to see progress or cancel. Make them make that choice explicitly.
                if (IsOcrLocked && !ConfirmCloseDuringOcr())
                {
                    e.Cancel = true;
                    TalliarkLog.Trace("EXIT file manager close declined during OCR");
                    return;
                }

                e.Cancel = true;
                SendResetUiToWebView();
                Hide();
                TalliarkLog.Trace("EXIT file manager user close hidden");
                return;
            }
            base.OnFormClosing(e);
            TalliarkLog.Trace($"EXIT file manager cancel={e.Cancel}");
        }

        /// <summary>
        /// Asks whether to abandon an in-progress OCR run. Returns true when the user
        /// confirms; the run is cancelled before returning so the window never hides
        /// with a worker still active.
        /// </summary>
        private bool ConfirmCloseDuringOcr()
        {
            DialogResult result = MessageBox.Show(
                this,
                "OCR is still running.\n\n" +
                "Closing this window will cancel the remaining files. " +
                "Files already processed keep their results.\n\n" +
                "Cancel OCR and close?",
                "Talliark – OCR in progress",
                MessageBoxButtons.YesNo,
                MessageBoxIcon.Warning,
                MessageBoxDefaultButton.Button2);

            if (result != DialogResult.Yes) return false;

            try
            {
                _ocrService.Cancel();
            }
            catch (Exception ex)
            {
                TalliarkLog.Trace($"OCR cancel on close failed: {ex.Message}");
            }

            return true;
        }

        protected override void OnFormClosed(FormClosedEventArgs e)
        {
            TalliarkLog.Trace($"ENTER file manager reason={e.CloseReason}");
            base.OnFormClosed(e);
            TalliarkLog.Trace("EXIT file manager");
        }

        protected override void Dispose(bool disposing)
        {
            TalliarkLog.Trace($"ENTER file manager disposing={disposing}");
            if (!_disposed)
            {
                _disposed = true;

                // Non-user closes (workbook shutdown, add-in teardown) bypass the
                // confirm prompt, so stop the worker here rather than leaving it
                // running against a workbook that is going away.
                try
                {
                    _ocrService?.Cancel();
                }
                catch (Exception ex)
                {
                    TalliarkLog.Trace($"file manager OCR cancel on dispose failed: {ex.Message}");
                }

                try
                {
                    if (_webView.CoreWebView2 != null)
                    {
                        _webView.CoreWebView2.WebMessageReceived -= OnWebMessageReceived;
                        _webView.CoreWebView2.NavigationStarting -= CoreWebView2_NavigationStarting;
                        _webView.CoreWebView2.NavigationCompleted -= CoreWebView2_NavigationCompleted;
                    }
                }
                catch (Exception ex)
                {
                    TalliarkLog.Trace($"file manager WebView event detach failed: {ex.Message}");
                }

                if (disposing)
                {
                    _webView.DragEnter -= NativeFileDrop_DragEnter;
                    _webView.DragOver -= NativeFileDrop_DragEnter;
                    _webView.DragDrop -= NativeFileDrop_DragDrop;

                    _nativeDropZone.DragEnter -= NativeFileDrop_DragEnter;
                    _nativeDropZone.DragOver -= NativeFileDrop_DragEnter;
                    _nativeDropZone.DragLeave -= NativeFileDrop_DragLeave;
                    _nativeDropZone.DragDrop -= NativeFileDrop_DragDrop;

                    DragEnter -= NativeFileDrop_DragEnter;
                    DragOver -= NativeFileDrop_DragEnter;
                    DragLeave -= NativeFileDrop_DragLeave;
                    DragDrop -= NativeFileDrop_DragDrop;

                    // Deferred while init is in flight: disposing a WebView2 mid-initialization
                    // is a native fail-fast in the runtime, and Excel blames the add-in for it.
                    WebViewDisposal.DisposeWhenInitialized(_webView, _initTask);

                    _startup?.Dispose();
                    _startup = null;
                }
            }
            base.Dispose(disposing);
            TalliarkLog.Trace("EXIT file manager");
        }

        protected override void OnResize(EventArgs e)
        {
            base.OnResize(e);
            PositionNativeDropZone();
        }

        private void SendResetUiToWebView()
        {
            if (_disposed) return;
            if (!_webViewReady) return;

            try
            {
                PostToWebView(HostMessageSerializer.BuildResetUi());
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine(
                    $"[Talliark] SendResetUiToWebView failed: {ex.Message}");
            }
        }

        private static string GetWebUiPath()
        {
            string codeBase = Assembly.GetExecutingAssembly().CodeBase;
            string addinDir = Path.GetDirectoryName(new Uri(codeBase).LocalPath)
                ?? AppDomain.CurrentDomain.BaseDirectory;
            return Path.Combine(addinDir, "webui");
        }

        /// <summary>
        /// Native Windows Forms panel for drag-drop file interaction.
        /// Color tokens MUST match src/web/shared/base.css design system:
        ///   - ForeColor (31,41,55) = --color-text-primary
        ///   - BackColor (255,255,255) = --color-surface
        ///   - fillColor drag-over (238,242,255) = --color-surface-light
        ///   - borderColor drag-over (124,106,247) = --color-accent
        ///   - borderColor rest (212,212,224) = --color-border
        ///   - mutedBrush (92,92,112) = --color-text-muted
        /// If design tokens change, both CSS and these hardcoded values must be updated together.
        /// </summary>
        private sealed class NativeDropZonePanel : Panel
        {
            private bool _dragOver;
            private bool _hoverOver;
            private bool _locked;

            public NativeDropZonePanel()
            {
                DoubleBuffered = true;
                BackColor = Color.White;
                ForeColor = Color.FromArgb(31, 41, 55);
                Cursor = Cursors.Hand;
                MouseEnter += (s, e) => { if (!_locked) { _hoverOver = true; Invalidate(); } };
                MouseLeave += (s, e) => { _hoverOver = false; Invalidate(); };
            }

            /// <summary>True while OCR is running; the panel ignores clicks and paints as paused.</summary>
            public bool IsLocked => _locked;

            public void SetDragOver(bool dragOver)
            {
                if (_locked || _dragOver == dragOver)
                    return;

                _dragOver = dragOver;
                Invalidate();
            }

            public void SetLocked(bool locked)
            {
                if (_locked == locked)
                    return;

                _locked = locked;
                _dragOver = false;
                _hoverOver = false;
                Cursor = locked ? Cursors.No : Cursors.Hand;
                Invalidate();
            }

            protected override void OnClick(EventArgs e)
            {
                if (_locked)
                    return;

                base.OnClick(e);
            }

            protected override void OnPaint(PaintEventArgs e)
            {
                base.OnPaint(e);

                e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;

                var bounds = ClientRectangle;
                bounds.Inflate(-1, -1);

                Color fillColor = _locked
                    ? Color.FromArgb(236, 236, 242)
                    : _dragOver ? Color.FromArgb(238, 242, 255) : Color.White;
                Color borderColor = _locked
                    ? Color.FromArgb(212, 212, 224)
                    : _dragOver
                        ? Color.FromArgb(124, 106, 247)
                        : _hoverOver ? Color.FromArgb(180, 168, 252) : Color.FromArgb(212, 212, 224);

                using (var fill = new SolidBrush(fillColor))
                using (var border = new Pen(borderColor, 2f))
                using (var textBrush = new SolidBrush(_locked ? Color.FromArgb(92, 92, 112) : ForeColor))
                using (var mutedBrush = new SolidBrush(Color.FromArgb(92, 92, 112)))
                using (var titleFont = new Font(Font.FontFamily, 11f, FontStyle.Bold))
                using (var bodyFont = new Font(Font.FontFamily, 11f, FontStyle.Regular))
                {
                    border.DashStyle = DashStyle.Dash;
                    e.Graphics.FillRectangle(fill, bounds);
                    e.Graphics.DrawRectangle(border, bounds);

                    var title = _locked ? "Adding files is paused" : "Drop documents or folders here";
                    var body = _locked ? "OCR is running" : "or click to browse";
                    var titleSize = e.Graphics.MeasureString(title, titleFont);
                    var bodySize = e.Graphics.MeasureString(body, bodyFont);

                    // Stack: icon → title → body, centered as a unit
                    const float iconH = 24f;
                    const float iconGap = 6f;
                    const float textGap = 5f;
                    float totalHeight = iconH + iconGap + titleSize.Height + textGap + bodySize.Height;
                    float stackTop = bounds.Top + (bounds.Height - totalHeight) / 2f;

                    float iconY = stackTop;
                    float titleY = stackTop + iconH + iconGap;
                    float bodyY = titleY + titleSize.Height + textGap;

                    Color iconColor = (_hoverOver && !_dragOver) ? Color.FromArgb(124, 106, 247) : Color.FromArgb(92, 92, 112);
                    float penWidth = (_hoverOver && !_dragOver) ? 2f : 1.5f;

                    using (var pen = new Pen(iconColor, penWidth))
                    using (var iconBrush = new SolidBrush(iconColor))
                    {
                        float iconX = bounds.Left + (bounds.Width - iconH) / 2f;
                        // Document rectangle
                        e.Graphics.DrawRectangle(pen, iconX + 3, iconY, iconH - 6, iconH);
                        // Folded corner
                        var cornerPoints = new[]
                        {
                            new PointF(iconX + iconH - 6, iconY),
                            new PointF(iconX + iconH, iconY + 6),
                            new PointF(iconX + iconH - 6, iconY + 6)
                        };
                        e.Graphics.FillPolygon(iconBrush, cornerPoints);
                    }
                    e.Graphics.DrawString(
                        title,
                        titleFont,
                        textBrush,
                        bounds.Left + (bounds.Width - titleSize.Width) / 2f,
                        titleY);
                    e.Graphics.DrawString(
                        body,
                        bodyFont,
                        mutedBrush,
                        bounds.Left + (bounds.Width - bodySize.Width) / 2f,
                        bodyY);
                }
            }
        }
    }
}
