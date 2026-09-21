using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading.Tasks;
using System.Windows.Forms;
using Talliark.Addin.Modules.CustomXml;
using Talliark.Addin.Modules.CustomXml.Models;
using Talliark.Addin.Modules.Services;
using Talliark.Addin.Modules.UI;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;
using Excel = Microsoft.Office.Interop.Excel;

namespace Talliark.Addin.Modules.WebView
{
    /// <summary>Shared WebView2 + messaging logic for the document-viewer web app.</summary>
    internal sealed class DocumentViewerController : IDisposable
    {
        private readonly Control _invokeTarget;
        private readonly string _loadFailureSurfaceName;
        private readonly Excel.Workbook _workbook;
        private readonly Panel _surface = new Panel();
        private readonly WebView2 _webView = new WebView2();
        private WebViewStartupSurface _startup;
        private readonly ExcelGridFocusRestoreService _focusRestoreService;
        private ThreadedProgressController _cacheProgress;
        private Task _initTask;
        private bool _webShellReady;
        private bool _webViewReady;
        private bool _dataSentToViewer;
        private bool _viewerShown;
        private bool _contentReady;
        private string _pendingNavigateId;
        private string _pendingNavigatePdfId;
        private int? _pendingNavigatePage;
        private string _pendingSearchQuery;
        private bool _disposed;

        internal DocumentViewerController(
            Control invokeTarget,
            string loadFailureSurfaceName,
            Excel.Workbook workbook)
        {
            _invokeTarget = invokeTarget ?? throw new ArgumentNullException(nameof(invokeTarget));
            _loadFailureSurfaceName = loadFailureSurfaceName ?? "viewer";
            _workbook = workbook ?? throw new ArgumentNullException(nameof(workbook));

            _surface.Dock = DockStyle.Fill;

            _webView.Dock = DockStyle.Fill;
            _webView.Leave += OnWebViewLeave;
            _focusRestoreService = new ExcelGridFocusRestoreService(_surface);

            _surface.Controls.Add(_webView);

            // Same placeholder the file manager and linker use. The viewer needs it most:
            // its bundle is the largest, and the pane is the surface opened most often.
            _startup = new WebViewStartupSurface(_surface, _webView);
        }

        internal Control Surface => _surface;

        internal WebView2 WebView => _webView;

        internal void Start()
        {
            if (_disposed) return;
            if (_initTask != null) return;
            TalliarkLog.Trace($"START surface={_loadFailureSurfaceName}");
            _initTask = InitAsync();
        }

        private async Task InitAsync()
        {
            TalliarkLog.Trace($"ENTER surface={_loadFailureSurfaceName}");
            try
            {
                if (_disposed) return;

                // One environment per process, shared by every host: they all point at the
                // same user data folder, and the runtime refuses a second environment over
                // one folder. Already warm by the time any window opens.
                var environment = await WebViewEagerLoader.GetEnvironmentAsync();

                await _webView.EnsureCoreWebView2Async(environment);
                if (_disposed) return;

                string uiPath = GetWebUiPath();
                if (!Directory.Exists(uiPath))
                    throw new DirectoryNotFoundException(
                        $"Web UI folder not found: {uiPath}\n\nRun 'npm run build' in src/web to generate it.");

                _webView.CoreWebView2.SetVirtualHostNameToFolderMapping(
                    "talliark.local",
                    uiPath,
                    CoreWebView2HostResourceAccessKind.Allow);

                _webView.CoreWebView2.WebMessageReceived += OnWebMessageReceived;

                _webView.CoreWebView2.Navigate("https://talliark.local/index.html");
                TalliarkLog.Trace($"EXIT initialized surface={_loadFailureSurfaceName}");
            }
            catch (Exception ex)
            {
                // Workbook-bound viewer surfaces can be eagerly initialized and then disposed
                // when Excel replaces its temporary blank workbook with the file being opened.
                // WebView2 reports that expected in-flight cancellation as E_ABORT.
                if (_disposed)
                {
                    TalliarkLog.Trace(
                        $"CANCEL initialization after dispose surface={_loadFailureSurfaceName} " +
                        $"{ex.GetType().FullName}: {ex.Message}");
                    return;
                }

                TalliarkLog.Trace($"EXCEPTION surface={_loadFailureSurfaceName} {ex.GetType().FullName}: {ex.Message}");
                MessageBox.Show(
                    $"Talliark {_loadFailureSurfaceName} failed to load:\n\n{ex.Message}",
                    "Talliark",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error);

                ShowStartupFailure(ex.Message);
            }
        }

        private void RevealWebView() => _startup?.Reveal();

        private void ShowStartupFailure(string message) => _startup?.ShowFailure(message);

        private void OnWebMessageReceived(object sender, CoreWebView2WebMessageReceivedEventArgs e)
        {
            if (_disposed) return;

            try
            {
                string raw = e.TryGetWebMessageAsString();
                if (string.IsNullOrWhiteSpace(raw))
                    return;

                string messageType = HostMessageParser.GetMessageType(raw);
                TalliarkLog.Trace($"message type={messageType ?? "(unknown)"} surface={_loadFailureSurfaceName}");

                switch (messageType)
                {
                    case "viewer-shell-ready":
                        // A shell-ready message identifies a newly mounted web document. Any
                        // state retained by this controller belongs to the previous JavaScript
                        // context and must not suppress the new context's bootstrap payload.
                        _webShellReady = true;
                        _webViewReady = false;
                        _dataSentToViewer = false;
                        _contentReady = false;
                        break;

                    case "viewer-ready":
                        if (!_webShellReady)
                        {
                            _webShellReady = true;
                        }
                        _webViewReady = true;
                        // viewer-ready is emitted once per initialized JavaScript context. A
                        // renderer/page restart therefore needs a complete authoritative sync,
                        // even if the previous context had already received the workbook data.
                        _dataSentToViewer = false;
                        SendDevStateToWebView();
                        if (_viewerShown)
                        {
                            RefreshDataIfReady();
                        }
                        break;

                    case "viewer-content-ready":
                        _contentReady = true;
                        // Also dismiss the cache-build progress here. It was opened in
                        // NotifyViewerShown() and is normally closed by cache-build-complete,
                        // but viewer-content-ready is always sent (from the finally block in
                        // viewer-bridge.ts) and arrives after onDocumentChanged has run —
                        // so it guarantees the loader is dismissed even when the cache was
                        // already populated (the fast path that skips sendCacheBuildComplete).
                        _cacheProgress?.Dispose();
                        _cacheProgress = null;
                        if (_viewerShown)
                            RevealWebView();
                        break;

                    case "open-file-manager":
                        HandleOpenFileManager();
                        break;

                    case "link-rectangle-created":
                        HandleLinkRectangleCreated(raw);
                        break;

                    case "link-rectangle-updated":
                        HandleLinkRectangleUpdated(raw);
                        break;

                    case "link-rectangle-clicked":
                        HandleLinkRectangleClicked(raw);
                        break;

                    case "link-rectangle-deleted":
                        HandleLinkRectangleDeleted(raw);
                        break;

                    case "copy-table-selection":
                        HandleCopyTableSelection(raw);
                        break;

                    case "excel-navigate":
                        HandleExcelNavigate(raw);
                        break;

                    case "undo-link-creation":
                        HandleUndoLinkCreation();
                        break;

                    case "rotate-page":
                        HandleRotatePage(raw);
                        break;

                    case "cache-build-complete":
                        _cacheProgress?.Dispose();
                        _cacheProgress = null;
                        break;
                }
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"[Talliark] OnWebMessageReceived failed: {ex.Message}");
            }
        }

        private void OnWebViewLeave(object sender, EventArgs e)
        {
            if (_disposed) return;
            RestoreExcelFocus();
        }

        private void HandleOpenFileManager()
        {
            _invokeTarget.BeginInvoke(new Action(() =>
            {
                if (!TryActivateWorkbook()) return;
                Globals.ThisAddIn.ShowManageFilesWindow(_workbook);
            }));
        }

        private void RestoreExcelFocus()
        {
            ExcelGridFocusRestoreService.RestoreExcelFocus();
        }

        /// <summary>
        /// Moves the Excel cursor for a Tab/Enter keystroke the viewer forwarded, so the grid
        /// keeps responding while the user works inside the PDF.
        /// </summary>
        private void HandleExcelNavigate(string json)
        {
            var payload = HostMessageParser.ParseExcelNavigate(json);
            if (payload == null) return;

            _invokeTarget.BeginInvoke(new Action(() =>
            {
                if (!TryActivateWorkbook()) return;
                Globals.ThisAddIn.CellNavigation.Navigate(payload.Motion, payload.Reverse);
            }));
        }

        /// <summary>
        /// Routes undo through the add-in so Excel's native undo stack keeps priority over
        /// Talliark's link-creation history.
        /// </summary>
        private void HandleUndoLinkCreation()
        {
            _invokeTarget.BeginInvoke(new Action(() =>
            {
                if (!TryActivateWorkbook()) return;
                Globals.ThisAddIn.UndoMostRecentAction();
            }));
        }

        private void HandleLinkRectangleCreated(string json)
        {
            var payload = HostMessageParser.ParseLinkRectangleCreated(json);
            if (payload == null) return;

            _invokeTarget.BeginInvoke(new Action(() =>
            {
                if (!TryActivateWorkbook()) return;
                ExecuteLinkRectangleCreated(payload, _workbook);
            }));
        }

        private void ExecuteLinkRectangleCreated(LinkRectangleCreatedPayload payload, Excel.Workbook wb)
        {
            using (TalliarkLog.Time("ExecuteLinkRectangleCreated total"))
            {
                TalliarkLog.Trace("ENTER");
                IWin32Window owner = _invokeTarget.FindForm() ?? _invokeTarget;
                if (!WorkbookProtectionGuard.TryRequireWritable(wb, owner))
                    return;

                string text = payload.Text;
                if (payload.LinkType != LinkType.Table && string.IsNullOrWhiteSpace(text))
                {
                    if (!LinkTextPromptDialog.TryPrompt(owner, out text))
                    {
                        TalliarkLog.Trace("text prompt cancelled");
                        SendLinkedRectanglesToWebView();
                        return;
                    }
                }

                // Resolved from the workbook's last user selection rather than the live
                // Application.Selection: the pane's window can become active without a
                // selection event, which used to point this at the wrong sheet.
                if (!Globals.ThisAddIn.TryGetLinkTargetCell(
                        wb, out Excel.Range startCell, out Excel.Range activeCell))
                {
                    TalliarkLog.Trace("create-link aborted: no link target cell for this workbook");
                    SendLinkedRectanglesToWebView();
                    return;
                }

                TalliarkLog.Trace($"text='{text}' – calling CreateLink");
                LinkedRectangle linkedRect;
                IList<LinkedRectangle> allRects;
                using (Globals.ThisAddIn.EnterSelectionNavSuppress())
                {
                    (linkedRect, allRects) = new CreateLinkService().CreateLink(
                        payload.PdfId,
                        payload.Page,
                        payload.X, payload.Y, payload.Width, payload.Height,
                        text,
                        payload.LinkType,
                        payload.AppendToActiveSum,
                        payload.TableGrid,
                        payload.TableCells,
                        startCell,
                        activeCell,
                        owner,
                        wb);

                    if (linkedRect != null)
                    {
                        Excel.Range linkedCell = LinkCellResolver.TryResolveCell(wb, linkedRect);
                        ExcelCellNavigationService.BringIntoView(linkedCell);
                    }
                }
                TalliarkLog.Trace($"CreateLink returned id={linkedRect?.Id ?? "null"}");

                try
                {
                    var ac = Globals.ThisAddIn.Application?.ActiveCell as Excel.Range;
                    TalliarkLog.Trace($"active cell after CreateLink: {ac?.Address ?? "null"}, value={ac?.Value2 ?? "(null)"}");
                }
                catch (Exception ex) { TalliarkLog.Trace($"post-create cell read failed: {ex.Message}"); }

                TalliarkLog.Trace("calling SendLinkedRectanglesToWebView (pre-loaded)");
                if (allRects != null)
                    SendLinkedRectanglesToWebView(allRects);
                else
                    SendLinkedRectanglesToWebView();
                TalliarkLog.Trace("SendLinkedRectanglesToWebView done");

                if (linkedRect != null)
                {
                    TalliarkLog.Trace($"calling SendHighlightRectangle id={linkedRect.Id}");
                    SendHighlightRectangle(linkedRect.Id);
                    TalliarkLog.Trace("SendHighlightRectangle done");
                }

                Globals.ThisAddIn.NotifyFileManagerLinksChanged(_workbook);

                TalliarkLog.Trace("restoring focus to Excel");
                RestoreExcelFocus();

                TalliarkLog.Trace("EXIT");
            }
        }

        private void HandleLinkRectangleUpdated(string json)
        {
            var payload = HostMessageParser.ParseLinkRectangleUpdated(json);
            if (payload == null) return;

            if (!TryActivateWorkbook()) return;
            Excel.Workbook wb = _workbook;

            IWin32Window owner = _invokeTarget.FindForm() ?? _invokeTarget;
            if (!WorkbookProtectionGuard.TryRequireWritable(wb, owner))
                return;

            string text = payload.Text;
            if (payload.TableGrid == null && string.IsNullOrWhiteSpace(text))
            {
                if (!LinkTextPromptDialog.TryPrompt(owner, out text))
                {
                    SendLinkedRectanglesToWebView();
                    return;
                }
            }

            new UpdateLinkService().UpdateLink(
                payload.Id,
                payload.Page,
                payload.X, payload.Y, payload.Width, payload.Height,
                text,
                payload.TableGrid,
                payload.TableCells,
                owner,
                wb);

            SendLinkedRectanglesToWebView();

            RestoreExcelFocus();
        }

        private void HandleLinkRectangleClicked(string json)
        {
            string rectId = HostMessageParser.ParseLinkRectangleClicked(json);
            if (string.IsNullOrWhiteSpace(rectId)) return;

            if (!TryActivateWorkbook()) return;
            Excel.Workbook wb = _workbook;

            Globals.ThisAddIn.SuppressNextSelectionNav = true;

            var session = Globals.ThisAddIn.GetStorageSession(wb);
            var rect = session.GetLinks().FirstOrDefault(r => string.Equals(r.Id, rectId, StringComparison.Ordinal));
            if (rect == null)
            {
                Globals.ThisAddIn.SuppressNextSelectionNav = false;
                return;
            }

            Excel.Range cell = LinkCellResolver.TryResolveCell(wb, rect);
            if (cell == null)
            {
                Globals.ThisAddIn.SuppressNextSelectionNav = false;
                return;
            }

            try
            {
                ((Excel.Worksheet)cell.Worksheet).Activate();
                cell.Select();

                // The select above is a no-op when the sheet already had this cell selected,
                // and Excel raises no selection event for that — record the jump explicitly
                // so the next link created from the viewer lands here.
                Globals.ThisAddIn.NoteLinkTargetCell(cell);

                // Selection nav is suppressed for this round-trip, so publish the selection
                // explicitly: a Sum cell still has several rectangles to list.
                Globals.ThisAddIn.PublishLinkSelection(cell);

                RestoreExcelFocus();
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"[Talliark] HandleLinkRectangleClicked navigate failed: {ex.Message}");
            }
            finally
            {
                Globals.ThisAddIn.SuppressNextSelectionNav = false;
            }
        }

        private void HandleLinkRectangleDeleted(string json)
        {
            LinkRectangleDeletedPayload payload =
                HostMessageParser.ParseLinkRectangleDeletion(json);
            if (payload == null) return;

            if (!TryActivateWorkbook()) return;
            Excel.Workbook wb = _workbook;

            IWin32Window owner = _invokeTarget.FindForm() ?? _invokeTarget;
            if (!WorkbookProtectionGuard.TryRequireWritable(wb, owner))
                return;

            using (Globals.ThisAddIn.EnterSelectionNavSuppress())
            {
                if (!new DeleteLinkService().DeleteLink(
                    payload.Id, wb, payload.DeleteCellData))
                    return;

                SendLinkRectanglesRemoved(new[] { payload.Id });
            }

            Globals.ThisAddIn.NotifyFileManagerLinksChanged(_workbook);

            RestoreExcelFocus();
        }

        private void HandleCopyTableSelection(string json)
        {
            CopyTableSelectionPayload payload = HostMessageParser.ParseCopyTableSelection(json);
            if (payload == null) return;

            if (!TryActivateWorkbook()) return;
            Excel.Workbook wb = _workbook;

            IWin32Window owner = _invokeTarget.FindForm() ?? _invokeTarget;
            if (!WorkbookProtectionGuard.TryRequireWritable(wb, owner))
                return;

            var targets = payload.Targets
                .Select(target => (target.Page, target.TableGrid, target.TableCells))
                .ToList();
            IList<LinkedRectangle> allRects;
            using (Globals.ThisAddIn.EnterSelectionNavSuppress())
            {
                allRects = new CopyTableSelectionService().Copy(
                    payload.Id, targets, owner, wb, SendLinkedRectangleAdded);
            }
            if (allRects == null) return;

            Globals.ThisAddIn.NotifyFileManagerLinksChanged(_workbook);
            RestoreExcelFocus();
        }

        private void HandleRotatePage(string json)
        {
            var payload = HostMessageParser.ParseRotatePage(json);
            if (payload == null) return;

            if (!TryActivateWorkbook()) return;
            Excel.Workbook wb = _workbook;

            IWin32Window owner = _invokeTarget.FindForm() ?? _invokeTarget;
            if (!WorkbookProtectionGuard.TryRequireWritable(wb, owner))
                return;

            _invokeTarget.BeginInvoke(new Action(() => ExecuteRotatePage(payload, wb)));
        }

        private void ExecuteRotatePage(RotatePagePayload payload, Excel.Workbook wb)
        {
            try
            {
                var (newRotations, allRects) = new RotatePageService().RotatePage(
                    payload.PdfId, payload.Page, payload.Direction, wb);

                SendPageRotationsUpdated(payload.PdfId, newRotations);
                SendLinkedRectanglesToWebView(allRects);
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"[Talliark] ExecuteRotatePage failed: {ex.Message}");
            }
        }

        internal void SendPageRotationsUpdated(string pdfId, Dictionary<int, int> rotations)
        {
            if (!_webViewReady || string.IsNullOrWhiteSpace(pdfId))
                return;

            try
            {
                string json = HostMessageSerializer.BuildPageRotationsUpdated(pdfId, rotations);
                _webView.CoreWebView2.PostWebMessageAsString(json);
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"[Talliark] SendPageRotationsUpdated failed: {ex.Message}");
            }
        }

        internal void SendLinkRectanglesRemoved(IList<string> ids)
        {
            if (!_webViewReady || ids == null || ids.Count == 0)
                return;

            try
            {
                _webView.CoreWebView2.PostWebMessageAsString(
                    HostMessageSerializer.BuildLinkRectanglesRemoved(ids));
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine(
                    $"[Talliark] SendLinkRectanglesRemoved failed: {ex.Message}");
            }
        }

        private void SendLinkedRectangleAdded(LinkedRectangle rectangle)
        {
            if (_disposed || !_webViewReady || rectangle == null) return;
            try
            {
                string json = HostMessageSerializer.BuildLinkedRectangleAdded(rectangle);
                _webView.CoreWebView2.PostWebMessageAsString(json);
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine(
                    $"[Talliark] SendLinkedRectangleAdded failed: {ex.Message}");
            }
        }

        /// <summary>
        /// Commands originating from a pop-out must act on that pop-out's workbook, even
        /// when another workbook was focused in Excel. Activating the owner also makes the
        /// workbook's current selection authoritative for link creation and grid navigation.
        /// </summary>
        private bool TryActivateWorkbook()
        {
            if (_disposed) return false;

            try
            {
                _workbook.Activate();
                return true;
            }
            catch (Exception ex)
            {
                TalliarkLog.Trace(
                    $"Activate owning workbook failed surface={_loadFailureSurfaceName}: {ex.Message}");
                return false;
            }
        }

        internal void SendClearRectangleHighlight()
        {
            if (!_webViewReady) return;

            try
            {
                _webView.CoreWebView2.PostWebMessageAsString(
                    HostMessageSerializer.BuildClearRectangleHighlight());
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine(
                    $"[Talliark] SendClearRectangleHighlight failed: {ex.Message}");
            }
        }

        internal void SendLinkSelectionChanged(IList<LinkSelectionEntry> entries)
        {
            if (!_webViewReady) return;

            try
            {
                _webView.CoreWebView2.PostWebMessageAsString(
                    HostMessageSerializer.BuildLinkSelectionChanged(entries));
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine(
                    $"[Talliark] SendLinkSelectionChanged failed: {ex.Message}");
            }
        }

        internal void SendSearchQuery(string query)
        {
            if (!_webViewReady)
            {
                _pendingSearchQuery = query ?? string.Empty;
                return;
            }

            try
            {
                _webView.CoreWebView2.PostWebMessageAsString(
                    HostMessageSerializer.BuildSetSearchQuery(query));
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine(
                    $"[Talliark] SendSearchQuery failed: {ex.Message}");
            }
        }

        /// <summary>
        /// Shows or hides the viewer's per-character bounding-box debug overlay.
        /// Silently ignored until the web context is ready; the state is re-sent
        /// from <see cref="SendDevStateToWebView"/> once it is.
        /// </summary>
        internal void SendCharBboxesVisible(bool visible)
        {
            if (_disposed || !_webViewReady) return;

            try
            {
                _webView.CoreWebView2.PostWebMessageAsString(
                    HostMessageSerializer.BuildSetCharBboxesVisible(visible));
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine(
                    $"[Talliark] SendCharBboxesVisible failed: {ex.Message}");
            }
        }

        internal void SendTableDetectionEnabled(bool enabled)
        {
            if (_disposed || !_webViewReady) return;
            try { _webView.CoreWebView2.PostWebMessageAsString(HostMessageSerializer.BuildSetTableDetectionEnabled(enabled)); }
            catch (Exception ex) { TalliarkLog.Trace("SendTableDetectionEnabled failed: " + ex.Message); }
        }

        internal void SendValuesVisible(bool visible)
        {
            if (_disposed || !_webViewReady) return;

            try
            {
                _webView.CoreWebView2.PostWebMessageAsString(
                    HostMessageSerializer.BuildSetValuesVisible(visible));
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine(
                    $"[Talliark] SendValuesVisible failed: {ex.Message}");
            }
        }

        internal void SendReferencesVisible(bool visible)
        {
            if (_disposed || !_webViewReady) return;

            try
            {
                _webView.CoreWebView2.PostWebMessageAsString(
                    HostMessageSerializer.BuildSetReferencesVisible(visible));
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine(
                    $"[Talliark] SendReferencesVisible failed: {ex.Message}");
            }
        }

        internal void SendStructureVisible(bool visible)
        {
            if (_disposed || !_webViewReady) return;

            try
            {
                _webView.CoreWebView2.PostWebMessageAsString(
                    HostMessageSerializer.BuildSetStructureVisible(visible));
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine(
                    $"[Talliark] SendStructureVisible failed: {ex.Message}");
            }
        }

        internal void SendValueNoiseVisible(bool visible)
        {
            if (_disposed || !_webViewReady) return;

            try
            {
                _webView.CoreWebView2.PostWebMessageAsString(
                    HostMessageSerializer.BuildSetValueNoiseVisible(visible));
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine(
                    $"[Talliark] SendValueNoiseVisible failed: {ex.Message}");
            }
        }

        /// <summary>
        /// Pushes the persisted developer toggles to a freshly initialized web context
        /// so a viewer opened after the setting changed starts in the same state.
        /// </summary>
        private void SendDevStateToWebView()
        {
            SendCharBboxesVisible(Infrastructure.DevSettings.ShowCharBoundingBoxes);
            SendTableDetectionEnabled(Infrastructure.ExperimentalSettings.TableDetection);
            SendValuesVisible(Infrastructure.DevSettings.ShowValues);
            SendReferencesVisible(Infrastructure.DevSettings.ShowReferences);
            SendStructureVisible(Infrastructure.DevSettings.ShowStructure);
            SendValueNoiseVisible(Infrastructure.DevSettings.ShowValueNoise);
        }

        private void FlushPendingSearchQuery()
        {
            if (_pendingSearchQuery == null) return;

            string query = _pendingSearchQuery;
            _pendingSearchQuery = null;
            SendSearchQuery(query);
        }

        internal void SendNavigateToRectangle(string id, string pdfId, int page)
        {
            if (!_webViewReady)
            {
                _pendingNavigateId = id;
                _pendingNavigatePdfId = pdfId;
                _pendingNavigatePage = page;
                return;
            }

            PostNavigateToRectangle(id, pdfId, page);
        }

        private void FlushPendingNavigateToRectangle()
        {
            if (_pendingNavigatePage == null)
                return;

            string id = _pendingNavigateId;
            string pdfId = _pendingNavigatePdfId;
            int page = _pendingNavigatePage.Value;

            _pendingNavigateId = null;
            _pendingNavigatePdfId = null;
            _pendingNavigatePage = null;

            PostNavigateToRectangle(id, pdfId, page);
        }

        private void PostNavigateToRectangle(string id, string pdfId, int page)
        {
            try
            {
                string json = HostMessageSerializer.BuildNavigateToRectangle(id, pdfId, page);
                _webView.CoreWebView2.PostWebMessageAsString(json);
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine(
                    $"[Talliark] SendNavigateToRectangle failed: {ex.Message}");
            }
        }

        private void SendHighlightRectangle(string id)
        {
            try
            {
                string json = HostMessageSerializer.BuildHighlightRectangle(id);
                _webView.CoreWebView2.PostWebMessageAsString(json);
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine(
                    $"[Talliark] SendHighlightRectangle failed: {ex.Message}");
            }
        }

        internal void RefreshDataIfReady()
        {
            TalliarkLog.Trace($"ENTER surface={_loadFailureSurfaceName} ready={_webViewReady} dataSent={_dataSentToViewer}");
            if (_disposed || !_webViewReady || _dataSentToViewer) return;

            // Do not mark this viewer as synchronized when loading or posting the
            // authoritative catalogue failed. A later show/activation/ready event can retry.
            if (!SendPdfsToWebView()) return;

            _dataSentToViewer = true;
            SendLinkedRectanglesToWebView();
            FlushPendingSearchQuery();
            FlushPendingNavigateToRectangle();
            TalliarkLog.Trace($"EXIT surface={_loadFailureSurfaceName}");
        }

        internal void InvalidateData()
        {
            _dataSentToViewer = false;
            _contentReady = false;
        }

        internal void NotifyViewerShown()
        {
            if (!_viewerShown && !_contentReady)
            {
                _cacheProgress?.Dispose();
                _cacheProgress = ThreadedProgressController.Show("Preparing document viewer...");
                _cacheProgress.Report("Preparing document viewer", "Building document index...", 0, 0);
            }
            _viewerShown = true;
            RefreshDataIfReady();
            SendViewerSurfaceShown();
            if (_contentReady)
                RevealWebView();
        }

        private void SendViewerSurfaceShown()
        {
            if (_disposed || !_webViewReady) return;

            try
            {
                _webView.CoreWebView2.PostWebMessageAsString(
                    HostMessageSerializer.BuildViewerSurfaceShown());
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine(
                    $"[Talliark] SendViewerSurfaceShown failed: {ex.Message}");
            }
        }

        internal void SendLinkedRectanglesToWebView()
        {
            using (TalliarkLog.Time($"SendLinkedRectanglesToWebView surface={_loadFailureSurfaceName}"))
            {
            if (_disposed) return;
            try
            {
                PostLinkedRectangles(Globals.ThisAddIn.GetStorageSession(_workbook).GetLinks());
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"[Talliark] SendLinkedRectanglesToWebView failed: {ex.Message}");
                TalliarkLog.Trace($"EXCEPTION {ex.GetType().FullName}: {ex.Message}");
            }
            }
        }

        /// <summary>
        /// Sends a pre-loaded linked-rectangles list to the viewer without reloading
        /// from storage. Use this when the caller already has the current list in memory
        /// (e.g. immediately after CreateLink returns) to avoid a redundant XML load.
        /// </summary>
        internal void SendLinkedRectanglesToWebView(IList<LinkedRectangle> linkedRectangles)
        {
            if (_disposed) return;
            if (linkedRectangles == null) return;
            try
            {
                PostLinkedRectangles(linkedRectangles);
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine(
                    $"[Talliark] SendLinkedRectanglesToWebView(list) failed: {ex.Message}");
            }
        }

        private void PostLinkedRectangles(IList<LinkedRectangle> linkedRectangles)
        {
            if (_disposed) return;
            if (!_webViewReady) return;
            string json = HostMessageSerializer.BuildLinkedRectanglesLoaded(linkedRectangles);
            _webView.CoreWebView2.PostWebMessageAsString(json);
        }

        private bool SendPdfsToWebView()
        {
            using (TalliarkLog.Time($"SendPdfsToWebView surface={_loadFailureSurfaceName}"))
            {
            if (_disposed || !_webViewReady) return false;
            try
            {
                var store = new TalliarkCustomXmlPartStore(_workbook);
                IList<PdfDocument> pdfs = store.LoadAllPdfsWithBinary();
                IList<PdfFolder> folders = store.LoadContent().Folders;
                string json = HostMessageSerializer.BuildPdfsLoaded(pdfs, folders);
                _webView.CoreWebView2.PostWebMessageAsString(json);
                return true;
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"[Talliark] SendPdfsToWebView failed: {ex.Message}");
                TalliarkLog.Trace($"EXCEPTION {ex.GetType().FullName}: {ex.Message}");
                return false;
            }
            }
        }

        /// <summary>
        /// Pushes the current folder catalogue and every PDF's folder assignment to the viewer.
        /// Sent after file-manager folder mutations so the viewer's folder filter stays in sync
        /// without reloading PDF bytes.
        /// </summary>
        internal void SendFoldersToWebView()
        {
            if (_disposed) return;
            if (!_webViewReady) return;

            try
            {
                TalliarkContent content = new TalliarkCustomXmlPartStore(_workbook).LoadContent();
                string json = HostMessageSerializer.BuildViewerFoldersUpdated(
                    content.Folders, content.Pdfs);
                _webView.CoreWebView2.PostWebMessageAsString(json);
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"[Talliark] SendFoldersToWebView failed: {ex.Message}");
            }
        }

        internal void SendPdfUpdated(string pdfId)
        {
            if (_disposed) return;
            if (!_webViewReady || string.IsNullOrWhiteSpace(pdfId))
                return;

            try
            {
                var store = new TalliarkCustomXmlPartStore(_workbook);
                if (!store.TryGetPdf(pdfId, out PdfDocument pdf))
                    return;

                string json = HostMessageSerializer.BuildPdfUpdated(pdf);
                _webView.CoreWebView2.PostWebMessageAsString(json);
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"[Talliark] SendPdfUpdated failed: {ex.Message}");
            }
        }

        internal void SendPdfAdded(string pdfId)
        {
            if (_disposed) return;
            if (!_webViewReady || string.IsNullOrWhiteSpace(pdfId))
                return;

            try
            {
                var store = new TalliarkCustomXmlPartStore(_workbook);
                if (!store.TryGetPdf(pdfId, out PdfDocument pdf))
                    return;

                string json = HostMessageSerializer.BuildPdfAdded(pdf);
                _webView.CoreWebView2.PostWebMessageAsString(json);
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"[Talliark] SendPdfAdded failed: {ex.Message}");
            }
        }

        internal void SendPdfNameUpdated(string id, string name)
        {
            if (_disposed) return;
            if (!_webViewReady || string.IsNullOrWhiteSpace(id))
                return;

            try
            {
                string json = HostMessageSerializer.BuildPdfNameUpdated(id, name);
                _webView.CoreWebView2.PostWebMessageAsString(json);
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"[Talliark] SendPdfNameUpdated failed: {ex.Message}");
            }
        }

        internal void SendPdfRemoved(string id)
        {
            if (_disposed) return;
            if (!_webViewReady || string.IsNullOrWhiteSpace(id))
                return;

            try
            {
                string json = HostMessageSerializer.BuildPdfRemoved(id);
                _webView.CoreWebView2.PostWebMessageAsString(json);
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"[Talliark] SendPdfRemoved failed: {ex.Message}");
            }
        }

        /// <summary>
        /// Switches the viewer to <paramref name="pdfId"/>. Unlike navigate-to-rectangle
        /// this is never queued: it reflects a transient file-manager selection, and
        /// replaying it once a viewer finally opens would override the document the
        /// viewer picks for itself.
        /// </summary>
        internal void SendShowPdf(string pdfId)
        {
            if (_disposed) return;
            if (!_webViewReady || string.IsNullOrWhiteSpace(pdfId))
                return;

            try
            {
                string json = HostMessageSerializer.BuildShowPdf(pdfId);
                _webView.CoreWebView2.PostWebMessageAsString(json);
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"[Talliark] SendShowPdf failed: {ex.Message}");
            }
        }

        private static string GetWebUiPath()
        {
            string codeBase = Assembly.GetExecutingAssembly().CodeBase;
            string addinDir = Path.GetDirectoryName(new Uri(codeBase).LocalPath)
                ?? AppDomain.CurrentDomain.BaseDirectory;

            return Path.Combine(addinDir, "webui");
        }

        public void Dispose()
        {
            if (_disposed) return;
            _disposed = true;

            TalliarkLog.Trace($"ENTER surface={_loadFailureSurfaceName}");

            try
            {
                _cacheProgress?.Dispose();
                _cacheProgress = null;
            }
            catch (Exception ex)
            {
                TalliarkLog.Trace($"cache progress dispose failed: {ex.Message}");
            }

            try
            {
                _focusRestoreService.Dispose();
            }
            catch (Exception ex)
            {
                TalliarkLog.Trace($"focus restore dispose failed: {ex.Message}");
            }

            try
            {
                _webView.Leave -= OnWebViewLeave;
                if (_webView.CoreWebView2 != null)
                    _webView.CoreWebView2.WebMessageReceived -= OnWebMessageReceived;
            }
            catch (Exception ex)
            {
                TalliarkLog.Trace($"webview event detach failed: {ex.Message}");
            }

            try
            {
                _webView.Dispose();
            }
            catch (Exception ex)
            {
                TalliarkLog.Trace($"webview dispose failed: {ex.Message}");
            }

            try
            {
                _startup?.Dispose();
                _startup = null;
                _surface.Dispose();
            }
            catch (Exception ex)
            {
                TalliarkLog.Trace($"surface dispose failed: {ex.Message}");
            }

            TalliarkLog.Trace($"EXIT surface={_loadFailureSurfaceName}");
        }
    }
}
