using System;
using System.Collections.Generic;
using System.Drawing;
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
    /// Hosts the file-manager web UI in a standalone non-modal window bound to one workbook.
    /// </summary>
    /// <remarks>
    /// The folder list is native — see <see cref="FileManagerSidebar"/> — and the web UI owns
    /// only the file table. Explorer → WebView2 file drops often do not surface HTML5 drop
    /// events inside Office/WinForms; Chromium may navigate to file:/// URLs instead. This host
    /// therefore disables WebView2 external drops and takes <see cref="DataFormats.FileDrop"/>
    /// paths in three ways: a folder row, the file table (into the selected folder), and the
    /// navigation-cancellation handler, which catches any file:/// URL that slips through.
    /// <para>
    /// The same boundary blocks the other direction, and the workaround is the mirror of the
    /// one above: a document dragged out of the web table is not an OLE drag and cannot become
    /// one, so the web sends <c>row-drag-started</c>, the sidebar takes the mouse, and the
    /// release is handled here. See <see cref="HandleRowDragStarted"/>.
    /// </para>
    /// </remarks>
    public sealed class FileManagerHost : Form
    {
        /// <summary>Width of the native folder column, in logical pixels.</summary>
        private const int SidebarWidth = 260;

        private readonly Excel.Workbook _workbook;
        private readonly WebView2 _webView = new WebView2();
        private WebViewStartupSurface _startup;
        private readonly FileManagerSidebar _sidebar = new FileManagerSidebar();
        private readonly ManageFilesService _service = new ManageFilesService();
        private readonly PdfExportService _exportService = new PdfExportService();
        private OcrService _ocrService;

        /// <summary>PDF IDs currently being processed by OCR. Populated before the await, cleared in finally.</summary>
        private readonly HashSet<string> _activeOcrIds = new HashSet<string>(StringComparer.Ordinal);

        /// <summary>
        /// True for the whole OCR run. Every file-add entry point (folder-row drop, table drop,
        /// file:/// navigation fallback, the file picker) is refused while set, and the sidebar
        /// locks its folder CRUD. Keyed off <see cref="_activeOcrIds"/> rather than
        /// <see cref="OcrService.IsRunning"/>, which only flips once the worker starts and so
        /// leaves the job-loading phase unguarded.
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
            MinimumSize = new Size(700, 480);
            StartPosition = FormStartPosition.CenterScreen;
            AllowDrop = true;

            _webView.Dock = DockStyle.Fill;
            _webView.DragEnter += NativeFileDrop_DragEnter;
            _webView.DragOver += NativeFileDrop_DragEnter;
            _webView.DragDrop += NativeFileDrop_DragDrop;
            Controls.Add(_webView);

            // Added after the fill-docked web view so docking gives the sidebar its strip
            // first and the web view the remainder.
            _sidebar.Dock = DockStyle.Left;
            _sidebar.Width = SidebarWidth;
            _sidebar.InteractionStarted += OnSidebarInteractionStarted;
            _sidebar.FolderSelected += OnSidebarFolderSelected;
            _sidebar.FolderCreateRequested += OnSidebarFolderCreateRequested;
            _sidebar.FolderRenameRequested += OnSidebarFolderRenameRequested;
            _sidebar.FolderRemoveRequested += OnSidebarFolderRemoveRequested;
            _sidebar.PathsDropped += OnSidebarPathsDropped;
            _sidebar.FilesDropped += OnSidebarFilesDropped;
            _sidebar.RowDragEnded += OnSidebarRowDragEnded;
            _sidebar.BrowseRequested += ShowPdfFilePicker;
            Controls.Add(_sidebar);

            // Last in, so it covers both the web view and the sidebar until the manager's
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

            ProcessOsPaths(new[] { localPath }, _sidebar.SelectedFolderId, FileDropScope.SelectedFolder);
        }

        private void CoreWebView2_NavigationCompleted(object sender, CoreWebView2NavigationCompletedEventArgs e)
        {
            // Nothing to re-measure: the sidebar is docked, not positioned by hand.
        }

        /// <summary>
        /// True when the pointer sits over the native sidebar. The sidebar is its own drop
        /// target and the blank area below its rows is deliberately inert, so the window-level
        /// handlers must not claim a drop the sidebar has already refused.
        /// </summary>
        private bool IsOverSidebar(DragEventArgs e) =>
            _sidebar.Visible
            && _sidebar.RectangleToScreen(_sidebar.ClientRectangle).Contains(e.X, e.Y);

        private void NativeFileDrop_DragEnter(object sender, DragEventArgs e)
        {
            if (_disposed)
            {
                e.Effect = DragDropEffects.None;
                return;
            }

            if (IsOverSidebar(e) || IsOcrLocked || GetDroppedPaths(e.Data).Length == 0)
            {
                e.Effect = DragDropEffects.None;
                return;
            }

            e.Effect = DragDropEffects.Copy;
        }

        private void NativeFileDrop_DragLeave(object sender, EventArgs e)
        {
        }

        private void NativeFileDrop_DragDrop(object sender, DragEventArgs e)
        {
            if (_disposed || IsOcrLocked || IsOverSidebar(e))
                return;

            string[] paths = GetDroppedPaths(e.Data);
            if (paths.Length == 0)
                return;

            BeginInvoke(new Action(() => ProcessOsPaths(paths, _sidebar.SelectedFolderId, FileDropScope.SelectedFolder)));
        }

        private static string[] GetDroppedPaths(IDataObject data)
        {
            if (data == null || !data.GetDataPresent(DataFormats.FileDrop))
                return Array.Empty<string>();

            return data.GetData(DataFormats.FileDrop) as string[] ?? Array.Empty<string>();
        }

        /// <summary>
        /// Enables/disables every OS-level file-add affordance while OCR runs: the file table's
        /// drop target and the sidebar's per-row drops and folder CRUD. The sidebar paints itself
        /// muted and refuses drops, so the block is visible, not just silent.
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
            _sidebar.SetLocked(locked);
        }

        // ── Native sidebar intents ────────────────────────────────────────────

        private void OnSidebarInteractionStarted()
        {
            if (_disposed || !_webViewReady) return;
            PostToWebView(FileManagerMessageSerializer.BuildDismissContextMenu());
        }

/// <summary>
        /// Tells the web UI which folder to filter by — it owns no folder list of its own.
        /// The sidebar keeps the selection itself; a drop onto the file table rather than
        /// a row reads it back through <see cref="FileManagerSidebar.SelectedFolderId"/>.
        /// </summary>
        private void OnSidebarFolderSelected(string folderId)
        {
            PostToWebView(FileManagerMessageSerializer.BuildFolderSelected(folderId));
        }

        private void OnSidebarPathsDropped(string[] paths, string folderId, FileDropScope scope)
        {
            if (paths == null || paths.Length == 0) return;
            if (IsOcrLocked) return;

            BeginInvoke(new Action(() => ProcessOsPaths(paths, folderId, scope)));
        }

        private void OnSidebarFolderCreateRequested(string name)
        {
            Excel.Workbook wb = _workbook;
            if (wb == null) return;
            if (!RequireWritable(wb)) return;

            _service.AddFolder(wb, name);
            SendFilesToWebView();
            Globals.ThisAddIn.NotifyViewerFoldersChanged(_workbook);
        }

        private void OnSidebarFolderRenameRequested(string folderId, string newName)
        {
            Excel.Workbook wb = _workbook;
            if (wb == null) return;
            if (!RequireWritable(wb)) return;

            _service.RenameFolder(wb, folderId, newName);
            SendFilesToWebView();
            Globals.ThisAddIn.NotifyViewerFoldersChanged(_workbook);
        }

        private void OnSidebarFolderRemoveRequested(string folderId)
        {
            Excel.Workbook wb = _workbook;
            if (wb == null) return;
            if (!RequireWritable(wb)) return;

            _service.RemoveFolder(wb, folderId);
            SendFilesToWebView();
            Globals.ThisAddIn.NotifyViewerFoldersChanged(_workbook);
        }

        /// <summary>
        /// Imports documents (or folders of documents) dropped from the OS or chosen in the
        /// file picker. Every candidate lands in <paramref name="folderId"/>, and
        /// <paramref name="scope"/> decides what a dropped <em>directory</em> becomes: a
        /// folder named after itself when the drop did not name a destination, or a flat
        /// import when it landed on a specific folder. De-duplicates rapid double delivery
        /// (NavigationStarting + DragDrop). Non-PDF files are confirmed with the user and
        /// converted before anything is embedded.
        /// </summary>
        private async void ProcessOsPaths(string[] paths, string folderId, FileDropScope scope)
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
                // One folder per dropped directory per call, so a tree with repeated paths
                // cannot produce duplicate folders.
                var newFolderIds = new Dictionary<string, string>(StringComparer.Ordinal);
                var candidates = new List<ImportCandidate>();

                // Candidates from a directory dropped without a named destination, paired with
                // the directory they came from. Their folder is only created once the user has
                // confirmed the import, so cancelling leaves no empty folders behind.
                var deferred = new List<KeyValuePair<string, ImportCandidate>>();

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
                        if ((attr & FileAttributes.Directory) != FileAttributes.Directory)
                        {
                            candidates.Add(new ImportCandidate
                            {
                                Path = path,
                                Name = Path.GetFileName(path),
                                FolderId = folderId,
                            });
                            continue;
                        }

                        bool deferFolder = scope == FileDropScope.SelectedFolder;
                        foreach (ImportCandidate candidate in ImportPathCollector.CollectDirectory(path, deferFolder ? null : folderId))
                        {
                            if (ShouldSkipDuplicateOsImport(candidate.Path)) continue;
                            candidates.Add(candidate);
                            if (deferFolder) deferred.Add(new KeyValuePair<string, ImportCandidate>(path, candidate));
                        }
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

                foreach (KeyValuePair<string, ImportCandidate> pair in deferred)
                {
                    string created = CreateFolderForDirectory(wb, pair.Key, newFolderIds);
                    if (created != null) pair.Value.FolderId = created;
                }

                if (deferred.Count > 0)
                {
                    // The folders exist whether or not the files below them convert, so the
                    // row list is refreshed here rather than left to the post-import push.
                    SendFilesToWebView();
                    Globals.ThisAddIn.NotifyViewerFoldersChanged(_workbook);
                }

                await ImportPreparedAsync(wb, plan);
            }
            catch (Exception ex)
            {
                TalliarkLog.Trace($"ProcessOsPaths failed: {ex}");
                ShowImportFailure(ex);
            }
        }

        /// <summary>
        /// Creates — or returns, for a directory seen earlier in the same drop — the folder
        /// named after <paramref name="dirPath"/>. A directory dropped without a named
        /// destination carries its own name as the one piece of intent the user gave, so
        /// that name is honoured instead of being flattened away.
        /// </summary>
        private string CreateFolderForDirectory(
            Excel.Workbook wb,
            string dirPath,
            Dictionary<string, string> cache)
        {
            if (cache.TryGetValue(dirPath, out string existing)) return existing;

            string folderName;
            try
            {
                folderName = new DirectoryInfo(dirPath).Name;
            }
            catch
            {
                return null;
            }

            if (string.IsNullOrWhiteSpace(folderName)) return null;

            string id = _service.AddFolder(wb, folderName);
            cache[dirPath] = id;
            return id;
        }

        /// <summary>
        /// Converts, embeds and refreshes for an already-confirmed plan. Shared by every
        /// OS drop route and the native file picker.
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
                    // WebView postMessage preserves order, so selection is applied only
                    // after the table has received rows for the newly imported IDs.
                    PostToWebView(FileManagerMessageSerializer.BuildSelectFiles(addedIds));
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

                    case "rename-file":
                        HandleRenameFile(FileManagerMessageParser.ParseRenameFile(raw));
                        break;

                    case "remove-file":
                        HandleRemoveFile(FileManagerMessageParser.ParseRemoveFile(raw));
                        break;

                    case "export-file":
                        _ = HandleExportFileAsync(FileManagerMessageParser.ParseExportFile(raw));
                        break;

                    case "select-file":
                        HandleSelectFile(FileManagerMessageParser.ParseSelectFile(raw));
                        break;

                    case "open-file-in-viewer":
                        HandleOpenFileInViewer(FileManagerMessageParser.ParseOpenFileInViewer(raw));
                        break;

                    case "move-file":
                        HandleMoveFile(FileManagerMessageParser.ParseMoveFile(raw));
                        break;

                    case "row-drag-started":
                        HandleRowDragStarted(FileManagerMessageParser.ParseRowDragStarted(raw));
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
                            if (detail?.PdfOrientationChanged == true)
                            {
                                // OCR persisted a new intrinsic page orientation and moved
                                // stored links with it, so refresh the whole viewer data set.
                                Globals.ThisAddIn.NotifyViewerLinksChanged(_workbook);
                            }
                            else
                            {
                                Globals.ThisAddIn.RefreshTaskPanePdf(_workbook, pdfId);
                            }
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

            BeginInvoke(new Action(() => ProcessOsPaths(selectedPaths, _sidebar.SelectedFolderId, FileDropScope.SelectedFolder)));
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
        /// Exports one read-only snapshot. The save path is chosen before background work
        /// begins; workbook Custom XML is read on the UI thread, and only detached strings
        /// are passed to the worker thread.
        /// </summary>
        private async Task HandleExportFileAsync(ExportFileRequest req)
        {
            if (IsOcrLocked || string.IsNullOrWhiteSpace(req?.Id)) return;

            PdfExportSource source;
            try
            {
                source = _exportService.LoadSource(_workbook, req.Id);
            }
            catch (Exception ex)
            {
                MessageBox.Show(this, "Could not load the PDF for export.\n\n" + ex.Message,
                    "Talliark Export", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return;
            }

            string outputPath;
            using (var dialog = new SaveFileDialog())
            {
                dialog.Title = "Export PDF";
                dialog.Filter = "PDF files (*.pdf)|*.pdf";
                dialog.DefaultExt = "pdf";
                dialog.AddExtension = true;
                dialog.OverwritePrompt = true;
                dialog.FileName = source.SuggestedFileName;
                if (dialog.ShowDialog(this) != DialogResult.OK)
                    return;
                outputPath = dialog.FileName;
            }

            PdfExportResult result;
            UseWaitCursor = true;
            try
            {
                result = await _exportService.ExportAsync(source, outputPath);
            }
            catch (Exception ex)
            {
                if (!_disposed && !IsDisposed)
                {
                    MessageBox.Show(this, "Could not export the PDF.\n\n" + ex.Message,
                        "Talliark Export", MessageBoxButtons.OK, MessageBoxIcon.Error);
                }
                return;
            }
            finally
            {
                if (!_disposed && !IsDisposed)
                    UseWaitCursor = false;
            }

            if (!_disposed && !IsDisposed)
            {
                string ocrResult = result.OcrTextLayerAdded
                    ? "Added from stored OCR geometry"
                    : "Not added (no stored OCR geometry)";
                MessageBox.Show(this,
                    "PDF exported successfully.\n\n" +
                    "File: " + result.OutputPath + "\n" +
                    "Size: " + FormatFileSize(result.OutputBytes) + "\n" +
                    "OCR text layer: " + ocrResult,
                    "Talliark Export", MessageBoxButtons.OK, MessageBoxIcon.Information);
            }
        }

        private static string FormatFileSize(long bytes)
        {
            if (bytes < 1024)
                return bytes + " bytes";

            double size = bytes;
            string[] units = { "KB", "MB", "GB", "TB" };
            int unitIndex = -1;
            do
            {
                size /= 1024;
                unitIndex++;
            }
            while (size >= 1024 && unitIndex < units.Length - 1);

            return size.ToString(size >= 10 ? "0.0" : "0.00") + " " + units[unitIndex];
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

        /// <summary>
        /// Shows this manager's workbook in the task-pane viewer and selects the requested PDF.
        /// Unlike ordinary row selection, this explicit command also survives viewer startup.
        /// </summary>
        private void HandleOpenFileInViewer(OpenFileInViewerRequest req)
        {
            if (string.IsNullOrWhiteSpace(req?.Id)) return;
            Globals.ThisAddIn.OpenPdfInTaskPane(_workbook, req.Id);
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

        /// <summary>
        /// Arms a row drag on the native folder list. The web UI cannot finish this gesture by
        /// itself: an HTML5 drag never leaves Chromium, and a mouse drag out of the file table
        /// releases into a WinForms panel it cannot see. So the web reports the drag instead,
        /// the sidebar takes the mouse, and this host hears the release.
        /// </summary>
        private void HandleRowDragStarted(RowDragStartedRequest req)
        {
            if (_disposed) return;

            var ids = new List<string>(req?.FileIds ?? new List<string>());

            // A file in an active OCR run is not ours to move — the same refusal
            // HandleMoveFile makes. The rest of the selection still moves.
            ids.RemoveAll(id => _activeOcrIds.Contains(id));

            // A refusal means the sidebar never armed, so nothing it raises will ever report
            // the end of this drag. It has to be reported from here or the web table is left
            // showing a drag that finished before it began.
            if (!_sidebar.BeginRowDrag(ids)) SendRowDragEnded();
        }

        private void OnSidebarFilesDropped(List<string> fileIds, string folderId)
        {
            if (_disposed || fileIds == null || fileIds.Count == 0) return;

            Excel.Workbook wb = _workbook;
            if (wb == null) return;
            if (!RequireWritable(wb)) return;

            // One load and one save for the whole selection, then one push, so the file table
            // and the folder list both land on the same list of counts in the same frame.
            TalliarkContent content = _service.MoveFiles(wb, fileIds, folderId);
            SendFilesToWebView(content);
            Globals.ThisAddIn.NotifyViewerFoldersChanged(wb);
        }

        /// <summary>The row drag is over; the web table is waiting to be told.</summary>
        private void OnSidebarRowDragEnded()
        {
            SendRowDragEnded();
        }

        private void SendRowDragEnded()
        {
            if (_disposed || !_webViewReady) return;
            PostToWebView(FileManagerMessageSerializer.BuildRowDragEnded());
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

                // One read of the workbook feeds both surfaces, so the native row list and
                // the web table can never disagree about counts or order.
                _sidebar.Update(content.Folders, content.Pdfs);
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

                    DragEnter -= NativeFileDrop_DragEnter;
                    DragOver -= NativeFileDrop_DragEnter;
                    DragLeave -= NativeFileDrop_DragLeave;
                    DragDrop -= NativeFileDrop_DragDrop;

                    _sidebar.InteractionStarted -= OnSidebarInteractionStarted;
                    _sidebar.FolderSelected -= OnSidebarFolderSelected;
                    _sidebar.FolderCreateRequested -= OnSidebarFolderCreateRequested;
                    _sidebar.FolderRenameRequested -= OnSidebarFolderRenameRequested;
                    _sidebar.FolderRemoveRequested -= OnSidebarFolderRemoveRequested;
                    _sidebar.PathsDropped -= OnSidebarPathsDropped;
                    _sidebar.BrowseRequested -= ShowPdfFilePicker;

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

        private void SendResetUiToWebView()
        {
            if (_disposed) return;
            if (!_webViewReady) return;

            // Both surfaces reset together. The sidebar clearing first raises folder-selected,
            // which reaches a web view that is about to be told to reset anyway — harmless,
            // and cheaper than a second ordering rule for the other direction.
            _sidebar.Reset();

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
    }
}
