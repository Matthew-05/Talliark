using System;

using System.Collections.Generic;

using System.Runtime.InteropServices;

using System.Threading.Tasks;

using System.Windows.Forms;

using Excel = Microsoft.Office.Interop.Excel;

using Office = Microsoft.Office.Core;

using Talliark.Addin.Ribbon;

using Talliark.Addin.Modules.CustomXml;

using Talliark.Addin.Modules.CustomXml.Models;

using Talliark.Addin.Modules.Services;

using Talliark.Addin.Modules.UI;

using Talliark.Addin.Modules.WebView;

using Talliark.Addin.Properties;



namespace Talliark.Addin

{

    public partial class ThisAddIn

    {

        // One entry per open workbook; created on demand when the user opens the task pane.

        private readonly List<WorkbookPaneEntry> _workbookPanes = new List<WorkbookPaneEntry>();

        // Standalone viewers are also workbook-scoped. A workbook may own at most one,
        // while viewers belonging to other open workbooks remain independent and visible.
        private readonly List<WorkbookViewerEntry> _workbookViewers = new List<WorkbookViewerEntry>();

        // File managers follow the same ownership model as standalone viewers: each open
        // workbook may have one independent window, permanently bound to that workbook.
        private readonly List<WorkbookFileManagerEntry> _workbookFileManagers =
            new List<WorkbookFileManagerEntry>();

        private readonly List<WorkbookReconcileEntry> _workbookReconcileWindows =
            new List<WorkbookReconcileEntry>();

        // Linkers are workbook-scoped for the same reason: the wizard reads one
        // workbook's cells and matches them against that workbook's PDFs, so a window may
        // never be re-pointed at another.
        private readonly List<WorkbookLinkerEntry> _workbookLinkers =
            new List<WorkbookLinkerEntry>();

        /// <summary>
        /// Workbooks in most-recently-activated order. Exists only so warm surfaces stop
        /// accumulating: everything outside the newest few loses whatever the user never
        /// opened. Entries are dropped by <see cref="ReconcileClosedWorkbooks"/> along with
        /// everything else belonging to a closed workbook.
        /// </summary>
        private readonly List<Excel.Workbook> _recentlyActivated = new List<Excel.Workbook>();

        private readonly Dictionary<string, WorkbookStorageSession> _storageSessions =
            new Dictionary<string, WorkbookStorageSession>(StringComparer.OrdinalIgnoreCase);

        private readonly Dictionary<string, Dictionary<string, string>> _transientPdfGeometry =
            new Dictionary<string, Dictionary<string, string>>(StringComparer.OrdinalIgnoreCase);

        /// <summary>
        /// Per-workbook link-creation undo history. Keyed and released exactly like
        /// <see cref="_storageSessions"/>, so undo dies with the workbook and is never persisted.
        /// </summary>
        private readonly Dictionary<string, Modules.Services.LinkCreationUndoStack> _linkUndoStacks =
            new Dictionary<string, Modules.Services.LinkCreationUndoStack>(StringComparer.OrdinalIgnoreCase);

        private Modules.Infrastructure.ExcelUndoKeyHook _excelUndoKeyHook;

        private readonly object _automaticUpdateCheckSync = new object();
        private Task _automaticUpdateCheckTask;

        /// <summary>
        /// Excel-grid navigation state for keystrokes forwarded from the viewer. Single
        /// instance because Excel itself tracks only one entry anchor at a time.
        /// </summary>
        private readonly Modules.Services.ExcelCellNavigationService _cellNavigation =
            new Modules.Services.ExcelCellNavigationService();

        internal Modules.Services.ExcelCellNavigationService CellNavigation => _cellNavigation;

        /// <summary>
        /// The cell the user last selected in each workbook. Viewer-initiated link creation
        /// resolves its target from here rather than from <c>Application.Selection</c>, which
        /// answers for the active window and changes without a selection event when another
        /// window of the same workbook is activated. See
        /// <see cref="Modules.Services.SelectionTargetTracker"/>.
        /// </summary>
        private readonly Modules.Services.SelectionTargetTracker _selectionTargets =
            new Modules.Services.SelectionTargetTracker();



        /// <summary>

        /// Set to <c>true</c> before making a programmatic cell selection (e.g.

        /// when navigating from a clicked PDF rectangle to its linked cell) so the

        /// resulting <see cref="Application_SheetSelectionChange"/> event is skipped

        /// and does not bounce a redundant navigate-to-rectangle message back.

        /// The handler resets this flag after reading it.

        /// </summary>

        internal bool SuppressNextSelectionNav { get; set; }

        private int _suppressSelectionNavDepth;

        /// <summary>
        /// When &gt; 0, <see cref="Application_SheetSelectionChange"/> skips viewer
        /// navigation. Used during bulk link deletion so unbind/repaint events do
        /// not post redundant navigate-to-rectangle messages.
        /// </summary>
        internal bool IsSelectionNavSuppressed => _suppressSelectionNavDepth > 0;

        internal SelectionNavSuppressScope EnterSelectionNavSuppress() =>
            new SelectionNavSuppressScope(this);

        internal sealed class SelectionNavSuppressScope : IDisposable
        {
            private readonly ThisAddIn _addIn;

            internal SelectionNavSuppressScope(ThisAddIn addIn)
            {
                _addIn = addIn;
                _addIn._suppressSelectionNavDepth++;
            }

            public void Dispose()
            {
                _addIn._suppressSelectionNavDepth--;
            }
        }

        private int _suppressActivationTargetDepth;

        /// <summary>
        /// When &gt; 0, <see cref="Application_SheetActivate"/> does not record the activated
        /// sheet as the link target. Entered for the duration of a viewer command, where any
        /// activation is programmatic — including another add-in's reaction to the workbook
        /// or target sheet being activated (CCH's ePace re-activates its own sheet), which
        /// would otherwise point the next link at the sheet the command landed on.
        /// </summary>
        internal bool IsActivationTargetSuppressed => _suppressActivationTargetDepth > 0;

        internal ActivationTargetSuppressScope EnterActivationTargetSuppress() =>
            new ActivationTargetSuppressScope(this);

        internal sealed class ActivationTargetSuppressScope : IDisposable
        {
            private readonly ThisAddIn _addIn;

            internal ActivationTargetSuppressScope(ThisAddIn addIn)
            {
                _addIn = addIn;
                _addIn._suppressActivationTargetDepth++;
            }

            public void Dispose()
            {
                _addIn._suppressActivationTargetDepth--;
            }
        }

        internal bool IsViewerPoppedOut =>
            IsViewerPoppedOutFor(Application?.ActiveWorkbook);

        /// <summary>
        /// Controls whether linked-cell selection opens the task-pane viewer. This is
        /// intentionally session-only and resets to enabled each time the add-in starts;
        /// do not load it from or save it to application settings.
        /// </summary>
        internal bool AutoOpenViewerOnCellClick { get; set; } = true;



        internal bool IsTaskPaneViewerVisible()

        {

            if (IsViewerPoppedOut) return false;

            var entry = FindEntryForActiveWorkbook();

            return entry != null && entry.Pane.Visible;

        }



        internal WorkbookStorageSession GetStorageSession(Excel.Workbook workbook)

        {

            if (workbook == null) throw new ArgumentNullException(nameof(workbook));

            string key = GetWorkbookSessionKey(workbook);

            if (!_storageSessions.TryGetValue(key, out WorkbookStorageSession session))

            {

                session = new WorkbookStorageSession(workbook);

                _storageSessions[key] = session;

            }

            return session;

        }



        internal void ReleaseStorageSession(Excel.Workbook workbook)

        {

            if (workbook == null) return;

            string key = GetWorkbookSessionKey(workbook);
            _storageSessions.Remove(key);
            _transientPdfGeometry.Remove(key);
            _linkUndoStacks.Remove(key);
            RefreshExcelUndoArmedState();

        }

        /// <summary>
        /// Returns the workbook's in-memory link-creation undo stack, creating it on first use.
        /// </summary>
        internal Modules.Services.LinkCreationUndoStack GetLinkUndoStack(Excel.Workbook workbook)
        {
            if (workbook == null) return null;

            string key = GetWorkbookSessionKey(workbook);

            if (!_linkUndoStacks.TryGetValue(key, out Modules.Services.LinkCreationUndoStack stack))
            {
                stack = new Modules.Services.LinkCreationUndoStack();
                _linkUndoStacks[key] = stack;
            }

            return stack;
        }

        /// <summary>
        /// Looks up a workbook's undo stack without creating one, so callers that only want to
        /// inspect state do not populate the dictionary for every workbook Excel touches.
        /// </summary>
        private Modules.Services.LinkCreationUndoStack TryGetLinkUndoStack(Excel.Workbook workbook)
        {
            if (workbook == null) return null;

            return _linkUndoStacks.TryGetValue(
                GetWorkbookSessionKey(workbook), out Modules.Services.LinkCreationUndoStack stack)
                ? stack
                : null;
        }

        /// <summary>
        /// Marks a link-rectangle creation as the most recent action, so the next Ctrl+Z on the
        /// worksheet grid reaches Talliark instead of Excel's own undo.
        /// </summary>
        internal void ArmExcelUndoForLinkCreation() => RefreshExcelUndoArmedState();

        /// <summary>
        /// Invalidates link-creation undo after a persisted Talliark mutation. Creation and
        /// successful undo call this indirectly while writing storage, then explicitly re-arm
        /// only after their complete operation has succeeded.
        /// </summary>
        internal void DisarmLinkCreationUndo(Excel.Workbook workbook)
        {
            try
            {
                TryGetLinkUndoStack(workbook)?.Disarm();
            }
            catch (Exception ex)
            {
                Modules.TalliarkLog.Trace(
                    $"DisarmLinkCreationUndo failed: {ex.Message}");
            }

            RefreshExcelUndoArmedState();
        }

        /// <summary>
        /// Points the grid's Ctrl+Z at Talliark only when the workbook the user is actually
        /// looking at has undoable history that is still the most recent thing to happen in it.
        /// </summary>
        /// <remarks>
        /// <para>
        /// Recomputed from the active workbook rather than latched, because arming is per workbook
        /// while the hook is one global switch. Creating a rectangle in one workbook must not leave
        /// the hook armed over another workbook's empty stack, and returning to the first workbook
        /// must restore its pending undo.
        /// </para>
        /// <para>
        /// Resolving the active workbook here, on the UI thread, is also what keeps COM calls out
        /// of the keyboard hook procedure, where Excel may refuse them mid-message.
        /// </para>
        /// </remarks>
        private void RefreshExcelUndoArmedState()
        {
            if (_excelUndoKeyHook == null) return;

            bool armed = false;

            try
            {
                Modules.Services.LinkCreationUndoStack stack =
                    TryGetLinkUndoStack(Application?.ActiveWorkbook);

                armed = stack != null && stack.IsArmed && !stack.IsEmpty;
            }
            catch (Exception ex)
            {
                Modules.TalliarkLog.Trace($"RefreshExcelUndoArmedState failed: {ex.Message}");
            }

            if (armed)
                _excelUndoKeyHook.Arm();
            else
                _excelUndoKeyHook.Disarm();
        }

        /// <summary>
        /// Routes Ctrl+Z to the actual most recent undo target. Excel's native stack has
        /// priority because it contains any worksheet action performed after Talliark's
        /// programmatic link write, including actions such as row/column sizing that raise no
        /// SheetChange event. Link creation is reversed only when Excel has nothing newer.
        /// </summary>
        internal void UndoMostRecentAction()
        {
            if (!TryGetNativeExcelUndoState(
                out Office.CommandBars commandBars,
                out bool nativeUndoAvailable))
            {
                // Uncertainty must never cost the user a rectangle. Leave both histories
                // untouched so a later Ctrl+Z can retry when Excel is responsive.
                Modules.TalliarkLog.Trace(
                    "UndoMostRecentAction: native Excel undo state unavailable – doing nothing");
                return;
            }

            if (nativeUndoAvailable)
            {
                try
                {
                    // Execute the built-in control rather than Application.Undo(), whose
                    // contract requires it to be the first operation in a macro. We already
                    // queried the command state to decide which undo history owns Ctrl+Z.
                    commandBars.ExecuteMso("Undo");
                }
                catch (Exception ex)
                {
                    // Never fall through to rectangle undo after Excel said it owned the
                    // keystroke. A failed native undo is safer than undoing the wrong action.
                    Modules.TalliarkLog.Trace(
                        $"UndoMostRecentAction: Excel undo failed: {ex.Message}");
                }

                RefreshExcelUndoArmedState();
                return;
            }

            UndoLastLinkCreation();
        }

        /// <summary>
        /// Reads the live state of Excel's Undo command. Unlike worksheet events, the command
        /// covers every native undoable action, including formatting and dimension changes.
        /// </summary>
        private bool TryGetNativeExcelUndoState(
            out Office.CommandBars commandBars,
            out bool available)
        {
            commandBars = null;
            available = false;

            try
            {
                commandBars = Application?.CommandBars as Office.CommandBars;
                if (commandBars == null) return false;

                available = commandBars.GetEnabledMso("Undo");
                return true;
            }
            catch (Exception ex)
            {
                Modules.TalliarkLog.Trace(
                    $"TryGetNativeExcelUndoState failed: {ex.Message}");
                return false;
            }
        }

        /// <summary>
        /// Runs one eligible step of link-creation undo and re-arms the grid keystroke when
        /// more history remains, so repeated Ctrl+Z can walk back through consecutive creates.
        /// </summary>
        internal string UndoLastLinkCreation()
        {
            Excel.Workbook wb = Application?.ActiveWorkbook;
            if (wb == null) return null;

            Modules.Services.LinkCreationUndoStack stack = TryGetLinkUndoStack(wb);
            if (stack == null || !stack.IsArmed || stack.IsEmpty)
            {
                RefreshExcelUndoArmedState();
                return null;
            }

            string removedId = null;

            try
            {
                removedId = new Modules.Services.UndoLinkCreationService().UndoLast(wb);
            }
            catch (Exception ex)
            {
                Modules.TalliarkLog.Trace($"UndoLastLinkCreation failed: {ex.Message}");
            }

            // Undoing is itself a Talliark action, so re-arm explicitly rather than relying on the
            // flag having survived: the reversal's own cell writes, and the pop that consumed the
            // entry, both leave it stale. Without this the chain stops after one Ctrl+Z.
            if (removedId != null)
                stack?.Arm();
            else
                stack?.Disarm();

            RefreshExcelUndoArmedState();

            if (removedId != null)
            {
                _cellNavigation.ResetAnchor();
                GetViewerHostFor(wb)?.SendLinkRectanglesRemoved(new List<string> { removedId });
                NotifyFileManagerLinksChanged(wb);
            }

            return removedId;
        }

        internal bool TryGetTransientPdfGeometry(Excel.Workbook workbook, string pdfId, out string geometryBase64)
        {
            geometryBase64 = null;
            if (workbook == null || string.IsNullOrWhiteSpace(pdfId)) return false;

            string key = GetWorkbookSessionKey(workbook);
            return _transientPdfGeometry.TryGetValue(key, out var byPdf)
                && byPdf.TryGetValue(pdfId, out geometryBase64)
                && !string.IsNullOrWhiteSpace(geometryBase64);
        }

        internal void StoreTransientPdfGeometry(Excel.Workbook workbook, string pdfId, string geometryBase64)
        {
            if (workbook == null || string.IsNullOrWhiteSpace(pdfId) || string.IsNullOrWhiteSpace(geometryBase64))
                return;

            string key = GetWorkbookSessionKey(workbook);
            if (!_transientPdfGeometry.TryGetValue(key, out var byPdf))
            {
                byPdf = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
                _transientPdfGeometry[key] = byPdf;
            }

            byPdf[pdfId] = geometryBase64;
        }



        private static string GetWorkbookDebugName(Excel.Workbook workbook)

        {

            if (workbook == null) return "(null)";

            try

            {

                if (!string.IsNullOrEmpty(workbook.FullName))

                    return workbook.FullName;

            }

            catch (COMException ex)

            {

                Modules.TalliarkLog.Trace($"FullName unavailable: {ex.Message}");

            }

            try

            {

                return workbook.Name ?? "(unnamed)";

            }

            catch (COMException ex)

            {

                Modules.TalliarkLog.Trace($"Name unavailable: {ex.Message}");

                return "(workbook COM unavailable)";

            }

        }



        private static string GetWorkbookSessionKey(Excel.Workbook workbook)

        {

            try

            {

                if (!string.IsNullOrEmpty(workbook.FullName))

                    return workbook.FullName;

            }

            catch (COMException) { }

            IntPtr unknown = Marshal.GetIUnknownForObject(workbook);
            try
            {
                return "unsaved:" + ((IntPtr)Marshal.GetUniqueObjectForIUnknown(unknown)).ToInt64().ToString();
            }
            finally
            {
                Marshal.Release(unknown);
            }
        }



        internal void ShowTaskPane()

        {

            Excel.Workbook wb = Application?.ActiveWorkbook;
            WorkbookViewerEntry viewerEntry = FindViewerEntryFor(wb);
            if (viewerEntry != null && !viewerEntry.Window.IsDisposed)
                viewerEntry.Window.Close();



            var entry = EnsureTaskPaneForActiveWorkbook();

            if (entry == null) return;

            entry.WasShown = true;

            entry.Pane.Visible = true;

            entry.Host.NotifyViewerShown();

            entry.Host.SendSearchQuery(
                GetActiveCellDisplayText(Application?.Selection as Excel.Range));

        }

        /// <summary>
        /// Activates <paramref name="workbook"/>, opens its task-pane viewer, and switches
        /// the viewer to <paramref name="pdfId"/> once its initial catalogue is ready.
        /// </summary>
        internal void OpenPdfInTaskPane(Excel.Workbook workbook, string pdfId)
        {
            if (workbook == null || string.IsNullOrWhiteSpace(pdfId)) return;

            try
            {
                if (!IsSameWorkbook(workbook, Application?.ActiveWorkbook))
                    workbook.Activate();

                if (!IsSameWorkbook(workbook, Application?.ActiveWorkbook))
                    return;

                ShowTaskPane();
                FindEntryFor(workbook)?.Host.SendShowPdfWhenReady(pdfId);
            }
            catch (Exception ex)
            {
                Modules.TalliarkLog.Trace(
                    $"OpenPdfInTaskPane failed: {ex.GetType().FullName}: {ex.Message}");
            }
        }



        internal void ShowViewerWindow()

        {
            ReconcileClosedWorkbooks();

            Excel.Workbook wb = Application?.ActiveWorkbook;
            if (wb == null) return;

            WorkbookViewerEntry entry = EnsureViewerWindowFor(wb);
            entry.WasShown = true;
            entry.Window.InvalidateData();

            HideTaskPaneFor(wb);

            ShowAndActivateWindow(entry.Window);

            entry.Window.NotifyViewerShown();

            entry.Window.SendSearchQuery(
                GetActiveCellDisplayText(Application?.Selection as Excel.Range));

        }



        /// <summary>

        /// Pushes the current workbook's PDFs and linked rectangles to the active

        /// viewer surface. No-ops if no viewer is open yet.

        /// </summary>

        internal void RefreshTaskPanePdfs()

        {

            GetActiveViewerHost()?.RefreshDataIfReady();

        }



        /// <summary>

        /// Pushes updated bytes for a single PDF to the active viewer surface.

        /// Used after OCR so an already-loaded document is refreshed immediately.

        /// </summary>

        internal void RefreshTaskPanePdf(Excel.Workbook workbook, string pdfId)

        {

            GetViewerHostFor(workbook)?.SendPdfUpdated(pdfId);

        }

        internal void NotifyViewerPdfAdded(Excel.Workbook workbook, string pdfId)

        {

            GetViewerHostFor(workbook)?.SendPdfAdded(pdfId);

        }

        internal void NotifyViewerPdfRenamed(Excel.Workbook workbook, string id, string name)

        {

            GetViewerHostFor(workbook)?.SendPdfNameUpdated(id, name);

        }

        internal void NotifyViewerPdfRemoved(Excel.Workbook workbook, string id)

        {

            GetViewerHostFor(workbook)?.SendPdfRemoved(id);

        }

        /// <summary>

        /// Switches an open viewer surface to a specific PDF. Raised when the user

        /// selects a document in the file manager. No-ops when no viewer is open —

        /// the file manager is usable on its own and must not force one open.

        /// </summary>

        internal void NotifyViewerShowPdf(Excel.Workbook workbook, string pdfId)

        {

            GetViewerHostFor(workbook)?.SendShowPdf(pdfId);

        }



        /// <summary>

        /// Pushes the current folder catalogue and PDF folder assignments to the active viewer.

        /// Called after file-manager folder or move operations so the viewer's folder filter stays current.

        /// </summary>

        internal void NotifyViewerFoldersChanged(Excel.Workbook workbook)

        {

            GetViewerHostFor(workbook)?.SendFoldersToWebView();

        }



        /// <summary>
        /// Rebuilds a workbook's viewer overlays after its links change. Takes the workbook
        /// explicitly because the linker can finish a run after focus has moved elsewhere,
        /// and the new rectangles belong to the workbook that was linked, not the active one.
        /// </summary>
        internal void NotifyViewerLinksChanged(Excel.Workbook workbook)
        {
            IDocumentViewerHost host = GetViewerHostFor(workbook);
            if (host == null) return;

            host.InvalidateData();
            host.RefreshDataIfReady();
        }

        internal void ShowManageFilesWindow()

        {
            ShowManageFilesWindow(Application?.ActiveWorkbook);
        }

        internal void ShowManageFilesWindow(Excel.Workbook workbook)
        {
            ReconcileClosedWorkbooks();
            if (workbook == null) return;

            WorkbookFileManagerEntry entry = EnsureFileManagerFor(workbook);
            entry.WasShown = true;
            ShowAndActivateWindow(entry.Window);
            entry.Window.RefreshDataIfReady();

        }

        internal void ShowReconcileWindow()
        {
            ReconcileClosedWorkbooks();
            Excel.Workbook workbook = Application?.ActiveWorkbook;
            if (workbook == null) return;

            if (!AppVersion.IsDevelopment)
            {
                MessageBox.Show(
                    "Reconcile is in development. Use at your own risk. If you encounter any issues please report it to me.",
                    "Talliark – Reconcile",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Information);
            }

            WorkbookReconcileEntry entry = EnsureReconcileFor(workbook);
            entry.WasShown = true;
            ShowAndActivateWindow(entry.Window);
            entry.Window.RefreshDataIfReady();
        }



        internal void ShowDocumentLinkerWindow()
        {
            ShowDocumentLinkerWindow(Application?.ActiveWorkbook);
        }

        internal void ShowDocumentLinkerWindow(Excel.Workbook workbook)
        {
            ReconcileClosedWorkbooks();
            if (workbook == null) return;

            WorkbookLinkerEntry entry = EnsureLinkerFor(workbook);
            entry.WasShown = true;

            // Reset re-arms an already-initialised web view. A window built just now has
            // not loaded its UI yet and posts linker-app-ready itself once it does, so the
            // call is a no-op in that case rather than a second, competing hand-off.
            entry.Window.Reset();
            ShowAndActivateWindow(entry.Window);
        }

        /// <summary>
        /// Shows an existing workbook window and restores it when its taskbar button was
        /// minimized. Leaves normal and maximized windows in their current state.
        /// </summary>
        private static void ShowAndActivateWindow(Form window)
        {
            if (window.WindowState == FormWindowState.Minimized)
                window.WindowState = FormWindowState.Normal;

            window.Show();
            window.BringToFront();
            window.Activate();
        }



        internal void NotifyFileManagerLinksChanged(Excel.Workbook workbook)
        {
            WorkbookFileManagerEntry entry = FindFileManagerEntryFor(workbook);
            if (entry != null && !entry.Window.IsDisposed)
                entry.Window.RefreshDataIfReady();
        }






        internal IDocumentViewerHost GetActiveViewerHost()

        {
            return GetViewerHostFor(Application?.ActiveWorkbook);
        }

        private IDocumentViewerHost GetViewerHostFor(Excel.Workbook workbook)
        {
            WorkbookViewerEntry viewerEntry = FindViewerEntryFor(workbook);
            if (viewerEntry != null
                && !viewerEntry.Window.IsDisposed
                && viewerEntry.Window.Visible)
                return viewerEntry.Window;

            return FindEntryFor(workbook)?.Host;

        }

        internal void CloseAllApplicationWindows()
        {
            foreach (WorkbookFileManagerEntry entry in _workbookFileManagers.ToArray())
            {
                if (!entry.Window.IsDisposed)
                    entry.Window.Close();
            }
            foreach (WorkbookViewerEntry entry in _workbookViewers.ToArray())
            {
                if (!entry.Window.IsDisposed)
                    entry.Window.Close();
            }
            foreach (WorkbookLinkerEntry entry in _workbookLinkers.ToArray())
            {
                if (!entry.Window.IsDisposed)
                    entry.Window.Close();
            }
        }



        // ── Per-workbook task pane management ────────────────────────────────
        private WorkbookPaneEntry EnsureTaskPaneForActiveWorkbook()
        {
            // Clear out anything left by a workbook that has since closed, so a stale entry
            // cannot cause a second pane to be built for the active workbook.
            ReconcileClosedWorkbooks();

            Excel.Workbook wb = Application?.ActiveWorkbook;
            if (wb == null) return null;

            var entry = FindEntryFor(wb);
            if (entry != null) return entry;

            // Passing the active window scopes the pane to this workbook's window, so Excel
            // shows and hides it automatically when the user switches workbooks.
            //
            // ActiveWindow rather than the workbook's Windows[1], deliberately: a workbook
            // can have several windows (View > New Window), and the pane belongs in the one
            // the user is looking at. That only holds because panes are built for the active
            // workbook and nothing else — the moment anything pre-creates a pane for a
            // background workbook, this line attaches it to the wrong window.
            Excel.Window window = Application?.ActiveWindow;
            if (window == null)
            {
                Modules.TalliarkLog.Trace("EnsureTaskPaneForActiveWorkbook: no active window");
                return null;
            }

            var host = new TaskPaneHost(wb);
            var pane = CustomTaskPanes.Add(host, "Talliark", window);
            pane.DockPosition = Office.MsoCTPDockPosition.msoCTPDockPositionRight;
            pane.Width = 640;

            entry = new WorkbookPaneEntry(wb, pane, host);

            pane.VisibleChanged += (_, __) =>
            {
                if (!pane.Visible) return;

                if (IsViewerPoppedOutFor(wb))
                {
                    pane.Visible = false;
                    return;
                }

                // Excel shows the pane on its own when the user toggles it from the ribbon's
                // task-pane list, which never goes through ShowTaskPane. Recording it here as
                // well is what stops eviction treating a pane the user is looking at as
                // unopened.
                entry.WasShown = true;
            };

            _workbookPanes.Add(entry);
            return entry;
        }



        private WorkbookViewerEntry EnsureViewerWindowFor(Excel.Workbook workbook)
        {
            WorkbookViewerEntry entry = FindViewerEntryFor(workbook);
            if (entry != null && !entry.Window.IsDisposed)
                return entry;

            if (entry != null)
                _workbookViewers.Remove(entry);

            var window = new ViewerWindowHost(workbook);
            entry = new WorkbookViewerEntry(workbook, window);
            _workbookViewers.Add(entry);
            return entry;
        }

        private WorkbookFileManagerEntry EnsureFileManagerFor(Excel.Workbook workbook)
        {
            WorkbookFileManagerEntry entry = FindFileManagerEntryFor(workbook);
            if (entry != null && !entry.Window.IsDisposed)
                return entry;

            if (entry != null)
                _workbookFileManagers.Remove(entry);

            var window = new FileManagerHost(workbook);
            entry = new WorkbookFileManagerEntry(workbook, window);
            _workbookFileManagers.Add(entry);
            return entry;
        }

        private WorkbookReconcileEntry EnsureReconcileFor(Excel.Workbook workbook)
        {
            WorkbookReconcileEntry entry = FindReconcileEntryFor(workbook);
            if (entry != null && !entry.Window.IsDisposed) return entry;
            if (entry != null) _workbookReconcileWindows.Remove(entry);

            entry = new WorkbookReconcileEntry(workbook, new ReconcileHost(workbook));
            _workbookReconcileWindows.Add(entry);
            return entry;
        }

        private WorkbookLinkerEntry EnsureLinkerFor(Excel.Workbook workbook)
        {
            WorkbookLinkerEntry entry = FindLinkerEntryFor(workbook);
            if (entry != null && !entry.Window.IsDisposed)
                return entry;

            if (entry != null)
                _workbookLinkers.Remove(entry);

            var window = new DocumentLinkerHost(workbook);
            entry = new WorkbookLinkerEntry(workbook, window);
            _workbookLinkers.Add(entry);
            return entry;
        }

        private void HideTaskPaneFor(Excel.Workbook workbook)
        {
            WorkbookPaneEntry entry = FindEntryFor(workbook);
            if (entry == null) return;

            try
            {
                if (entry.Pane.Visible)
                    entry.Pane.Visible = false;
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine(
                    $"[Talliark] HideTaskPaneFor failed: {ex.Message}");
            }
        }



        private WorkbookPaneEntry FindEntryForActiveWorkbook()

        {

            Excel.Workbook wb = Application?.ActiveWorkbook;

            return wb == null ? null : FindEntryFor(wb);

        }

        private bool IsViewerPoppedOutFor(Excel.Workbook workbook)
        {
            WorkbookViewerEntry entry = FindViewerEntryFor(workbook);
            return entry != null && !entry.Window.IsDisposed && entry.Window.Visible;
        }



        /// <summary>

        /// Finds the pane entry for a specific workbook using COM identity comparison

        /// so that multiple RCW wrappers for the same COM object resolve correctly.

        /// </summary>

        private WorkbookPaneEntry FindEntryFor(Excel.Workbook wb)

        {

            if (wb == null) return null;



            IntPtr target = IntPtr.Zero;

            try

            {

                target = Marshal.GetIUnknownForObject(wb);

                foreach (var entry in _workbookPanes)

                {

                    IntPtr candidate = IntPtr.Zero;

                    try

                    {

                        candidate = Marshal.GetIUnknownForObject(entry.Workbook);

                        if (candidate == target) return entry;

                    }

                    catch (Exception ex)

                    {

                        // A workbook that closed leaves an unusable wrapper behind, and entries
                        // now survive until ReconcileClosedWorkbooks sweeps them. Skip rather
                        // than let a dead entry break the lookup for a live workbook.
                        Modules.TalliarkLog.Trace($"FindEntryFor skipping unusable entry: {ex.Message}");

                    }

                    finally

                    {

                        if (candidate != IntPtr.Zero) Marshal.Release(candidate);

                    }

                }

            }

            finally

            {

                if (target != IntPtr.Zero) Marshal.Release(target);

            }



            return null;

        }



        /// <summary>
        /// Applies the Development toggle to every open viewer surface so the overlay
        /// appears or clears without reopening the workbook. Viewers created later pick
        /// the state up from their controller when their web context reports ready.
        /// </summary>
        private void OnCharBoundingBoxesChanged(object sender, bool visible)
        {
            foreach (WorkbookPaneEntry entry in _workbookPanes.ToArray())
            {
                try
                {
                    entry.Host?.SendCharBboxesVisible(visible);
                }
                catch (Exception ex)
                {
                    Modules.TalliarkLog.Trace(
                        $"OnCharBoundingBoxesChanged skipping pane: {ex.Message}");
                }
            }

            foreach (WorkbookViewerEntry entry in _workbookViewers.ToArray())
            {
                try
                {
                    if (entry.Window != null && !entry.Window.IsDisposed)
                        entry.Window.SendCharBboxesVisible(visible);
                }
                catch (Exception ex)
                {
                    Modules.TalliarkLog.Trace(
                        $"OnCharBoundingBoxesChanged skipping viewer window: {ex.Message}");
                }
            }
        }

        private void OnExperimentalTableDetectionChanged(object sender, bool enabled)
        {
            foreach (WorkbookPaneEntry entry in _workbookPanes.ToArray())
            {
                try { entry.Host?.SendTableDetectionEnabled(enabled); }
                catch (Exception ex) { Modules.TalliarkLog.Trace("Table detection update skipped pane: " + ex.Message); }
            }
            foreach (WorkbookViewerEntry entry in _workbookViewers.ToArray())
            {
                try { if (entry.Window != null && !entry.Window.IsDisposed) entry.Window.SendTableDetectionEnabled(enabled); }
                catch (Exception ex) { Modules.TalliarkLog.Trace("Table detection update skipped viewer: " + ex.Message); }
            }
        }


        private void OnValuesChanged(object sender, bool visible)
        {
            foreach (WorkbookPaneEntry entry in _workbookPanes.ToArray())
            {
                try { entry.Host?.SendValuesVisible(visible); }
                catch (Exception ex)
                {
                    Modules.TalliarkLog.Trace($"OnValuesChanged skipping pane: {ex.Message}");
                }
            }

            foreach (WorkbookViewerEntry entry in _workbookViewers.ToArray())
            {
                try
                {
                    if (entry.Window != null && !entry.Window.IsDisposed)
                        entry.Window.SendValuesVisible(visible);
                }
                catch (Exception ex)
                {
                    Modules.TalliarkLog.Trace($"OnValuesChanged skipping viewer window: {ex.Message}");
                }
            }
        }

        private void OnReferencesChanged(object sender, bool visible)
        {
            foreach (WorkbookPaneEntry entry in _workbookPanes.ToArray())
            {
                try { entry.Host?.SendReferencesVisible(visible); }
                catch (Exception ex)
                {
                    Modules.TalliarkLog.Trace($"OnReferencesChanged skipping pane: {ex.Message}");
                }
            }

            foreach (WorkbookViewerEntry entry in _workbookViewers.ToArray())
            {
                try
                {
                    if (entry.Window != null && !entry.Window.IsDisposed)
                        entry.Window.SendReferencesVisible(visible);
                }
                catch (Exception ex)
                {
                    Modules.TalliarkLog.Trace($"OnReferencesChanged skipping viewer window: {ex.Message}");
                }
            }
        }

        private void OnStructureChanged(object sender, bool visible)
        {
            foreach (WorkbookPaneEntry entry in _workbookPanes.ToArray())
            {
                try { entry.Host?.SendStructureVisible(visible); }
                catch (Exception ex)
                {
                    Modules.TalliarkLog.Trace($"OnStructureChanged skipping pane: {ex.Message}");
                }
            }

            foreach (WorkbookViewerEntry entry in _workbookViewers.ToArray())
            {
                try
                {
                    if (entry.Window != null && !entry.Window.IsDisposed)
                        entry.Window.SendStructureVisible(visible);
                }
                catch (Exception ex)
                {
                    Modules.TalliarkLog.Trace($"OnStructureChanged skipping viewer window: {ex.Message}");
                }
            }
        }

        private void OnValueNoiseChanged(object sender, bool visible)
        {
            foreach (WorkbookPaneEntry entry in _workbookPanes.ToArray())
            {
                try { entry.Host?.SendValueNoiseVisible(visible); }
                catch (Exception ex)
                {
                    Modules.TalliarkLog.Trace($"OnValueNoiseChanged skipping pane: {ex.Message}");
                }
            }

            foreach (WorkbookViewerEntry entry in _workbookViewers.ToArray())
            {
                try
                {
                    if (entry.Window != null && !entry.Window.IsDisposed)
                        entry.Window.SendValueNoiseVisible(visible);
                }
                catch (Exception ex)
                {
                    Modules.TalliarkLog.Trace($"OnValueNoiseChanged skipping viewer window: {ex.Message}");
                }
            }
        }

        /// <summary>Finds the standalone viewer owned by a workbook using COM identity.</summary>
        private WorkbookViewerEntry FindViewerEntryFor(Excel.Workbook wb)
        {
            if (wb == null) return null;

            IntPtr target = IntPtr.Zero;
            try
            {
                target = Marshal.GetIUnknownForObject(wb);
                foreach (WorkbookViewerEntry entry in _workbookViewers)
                {
                    IntPtr candidate = IntPtr.Zero;
                    try
                    {
                        candidate = Marshal.GetIUnknownForObject(entry.Workbook);
                        if (candidate == target) return entry;
                    }
                    catch (Exception ex)
                    {
                        Modules.TalliarkLog.Trace(
                            $"FindViewerEntryFor skipping unusable entry: {ex.Message}");
                    }
                    finally
                    {
                        if (candidate != IntPtr.Zero) Marshal.Release(candidate);
                    }
                }
            }
            finally
            {
                if (target != IntPtr.Zero) Marshal.Release(target);
            }

            return null;
        }

        /// <summary>Finds the document linker owned by a workbook using COM identity.</summary>
        private WorkbookLinkerEntry FindLinkerEntryFor(Excel.Workbook wb)
        {
            if (wb == null) return null;

            IntPtr target = IntPtr.Zero;
            try
            {
                target = Marshal.GetIUnknownForObject(wb);
                foreach (WorkbookLinkerEntry entry in _workbookLinkers)
                {
                    IntPtr candidate = IntPtr.Zero;
                    try
                    {
                        candidate = Marshal.GetIUnknownForObject(entry.Workbook);
                        if (candidate == target) return entry;
                    }
                    catch (Exception ex)
                    {
                        Modules.TalliarkLog.Trace(
                            $"FindLinkerEntryFor skipping unusable entry: {ex.Message}");
                    }
                    finally
                    {
                        if (candidate != IntPtr.Zero) Marshal.Release(candidate);
                    }
                }
            }
            finally
            {
                if (target != IntPtr.Zero) Marshal.Release(target);
            }

            return null;
        }

        /// <summary>Finds the file manager owned by a workbook using COM identity.</summary>
        private WorkbookFileManagerEntry FindFileManagerEntryFor(Excel.Workbook wb)
        {
            if (wb == null) return null;

            IntPtr target = IntPtr.Zero;
            try
            {
                target = Marshal.GetIUnknownForObject(wb);
                foreach (WorkbookFileManagerEntry entry in _workbookFileManagers)
                {
                    IntPtr candidate = IntPtr.Zero;
                    try
                    {
                        candidate = Marshal.GetIUnknownForObject(entry.Workbook);
                        if (candidate == target) return entry;
                    }
                    catch (Exception ex)
                    {
                        Modules.TalliarkLog.Trace(
                            $"FindFileManagerEntryFor skipping unusable entry: {ex.Message}");
                    }
                    finally
                    {
                        if (candidate != IntPtr.Zero) Marshal.Release(candidate);
                    }
                }
            }
            finally
            {
                if (target != IntPtr.Zero) Marshal.Release(target);
            }

            return null;
        }

        private WorkbookReconcileEntry FindReconcileEntryFor(Excel.Workbook wb)
        {
            if (wb == null) return null;
            foreach (WorkbookReconcileEntry entry in _workbookReconcileWindows)
                if (IsSameWorkbook(wb, entry.Workbook)) return entry;
            return null;
        }

        // ── Event handlers ────────────────────────────────────────────────────



        private void ThisAddIn_Startup(object sender, System.EventArgs e)

        {

            Modules.TalliarkLog.StartSession();

            Modules.TalliarkLog.Trace("addin startup");

            // Last-resort handlers first: from here on, an exception the add-in failed to catch
            // is logged rather than left to unwind into Excel, which disables add-ins that do.

            Modules.Infrastructure.AddinExceptionGuard.Install();

            // Warm-up is guarded on its own so a WebView2 failure cannot skip the event wiring
            // below — an add-in that loads without surfaces is recoverable, one that throws out
            // of Startup is not.

            try

            {

                WebViewEagerLoader.Initialize(this);

            }

            catch (Exception ex)

            {

                Modules.TalliarkLog.Trace($"eager load failed: {ex}");

            }

            try

            {

                Application.SheetSelectionChange += Application_SheetSelectionChange;

                Application.SheetActivate += Application_SheetActivate;

                Application.SheetChange += Application_SheetChange;

                Application.WorkbookBeforeClose += Application_WorkbookBeforeClose;

                Application.WorkbookBeforeSave += Application_WorkbookBeforeSave;

                Application.WorkbookActivate += Application_WorkbookActivate;

                Application.WorkbookOpen += Application_WorkbookOpen;

                ((Excel.AppEvents_Event)Application).NewWorkbook += Application_NewWorkbook;

                EnsureLinkTracking(Application.ActiveWorkbook);

                _excelUndoKeyHook = new Modules.Infrastructure.ExcelUndoKeyHook(
                    () => UndoMostRecentAction());

                Modules.Infrastructure.DevSettings.CharBoundingBoxesChanged +=
                    OnCharBoundingBoxesChanged;
                Modules.Infrastructure.DevSettings.ValuesChanged += OnValuesChanged;
                Modules.Infrastructure.DevSettings.ReferencesChanged += OnReferencesChanged;
                Modules.Infrastructure.DevSettings.StructureChanged += OnStructureChanged;
                Modules.Infrastructure.DevSettings.ValueNoiseChanged += OnValueNoiseChanged;
                Modules.Infrastructure.ExperimentalSettings.TableDetectionChanged +=
                    OnExperimentalTableDetectionChanged;

                _ = CheckForUpdateOnOpenAsync();

            }

            catch (Exception ex)

            {

                Modules.TalliarkLog.Trace($"addin startup failed: {ex}");

            }

        }



        private void ThisAddIn_Shutdown(object sender, System.EventArgs e)

        {

            Modules.TalliarkLog.Trace("ENTER");

            try

            {

                DisposeApplicationSurfacesForShutdown();

                _excelUndoKeyHook?.Dispose();

                _excelUndoKeyHook = null;

            }

            catch (Exception ex)

            {

                Modules.TalliarkLog.Trace($"addin shutdown cleanup failed: {ex}");

            }

            try

            {

                Modules.Infrastructure.DevSettings.CharBoundingBoxesChanged -=
                    OnCharBoundingBoxesChanged;
                Modules.Infrastructure.DevSettings.ValuesChanged -= OnValuesChanged;
                Modules.Infrastructure.DevSettings.ReferencesChanged -= OnReferencesChanged;
                Modules.Infrastructure.DevSettings.StructureChanged -= OnStructureChanged;
                Modules.Infrastructure.DevSettings.ValueNoiseChanged -= OnValueNoiseChanged;
                Modules.Infrastructure.ExperimentalSettings.TableDetectionChanged -=
                    OnExperimentalTableDetectionChanged;

                Application.SheetSelectionChange -= Application_SheetSelectionChange;

                Application.SheetActivate -= Application_SheetActivate;

                Application.SheetChange -= Application_SheetChange;

                Application.WorkbookBeforeClose -= Application_WorkbookBeforeClose;

                Application.WorkbookBeforeSave -= Application_WorkbookBeforeSave;

                Application.WorkbookActivate -= Application_WorkbookActivate;

                Application.WorkbookOpen -= Application_WorkbookOpen;

                ((Excel.AppEvents_Event)Application).NewWorkbook -= Application_NewWorkbook;

            }

            catch (Exception ex)

            {

                Modules.TalliarkLog.Trace($"addin shutdown unsubscribe failed: {ex}");

            }

            Modules.Infrastructure.AddinExceptionGuard.Uninstall();

            Modules.TalliarkLog.Trace("EXIT");

        }



        private void DisposeApplicationSurfacesForShutdown()

        {

            using (Modules.TalliarkLog.Time("DisposeApplicationSurfacesForShutdown total"))

            {

                Modules.TalliarkLog.Trace($"ENTER panes={_workbookPanes.Count} sessions={_storageSessions.Count}");

                foreach (WorkbookPaneEntry entry in _workbookPanes.ToArray())
                {
                    DisposePaneEntry(entry, "shutdown");
                }

                _workbookPanes.Clear();
                _recentlyActivated.Clear();
                _selectionTargets.Clear();

                foreach (WorkbookFileManagerEntry entry in _workbookFileManagers.ToArray())
                {
                    try
                    {
                        if (!entry.Window.IsDisposed)
                        {
                            Modules.TalliarkLog.Trace("disposing workbook file manager window");
                            entry.Window.Dispose();
                        }
                    }
                    catch (Exception ex)
                    {
                        Modules.TalliarkLog.Trace(
                            $"file manager dispose failed: {ex.GetType().FullName}: {ex.Message}");
                    }
                }
                _workbookFileManagers.Clear();

                foreach (WorkbookReconcileEntry entry in _workbookReconcileWindows.ToArray())
                    DisposeWindow(entry.Window, "reconcile");
                _workbookReconcileWindows.Clear();

                foreach (WorkbookViewerEntry entry in _workbookViewers.ToArray())
                {
                    try
                    {
                        if (!entry.Window.IsDisposed)
                        {
                            Modules.TalliarkLog.Trace("disposing workbook viewer window");
                            entry.Window.Dispose();
                        }
                    }
                    catch (Exception ex)
                    {
                        Modules.TalliarkLog.Trace(
                            $"viewer window dispose failed: {ex.GetType().FullName}: {ex.Message}");
                    }
                }
                _workbookViewers.Clear();

                foreach (WorkbookLinkerEntry entry in _workbookLinkers.ToArray())
                {
                    try
                    {
                        if (!entry.Window.IsDisposed)
                        {
                            Modules.TalliarkLog.Trace("disposing workbook document linker window");
                            entry.Window.Dispose();
                        }
                    }
                    catch (Exception ex)
                    {
                        Modules.TalliarkLog.Trace(
                            $"document linker dispose failed: {ex.GetType().FullName}: {ex.Message}");
                    }
                }
                _workbookLinkers.Clear();

                _storageSessions.Clear();

                Modules.TalliarkLog.Trace("EXIT");

            }

        }



        private void Application_WorkbookBeforeClose(Excel.Workbook wb, ref bool cancel)
        {
            Modules.TalliarkLog.Trace(
                $"ENTER workbook={GetWorkbookDebugName(wb)} cancel={cancel} " +
                $"panes={_workbookPanes.Count} viewers={_workbookViewers.Count} " +
                $"fileManagers={_workbookFileManagers.Count} " +
                $"reconcileWindows={_workbookReconcileWindows.Count} " +
                $"linkers={_workbookLinkers.Count} " +
                $"sessions={_storageSessions.Count}");

            // Safe to drop even if the close is cancelled: the session is only a cache over
            // the workbook's Custom XML, and GetStorageSession rebuilds it on next use. Guarded
            // because the workbook's RCW can already be disconnected here, which throws from
            // the COM identity read inside.
            try
            {
                ReleaseStorageSession(wb);
            }
            catch (Exception ex)
            {
                Modules.TalliarkLog.Trace(
                    $"storage session release failed: {ex.GetType().FullName}: {ex.Message}");
            }

            // The recorded selection must not outlive the workbook instance. Keys are full
            // paths, so a workbook reopened at the same path would otherwise inherit the
            // previous session's cell. A cancelled close costs only the record, and the
            // live-selection fallback still answers correctly.
            try
            {
                _selectionTargets.Forget(GetWorkbookSessionKey(wb));
            }
            catch (Exception ex)
            {
                Modules.TalliarkLog.Trace(
                    $"selection target release failed: {ex.GetType().FullName}: {ex.Message}");
            }

            // The pane entry is deliberately left alone.
            //
            // This event fires *before* Excel asks about unsaved changes, so the close can
            // still be cancelled — and if the user cancels, the workbook stays open. Removing
            // the entry here left exactly that case broken: the CustomTaskPane was still on
            // screen but no longer in _workbookPanes, so the next Show Task Pane built a
            // second pane for the same workbook and the original host was never disposed.
            //
            // ReconcileClosedWorkbooks handles it instead, by checking which workbooks Excel
            // actually still has open rather than guessing from this event.
            Modules.TalliarkLog.Trace("EXIT (pane cleanup deferred to reconcile)");
        }

        /// <summary>
        /// Drops pane entries and storage sessions belonging to workbooks Excel no longer has
        /// open, disposing each orphaned host.
        /// </summary>
        /// <remarks>
        /// Driven off the live workbook collection rather than the close event, because
        /// WorkbookBeforeClose cannot tell a real close from one the user is about to cancel.
        /// Called from the workbook lifecycle points that matter — activate, open, and pane
        /// creation — but deliberately not from the selection-change path, which is hot.
        /// </remarks>
        private void ReconcileClosedWorkbooks()
        {
            if (_workbookPanes.Count == 0
                && _workbookViewers.Count == 0
                && _workbookFileManagers.Count == 0
                && _workbookReconcileWindows.Count == 0
                && _workbookLinkers.Count == 0
                && _recentlyActivated.Count == 0
                && _selectionTargets.Count == 0
                && _storageSessions.Count == 0)
                return;

            var liveWorkbooks = new HashSet<IntPtr>();
            var liveKeys = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

            try
            {
                foreach (Excel.Workbook open in Application.Workbooks)
                {
                    IntPtr unknown = IntPtr.Zero;
                    try
                    {
                        unknown = Marshal.GetIUnknownForObject(open);
                        liveWorkbooks.Add(unknown);
                        liveKeys.Add(GetWorkbookSessionKey(open));
                    }
                    finally
                    {
                        if (unknown != IntPtr.Zero) Marshal.Release(unknown);
                    }
                }
            }
            catch (Exception ex)
            {
                // Excel is busy or mid-teardown. Retry on the next lifecycle event rather than
                // risk disposing a pane whose workbook is in fact still open.
                Modules.TalliarkLog.Trace($"ReconcileClosedWorkbooks enumeration failed: {ex.Message}");
                return;
            }

            // Selection targets are recorded for every workbook the user touches, including
            // ones with no Talliark surface, so they prune from the live set rather than
            // riding on the storage-session sweep below.
            _selectionTargets.PruneTo(liveKeys);

            foreach (WorkbookPaneEntry entry in _workbookPanes.ToArray())
            {
                if (IsWorkbookStillOpen(entry.Workbook, liveWorkbooks))
                    continue;

                Modules.TalliarkLog.Trace("reconcile: removing pane entry for closed workbook");
                _workbookPanes.Remove(entry);
                DisposePaneEntry(entry, "reconcile");
            }

            foreach (WorkbookViewerEntry entry in _workbookViewers.ToArray())
            {
                if (!entry.Window.IsDisposed
                    && IsWorkbookStillOpen(entry.Workbook, liveWorkbooks))
                    continue;

                Modules.TalliarkLog.Trace("reconcile: removing viewer for closed workbook");
                _workbookViewers.Remove(entry);

                try
                {
                    if (!entry.Window.IsDisposed)
                        entry.Window.Dispose();
                }
                catch (Exception ex)
                {
                    Modules.TalliarkLog.Trace(
                        $"reconcile: viewer dispose failed: {ex.GetType().FullName}: {ex.Message}");
                }
            }

            foreach (WorkbookFileManagerEntry entry in _workbookFileManagers.ToArray())
            {
                if (!entry.Window.IsDisposed
                    && IsWorkbookStillOpen(entry.Workbook, liveWorkbooks))
                    continue;

                Modules.TalliarkLog.Trace("reconcile: removing file manager for closed workbook");
                _workbookFileManagers.Remove(entry);

                try
                {
                    if (!entry.Window.IsDisposed)
                        entry.Window.Dispose();
                }
                catch (Exception ex)
                {
                    Modules.TalliarkLog.Trace(
                        $"reconcile: file manager dispose failed: {ex.GetType().FullName}: {ex.Message}");
                }
            }

            foreach (WorkbookReconcileEntry entry in _workbookReconcileWindows.ToArray())
            {
                if (!entry.Window.IsDisposed
                    && IsWorkbookStillOpen(entry.Workbook, liveWorkbooks))
                    continue;

                _workbookReconcileWindows.Remove(entry);
                try
                {
                    if (!entry.Window.IsDisposed) entry.Window.Dispose();
                }
                catch (Exception ex)
                {
                    Modules.TalliarkLog.Trace(
                        $"reconcile: Reconcile window dispose failed: {ex.Message}");
                }
            }

            foreach (WorkbookLinkerEntry entry in _workbookLinkers.ToArray())
            {
                if (!entry.Window.IsDisposed
                    && IsWorkbookStillOpen(entry.Workbook, liveWorkbooks))
                    continue;

                Modules.TalliarkLog.Trace("reconcile: removing document linker for closed workbook");
                _workbookLinkers.Remove(entry);

                try
                {
                    if (!entry.Window.IsDisposed)
                        entry.Window.Dispose();
                }
                catch (Exception ex)
                {
                    Modules.TalliarkLog.Trace(
                        $"reconcile: document linker dispose failed: {ex.GetType().FullName}: {ex.Message}");
                }
            }

            // A closed workbook must not keep a slot in the warm set, or it would hold one
            // open on behalf of a workbook that no longer exists.
            for (int i = _recentlyActivated.Count - 1; i >= 0; i--)
            {
                if (!IsWorkbookStillOpen(_recentlyActivated[i], liveWorkbooks))
                    _recentlyActivated.RemoveAt(i);
            }

            // Backstop for sessions WorkbookBeforeClose did not catch — a workbook closed
            // without that event, or one whose key changed via Save As while it was open.
            foreach (string key in new List<string>(_storageSessions.Keys))
            {
                if (liveKeys.Contains(key)) continue;
                _storageSessions.Remove(key);
                _transientPdfGeometry.Remove(key);
                _linkUndoStacks.Remove(key);
                Modules.TalliarkLog.Trace("reconcile: released storage session for closed workbook");
            }
        }

        /// <summary>
        /// COM-identity test against the set of open workbooks. A workbook that has closed
        /// leaves behind an RCW that throws when touched, so failure here means closed too.
        /// </summary>
        private static bool IsWorkbookStillOpen(Excel.Workbook workbook, HashSet<IntPtr> liveWorkbooks)
        {
            if (workbook == null) return false;

            IntPtr unknown = IntPtr.Zero;
            try
            {
                unknown = Marshal.GetIUnknownForObject(workbook);
                return liveWorkbooks.Contains(unknown);
            }
            catch (Exception ex)
            {
                Modules.TalliarkLog.Trace($"reconcile: workbook handle unusable, treating as closed: {ex.Message}");
                return false;
            }
            finally
            {
                if (unknown != IntPtr.Zero) Marshal.Release(unknown);
            }
        }



        private void Application_WorkbookActivate(Excel.Workbook wb)

        {

            // An exception escaping an Excel COM event is how an add-in gets disabled, so the
            // whole handler is guarded — not just the refresh at the end of it.

            try

            {

                ReconcileClosedWorkbooks();

                WebViewEagerLoader.WarmUp(this, wb);

                EnsureLinkTracking(wb);

                // Pending undo belongs to whichever workbook is in front, so re-evaluate before
                // anything else — including the pop-out early return further down.
                RefreshExcelUndoArmedState();

                WorkbookViewerEntry viewerEntry = FindViewerEntryFor(wb);
                if (viewerEntry != null
                    && !viewerEntry.Window.IsDisposed
                    && viewerEntry.Window.Visible)

                {

                    viewerEntry.Window.InvalidateData();

                    viewerEntry.Window.RefreshDataIfReady();

                    return;

                }

                var entry = FindEntryFor(wb);

                if (entry == null) return;

                if (entry.Pane.Visible)

                    entry.Host.RefreshDataIfReady();

            }

            catch (Exception ex)

            {

                Modules.TalliarkLog.Trace(
                    $"Application_WorkbookActivate failed: {ex.GetType().FullName}: {ex.Message}");

            }

        }



        private async void Application_WorkbookOpen(Excel.Workbook wb)

        {

            // async void on a COM event: an exception here is rethrown on Excel's UI thread and
            // attributed to the add-in, so it must never escape.

            try

            {

                ReconcileClosedWorkbooks();

                WebViewEagerLoader.WarmUp(this, wb);

                EnsureLinkTracking(wb);

                await CheckForUpdateOnOpenAsync();

            }

            catch (Exception ex)

            {

                Modules.TalliarkLog.Trace(
                    $"Application_WorkbookOpen failed: {ex.GetType().FullName}: {ex.Message}");

            }

        }

        private Task CheckForUpdateOnOpenAsync()

        {

            try

            {

                if (AppVersion.IsDevelopment)
                    return Task.CompletedTask;

                if ((DateTime.UtcNow - Settings.Default.LastUpdateCheck).TotalHours < 24)
                    return Task.CompletedTask;

                lock (_automaticUpdateCheckSync)
                {
                    if (_automaticUpdateCheckTask == null || _automaticUpdateCheckTask.IsCompleted)
                        _automaticUpdateCheckTask = CheckForUpdateOnOpenCoreAsync();

                    return _automaticUpdateCheckTask;
                }

            }

            catch (Exception ex)

            {

                // Reading settings can fail on a corrupt user.config; that must not fail the
                // workbook-open event it was called from.

                Modules.TalliarkLog.Trace($"update check skipped: {ex.Message}");

                return Task.CompletedTask;

            }

        }

        private async Task CheckForUpdateOnOpenCoreAsync()

        {

            UpdateCheckResult result;

            try { result = await UpdateCheckService.CheckAsync().ConfigureAwait(true); }

            catch { return; }

            if (result?.UpdateAvailable != true) return;

            try

            {

                UpdateDialog.ShowSingle(result);

            }

            catch (Exception ex)

            {

                Modules.TalliarkLog.Trace(
                    $"update dialog failed: {ex.GetType().FullName}: {ex.Message}");

            }

        }



        private void Application_NewWorkbook(Excel.Workbook wb)

        {

            try

            {

                WebViewEagerLoader.WarmUp(this, wb);

                EnsureLinkTracking(wb);

            }

            catch (Exception ex)

            {

                Modules.TalliarkLog.Trace(
                    $"Application_NewWorkbook failed: {ex.GetType().FullName}: {ex.Message}");

            }

        }


        // ── Warm-up and eviction ──────────────────────────────────────────────
        //
        // Each warm-up creates a workbook's surface invisibly and forces HWND creation, which
        // is what starts its WebView2 initialising, so the window is loaded before the user
        // asks for it. All four are idempotent: the repeated calls that come from workbook
        // activation cost a registry lookup and nothing more.
        //
        // Warming all four is only affordable because EvictUnopenedSurfaces takes them back.
        // WebViewEagerLoader decides which run and how many workbooks stay warm; nothing else
        // should call any of this directly.

        /// <summary>
        /// Records <paramref name="wb"/> as the most recently activated workbook.
        /// </summary>
        internal void NoteWorkbookActivated(Excel.Workbook wb)
        {
            if (wb == null) return;

            for (int i = _recentlyActivated.Count - 1; i >= 0; i--)
            {
                if (IsSameWorkbook(_recentlyActivated[i], wb))
                    _recentlyActivated.RemoveAt(i);
            }

            _recentlyActivated.Insert(0, wb);
        }

        /// <summary>
        /// Disposes warm surfaces belonging to workbooks outside the
        /// <paramref name="keepMostRecent"/> most recently activated, keeping anything the
        /// user actually opened.
        /// </summary>
        /// <remarks>
        /// This is what makes warming all four surfaces affordable. Without it, a session that
        /// visits twenty workbooks ends up holding four WebView2 renderers for each of them,
        /// most for workbooks the user glanced at once. The keep count has to be more than one
        /// or alternating between two workbooks would rebuild every surface on each switch.
        /// </remarks>
        internal void EvictUnopenedSurfaces(int keepMostRecent)
        {
            if (keepMostRecent < 1) return;
            if (_recentlyActivated.Count <= keepMostRecent) return;

            var keep = new List<Excel.Workbook>();
            for (int i = 0; i < keepMostRecent && i < _recentlyActivated.Count; i++)
                keep.Add(_recentlyActivated[i]);

            foreach (WorkbookLinkerEntry entry in _workbookLinkers.ToArray())
            {
                if (entry.WasShown || IsAnyOf(entry.Workbook, keep)) continue;
                _workbookLinkers.Remove(entry);
                DisposeWindow(entry.Window, "linker");
            }

            foreach (WorkbookViewerEntry entry in _workbookViewers.ToArray())
            {
                if (entry.WasShown || IsAnyOf(entry.Workbook, keep)) continue;
                _workbookViewers.Remove(entry);
                DisposeWindow(entry.Window, "viewer");
            }

            foreach (WorkbookFileManagerEntry entry in _workbookFileManagers.ToArray())
            {
                if (entry.WasShown || IsAnyOf(entry.Workbook, keep)) continue;
                _workbookFileManagers.Remove(entry);
                DisposeWindow(entry.Window, "file manager");
            }

            foreach (WorkbookReconcileEntry entry in _workbookReconcileWindows.ToArray())
            {
                if (entry.WasShown || IsAnyOf(entry.Workbook, keep)) continue;
                _workbookReconcileWindows.Remove(entry);
                DisposeWindow(entry.Window, "reconcile");
            }

            foreach (WorkbookPaneEntry entry in _workbookPanes.ToArray())
            {
                if (entry.WasShown || IsAnyOf(entry.Workbook, keep)) continue;
                _workbookPanes.Remove(entry);
                DisposePaneEntry(entry, "evict");
            }
        }

        /// <summary>
        /// Tears down a task pane, removing the CustomTaskPane from Excel's collection as well
        /// as disposing the host.
        /// </summary>
        /// <remarks>
        /// Disposing the host alone leaves the pane registered with Excel: an empty strip in
        /// the task-pane list that cannot be reopened. That was survivable while panes only
        /// died with their workbook; now that they can be reclaimed while Excel is running, it
        /// would be visible.
        /// </remarks>
        private void DisposePaneEntry(WorkbookPaneEntry entry, string reason)
        {
            if (entry == null) return;

            Modules.TalliarkLog.Trace($"{reason}: disposing task pane");

            try
            {
                CustomTaskPanes.Remove(entry.Pane);
            }
            catch (Exception ex)
            {
                // Excel drops the pane itself when its window goes, so removing one whose
                // workbook has already closed can fail. The host still has to be disposed.
                Modules.TalliarkLog.Trace(
                    $"{reason}: task pane remove failed: {ex.GetType().FullName}: {ex.Message}");
            }

            try
            {
                entry.Host?.Dispose();
            }
            catch (Exception ex)
            {
                Modules.TalliarkLog.Trace(
                    $"{reason}: task pane host dispose failed: {ex.GetType().FullName}: {ex.Message}");
            }
        }

        private static void DisposeWindow(Form window, string label)
        {
            if (window == null || window.IsDisposed) return;

            Modules.TalliarkLog.Trace($"evict: disposing unopened {label}");

            try
            {
                window.Dispose();
            }
            catch (Exception ex)
            {
                Modules.TalliarkLog.Trace(
                    $"evict: {label} dispose failed: {ex.GetType().FullName}: {ex.Message}");
            }
        }

        /// <summary>True when <paramref name="workbook"/> is Excel's active workbook.</summary>
        internal bool IsWorkbookActive(Excel.Workbook workbook) =>
            IsSameWorkbook(workbook, Application?.ActiveWorkbook);

        /// <summary>COM-identity comparison, the same test the registry lookups use.</summary>
        internal static bool IsSameWorkbook(Excel.Workbook left, Excel.Workbook right)
        {
            if (left == null || right == null) return false;

            IntPtr a = IntPtr.Zero;
            IntPtr b = IntPtr.Zero;
            try
            {
                a = Marshal.GetIUnknownForObject(left);
                b = Marshal.GetIUnknownForObject(right);
                return a == b;
            }
            catch (Exception ex)
            {
                // One of them belongs to a workbook that has closed. Not a match, which makes
                // callers treat it as evictable — and it is.
                Modules.TalliarkLog.Trace($"IsSameWorkbook unusable: {ex.Message}");
                return false;
            }
            finally
            {
                if (a != IntPtr.Zero) Marshal.Release(a);
                if (b != IntPtr.Zero) Marshal.Release(b);
            }
        }

        private static bool IsAnyOf(Excel.Workbook workbook, IList<Excel.Workbook> candidates)
        {
            for (int i = 0; i < candidates.Count; i++)
            {
                if (IsSameWorkbook(candidates[i], workbook)) return true;
            }

            return false;
        }

        /// <summary>
        /// Only the active workbook, because the pane is bound to Application.ActiveWindow —
        /// see EnsureTaskPaneForActiveWorkbook. Warm-up runs from workbook activation, open
        /// and creation, where the workbook in hand is the active one; the guard is here so
        /// that stays true if it is ever called from somewhere else.
        /// </summary>
        internal void WarmUpTaskPaneFor(Excel.Workbook wb)
        {
            if (wb == null || !IsSameWorkbook(wb, Application?.ActiveWorkbook)) return;

            try
            {
                WorkbookPaneEntry entry = EnsureTaskPaneForActiveWorkbook();
                if (entry != null)
                    _ = entry.Host.Handle;
            }
            catch (Exception ex)
            {
                Modules.TalliarkLog.Trace(
                    $"WarmUpTaskPaneFor failed: {ex.GetType().FullName}: {ex.Message}");
            }
        }

        internal void WarmUpFileManagerFor(Excel.Workbook wb)
        {
            if (wb == null) return;

            try
            {
                WorkbookFileManagerEntry entry = EnsureFileManagerFor(wb);
                _ = entry.Window.Handle;
            }
            catch (Exception ex)
            {
                Modules.TalliarkLog.Trace(
                    $"WarmUpFileManagerFor failed: {ex.GetType().FullName}: {ex.Message}");
            }
        }

        internal void WarmUpReconcileFor(Excel.Workbook wb)
        {
            if (wb == null) return;
            try
            {
                WorkbookReconcileEntry entry = EnsureReconcileFor(wb);
                _ = entry.Window.Handle;
            }
            catch (Exception ex)
            {
                Modules.TalliarkLog.Trace(
                    $"WarmUpReconcileFor failed: {ex.GetType().FullName}: {ex.Message}");
            }
        }

        internal void WarmUpViewerWindowFor(Excel.Workbook wb)
        {
            if (wb == null) return;

            try
            {
                WorkbookViewerEntry entry = EnsureViewerWindowFor(wb);
                _ = entry.Window.Handle;
            }
            catch (Exception ex)
            {
                Modules.TalliarkLog.Trace(
                    $"WarmUpViewerWindowFor failed: {ex.GetType().FullName}: {ex.Message}");
            }
        }

        internal void WarmUpLinkerWindowFor(Excel.Workbook wb)
        {
            if (wb == null) return;

            try
            {
                WorkbookLinkerEntry entry = EnsureLinkerFor(wb);
                _ = entry.Window.Handle;
            }
            catch (Exception ex)
            {
                Modules.TalliarkLog.Trace(
                    $"WarmUpLinkerWindowFor failed: {ex.GetType().FullName}: {ex.Message}");
            }
        }

        /// <summary>
        /// True when the user can bring this workbook to the front. Add-in and macro workbooks
        /// (PERSONAL.XLSB and friends) are open but windowless or hidden, and a surface warmed
        /// for one could never be shown.
        /// </summary>
        internal bool HasVisibleWindow(Excel.Workbook wb)
        {
            if (wb == null) return false;

            try
            {
                Excel.Windows windows = wb.Windows;
                if (windows == null || windows.Count == 0) return false;

                return ((Excel.Window)windows[1]).Visible;
            }
            catch (Exception ex)
            {
                Modules.TalliarkLog.Trace(
                    $"HasVisibleWindow unavailable: {ex.GetType().FullName}: {ex.Message}");
                return false;
            }
        }

        /// <summary>
        /// Creates hidden track names for persisted links. The operation is idempotent and
        /// normally becomes a quick read-only check on subsequent workbook activations.
        /// </summary>
        private void EnsureLinkTracking(Excel.Workbook wb)
        {
            if (wb == null || WorkbookProtectionGuard.IsStructureProtected(wb))
                return;

            try
            {
                WorkbookStorageSession session = GetStorageSession(wb);
                LinkCellTracker.EnsureBindings(wb, session.GetLinks());
            }
            catch (Exception ex)
            {
                Modules.TalliarkLog.Trace(
                    $"EnsureLinkTracking failed: {ex.GetType().FullName}: {ex.Message}");
            }
        }



        private void Application_WorkbookBeforeSave(Excel.Workbook wb, bool saveAsUi, ref bool cancel)

        {

            string workbookName = GetWorkbookDebugName(wb);

            Modules.TalliarkLog.Trace($"ENTER workbook={workbookName} saveAsUi={saveAsUi} cancel={cancel}");

            using (Modules.TalliarkLog.Time("WorkbookBeforeSave total"))

            {

            try

            {

                if (WorkbookProtectionGuard.IsStructureProtected(wb))

                {

                    Modules.TalliarkLog.Trace("structure protected - skipping SyncAllPositions");

                    return;

                }

                Modules.TalliarkLog.Trace("calling LinkCellTracker.SyncAllPositions");

                IList<string> prunedIds = LinkCellTracker.SyncAllPositions(wb);

                Modules.TalliarkLog.Trace("LinkCellTracker.SyncAllPositions done");

                if (prunedIds.Count > 0)
                {
                    Modules.TalliarkLog.Trace(
                        $"pruned stale linked rectangles count={prunedIds.Count}");
                    GetViewerHostFor(wb)?.SendLinkRectanglesRemoved(prunedIds);
                    NotifyFileManagerLinksChanged(wb);
                }

            }

            catch (Exception ex)

            {

                System.Diagnostics.Debug.WriteLine(

                    $"[Talliark] Application_WorkbookBeforeSave sync failed: {ex.Message}");

                Modules.TalliarkLog.Trace($"EXCEPTION {ex.GetType().FullName}: {ex.Message}");

            }

            }

            Modules.TalliarkLog.Trace($"EXIT cancel={cancel}");

        }



        /// <summary>
        /// Any worksheet edit the user makes means a link creation is no longer the most recent
        /// action, so the grid's Ctrl+Z goes back to Excel's own undo.
        /// </summary>
        /// <remarks>
        /// Talliark's own writes run inside <see cref="EnterSelectionNavSuppress"/>, which is
        /// what distinguishes them from a user edit here — without that test, creating a link
        /// would immediately disarm the undo it just recorded. Only the edited workbook is
        /// disarmed, so typing in one workbook cannot cancel pending undo history in another.
        /// </remarks>
        private void Application_SheetChange(object sh, Excel.Range target)
        {
            if (IsSelectionNavSuppressed) return;

            try
            {
                TryGetLinkUndoStack((sh as Excel.Worksheet)?.Parent as Excel.Workbook)?.Disarm();
            }
            catch (Exception ex)
            {
                Modules.TalliarkLog.Trace($"Application_SheetChange disarm failed: {ex.Message}");
            }

            RefreshExcelUndoArmedState();
        }

        /// <summary>
        /// Resolves where a link created from the viewer should be written: the cell the user
        /// last selected in <paramref name="workbook"/>, falling back to the live selection
        /// only when that selection belongs to the same workbook.
        /// </summary>
        /// <remarks>
        /// The recorded target is preferred because <c>Application.Selection</c> answers for
        /// the active window. A workbook with more than one window keeps a separate active
        /// sheet per window, so activating the pane's window changes that answer without a
        /// selection event, and reading it at message time wrote the link onto the pane
        /// window's sheet — typically the workbook's first tab — rather than the sheet the
        /// user was working on. The identity-checked fallback covers a workbook whose
        /// selection predates the tracker (it was already open when the add-in started).
        /// <para>
        /// A multi-cell selection resolves to its top-left cell. The range says what the user
        /// has selected, not which cell a link should target, and handing the range on would
        /// stop the rightward scan for an available cell: <c>Range.Formula</c> answers for a
        /// multi-cell range with an array, which every candidate reads as occupied. The
        /// top-left cell is the origin the pipeline is meant to receive.
        /// </para>
        /// </remarks>
        internal bool TryGetLinkTargetCell(
            Excel.Workbook workbook,
            out Excel.Range startCell,
            out Excel.Range activeCell)
        {
            startCell = null;
            activeCell = null;
            if (workbook == null) return false;

            try
            {
                string key = GetWorkbookSessionKey(workbook);
                if (_selectionTargets.TryResolve(workbook, key, out startCell, out activeCell))
                {
                    startCell = TopLeftCellOf(startCell);
                    Modules.TalliarkLog.Trace($"link target recorded {DescribeRange(startCell)}");
                    return startCell != null;
                }

                var selection = Application?.Selection as Excel.Range;
                if (selection == null) return false;

                // Never fall through to another workbook's selection: the pdfIds and cell
                // records this target is used with belong to this workbook alone.
                if (!IsSameWorkbook(selection.Worksheet?.Parent as Excel.Workbook, workbook))
                    return false;

                startCell = TopLeftCellOf(selection);
                activeCell = Application?.ActiveCell as Excel.Range;
                Modules.TalliarkLog.Trace($"link target live {DescribeRange(startCell)}");
                return startCell != null;
            }
            catch (Exception ex)
            {
                Modules.TalliarkLog.Trace(
                    $"TryGetLinkTargetCell failed: {ex.GetType().FullName}: {ex.Message}");
                startCell = null;
                activeCell = null;
                return false;
            }
        }

        /// <summary>
        /// Remembers where the cursor is in the selection's workbook. Recorded for suppressed
        /// selections too: a programmatic select still moves the cursor that the next
        /// viewer-initiated link should start from.
        /// </summary>
        private bool NoteSelectionTarget(Excel.Worksheet sheet, Excel.Range target)
        {
            try
            {
                Excel.Workbook workbook = sheet?.Parent as Excel.Workbook;
                if (workbook == null || target == null) return false;

                // The active cell seeds the table-link anchor. Only record it when it is on
                // the recorded sheet: resolving a foreign sheet's address here would anchor a
                // table on a cell the user never chose. Its read is isolated because a COM
                // failure here must not cost the selection record itself.
                string activeCellAddress = null;
                try
                {
                    var activeCell = Application?.ActiveCell as Excel.Range;
                    if (activeCell != null
                        && string.Equals(
                            (activeCell.Worksheet as Excel.Worksheet)?.Name,
                            sheet.Name,
                            StringComparison.OrdinalIgnoreCase))
                    {
                        activeCellAddress = activeCell.Address;
                    }
                }
                catch (COMException)
                {
                }

                return _selectionTargets.Note(
                    GetWorkbookSessionKey(workbook), sheet, target, activeCellAddress);
            }
            catch (Exception ex)
            {
                Modules.TalliarkLog.Trace(
                    $"NoteSelectionTarget failed: {ex.GetType().FullName}: {ex.Message}");
                return false;
            }
        }

        /// <summary>
        /// Makes <paramref name="cell"/> the link target for its workbook. Called by
        /// navigation the viewer drives itself, because Excel raises no selection event for a
        /// select that does not change the selection — jumping to a linked cell can land on a
        /// cell the sheet already had selected, and without this the target would stay on the
        /// sheet the user came from.
        /// </summary>
        internal void NoteLinkTargetCell(Excel.Range cell)
        {
            if (cell == null) return;

            try
            {
                var sheet = cell.Worksheet as Excel.Worksheet;
                var workbook = sheet?.Parent as Excel.Workbook;
                if (sheet == null || workbook == null) return;

                _selectionTargets.Note(
                    GetWorkbookSessionKey(workbook), sheet, cell, cell.Address);
            }
            catch (Exception ex)
            {
                Modules.TalliarkLog.Trace(
                    $"NoteLinkTargetCell failed: {ex.GetType().FullName}: {ex.Message}");
            }
        }

        private static string DescribeSheetName(object sheet)
        {
            try { return (sheet as Excel.Worksheet)?.Name ?? "(none)"; }
            catch (COMException) { return "(unavailable)"; }
        }

        private static string DescribeRange(Excel.Range range)
        {
            if (range == null) return "(null)";

            try
            {
                var sheet = range.Worksheet as Excel.Worksheet;
                return $"{sheet?.Name ?? "(none)"}!{range.Address}";
            }
            catch (COMException)
            {
                return "(unavailable)";
            }
        }

        /// <summary>
        /// Reduces a selection to the single cell a viewer-created link starts from. For a
        /// multi-cell range that is its top-left cell, which is also what a single-cell
        /// selection is.
        /// </summary>
        private static Excel.Range TopLeftCellOf(Excel.Range selection)
        {
            if (selection == null) return null;

            return (Excel.Range)selection.Cells[1, 1];
        }

        /// <summary>
        /// Records the activated sheet as the link target when the activation restores a
        /// selection Excel will not report.
        /// </summary>
        /// <remarks>
        /// Activating a sheet restores the selection that sheet last had, and Excel raises no
        /// selection-change event when the restored selection is the one the sheet already
        /// had — so without this, switching tabs would leave the link target on the sheet the
        /// user came from. The selection is recorded only when it belongs to the activated
        /// sheet: an activation in a background window leaves <c>Application.Selection</c> on
        /// another sheet, and pairing this sheet with that address would record a cell the
        /// user never chose.
        /// </remarks>
        private void Application_SheetActivate(object sh)
        {
            try
            {
                // A viewer command activates the workbook and the target sheet itself, and
                // another add-in may answer that by activating a sheet of its own choosing.
                // Recording such an activation would move the link target away from the sheet
                // the user was working in. User tab switches happen outside a command and
                // still record.
                if (IsActivationTargetSuppressed)
                {
                    Modules.TalliarkLog.Trace("sheet activation ignored (viewer command in flight)");
                    return;
                }

                var sheet = sh as Excel.Worksheet;
                var selection = Application?.Selection as Excel.Range;
                var selectionSheet = selection?.Worksheet as Excel.Worksheet;
                if (sheet == null || selection == null || selectionSheet == null) return;

                if (!string.Equals(
                        selectionSheet.Name, sheet.Name, StringComparison.OrdinalIgnoreCase))
                    return;

                NoteSelectionTarget(sheet, selection);
                Modules.TalliarkLog.Trace($"sheet activation recorded {DescribeRange(selection)}");
            }
            catch (Exception ex)
            {
                Modules.TalliarkLog.Trace(
                    $"Application_SheetActivate failed: {ex.GetType().FullName}: {ex.Message}");
            }
        }

        private void Application_SheetSelectionChange(object sh, Excel.Range target)

        {

            bool noted = NoteSelectionTarget(sh as Excel.Worksheet, target);

            // Outside the main try, so the trace is guarded on its own: reading Address from a
            // stale range RCW throws, and this handler runs on every cell selection.
            try
            {
                Modules.TalliarkLog.Trace(
                    $"ENTER sheet={DescribeSheetName(sh)} addr={target?.Address ?? "null"} " +
                    $"noted={noted} SuppressNext={SuppressNextSelectionNav} SuppressDepth={_suppressSelectionNavDepth}");
            }
            catch (Exception ex)
            {
                Modules.TalliarkLog.Trace($"selection trace failed: {ex.Message}");
            }

            if (SuppressNextSelectionNav)

            {

                SuppressNextSelectionNav = false;

                Modules.TalliarkLog.Trace("suppressed (SuppressNext) – return");

                return;

            }

            if (IsSelectionNavSuppressed)

            {

                Modules.TalliarkLog.Trace("suppressed (depth) – return");

                return;

            }



            try

            {

                // Resolved once and reused. Each of these walks the pane and viewer
                // registries comparing COM identity entry by entry, and the handler used to
                // call them six separate times — so a single arrow key cost roughly a dozen
                // scans, each proportional to the number of workbooks with surfaces open.
                bool poppedOut = IsViewerPoppedOut;
                bool paneViewerVisible = !poppedOut && IsTaskPaneViewerVisible();
                IDocumentViewerHost viewer = GetActiveViewerHost();

                string searchQuery = GetActiveCellDisplayText(target);
                if (poppedOut || paneViewerVisible)
                    viewer?.SendSearchQuery(searchQuery);

                Excel.Workbook wb = Application?.ActiveWorkbook;

                if (wb == null) return;



                WorkbookStorageSession session = GetStorageSession(wb);

                IList<LinkSelectionEntry> selectedLinks = BuildLinkSelection(session, target, out LinkedRectangle rect);



                // Always publish the selection so the viewer can show or hide its panel.

                viewer?.SendLinkSelectionChanged(selectedLinks);



                if (rect == null)

                {

                    viewer?.SendClearRectangleHighlight();

                    return;

                }



                if (AutoOpenViewerOnCellClick && !poppedOut && !paneViewerVisible)

                {

                    ShowTaskPane();

                    // The only point where the handle read above can be stale: ShowTaskPane
                    // may have just created the pane that becomes the viewer host.
                    viewer = GetActiveViewerHost();

                }



                if (viewer == null) return;



                viewer.SendNavigateToRectangle(rect.Id, rect.PdfId, rect.Rectangle.PageIndex);

            }

            catch (Exception ex)

            {

                System.Diagnostics.Debug.WriteLine(

                    $"[Talliark] Application_SheetSelectionChange failed: {ex.Message}");

            }

        }



        /// <summary>

        /// Publishes the link selection for <paramref name="target"/> without navigating.

        /// Used when the viewer itself drives the Excel selection (a rectangle click), where

        /// navigation is suppressed but the panel still has to reflect the new cell — a Sum

        /// cell backed by several rectangles opens the panel just as a multi-cell drag does.

        /// </summary>

        internal void PublishLinkSelection(Excel.Range target)

        {

            if (target == null) return;



            try

            {

                Excel.Workbook wb = Application?.ActiveWorkbook;

                if (wb == null) return;



                IList<LinkSelectionEntry> entries =

                    BuildLinkSelection(GetStorageSession(wb), target, out _);

                IDocumentViewerHost viewer = GetVisibleViewerHost();
                viewer?.SendSearchQuery(GetActiveCellDisplayText(target));
                viewer?.SendLinkSelectionChanged(entries);

            }

            catch (Exception ex)

            {

                System.Diagnostics.Debug.WriteLine(

                    $"[Talliark] PublishLinkSelection failed: {ex.Message}");

            }

        }



        private IDocumentViewerHost GetVisibleViewerHost()

        {

            return IsViewerPoppedOut || IsTaskPaneViewerVisible()

                ? GetActiveViewerHost()

                : null;

        }



        private string GetActiveCellDisplayText(Excel.Range selection)

        {

            try

            {

                Excel.Range activeCell = Application?.ActiveCell as Excel.Range;

                if (activeCell != null)

                    return activeCell.Text?.ToString() ?? string.Empty;

                return selection?.Text?.ToString() ?? string.Empty;

            }

            catch (COMException)

            {

                return string.Empty;

            }

        }



        /// <summary>

        /// Maps the linked rectangles inside <paramref name="target"/> to viewer payload entries

        /// and reports the first one via <paramref name="firstRect"/> for navigation.

        /// Entries are only populated once the selection covers at least two rectangles, which

        /// covers both several linked cells and a single Sum cell built from several rectangles;

        /// a lone rectangle is handled by navigation alone and the panel stays hidden.

        /// </summary>

        private IList<LinkSelectionEntry> BuildLinkSelection(

            WorkbookStorageSession session,

            Excel.Range target,

            out LinkedRectangle firstRect)

        {

            firstRect = null;

            var entries = new List<LinkSelectionEntry>();



            IList<LinkCellResolver.SelectedLink> selected =

                LinkCellResolver.ResolveLinksInSelection(session.GetLinks(), target);

            if (selected.Count == 0) return entries;



            firstRect = selected[0].Rectangle;

            if (selected.Count < 2) return entries;



            var pdfNames = new Dictionary<string, string>(StringComparer.Ordinal);

            foreach (PdfMetadata pdf in session.Store.LoadContent().Pdfs)

            {

                if (!string.IsNullOrEmpty(pdf?.Id))

                    pdfNames[pdf.Id] = pdf.Name ?? string.Empty;

            }



            foreach (LinkCellResolver.SelectedLink link in selected)

            {

                string cellValue = string.Empty;

                string cellAddress = string.Empty;

                try

                {

                    cellValue = link.Cell.Text?.ToString() ?? string.Empty;

                    cellAddress = ((Excel.Worksheet)link.Cell.Worksheet).Name

                        + "!" + (link.Cell.Address ?? string.Empty).Replace("$", string.Empty);

                }

                catch (COMException) { }



                // Sum cells hold several rectangles behind one total, so each row carries what

                // its own rectangle contributes; the cell total stays on the cell. A rectangle

                // holding several numbers reports their subtotal plus how many it summed,

                // formatted like the cell so the figures line up with the sheet.

                bool isSum = link.Rectangle.LinkType == LinkType.Sum;

                string value = cellValue;

                int valueCount = 1;



                if (isSum)

                {

                    string sourceText = link.Rectangle.SourceText ?? string.Empty;

                    valueCount = TextValueFormatter.CountValues(sourceText);

                    double? subtotal = TextValueFormatter.SumValues(sourceText);

                    value = subtotal.HasValue

                        ? CellFormattingService.FormatLikeCell(link.Cell, subtotal.Value)

                        : sourceText;

                }



                pdfNames.TryGetValue(link.Rectangle.PdfId ?? string.Empty, out string pdfName);



                entries.Add(new LinkSelectionEntry(

                    link.Rectangle.Id,

                    link.Rectangle.PdfId,

                    pdfName ?? string.Empty,

                    link.Rectangle.Rectangle.PageIndex,

                    value,

                    valueCount,

                    cellAddress,

                    cellValue));

            }



            return entries;

        }



        protected override Office.IRibbonExtensibility CreateRibbonExtensibilityObject()

        {

            return new TalliarkRibbon();

        }



        #region VSTO generated code



        /// <summary>

        /// Required method for Designer support - do not modify

        /// the contents of this method with the code editor.

        /// </summary>

        private void InternalStartup()

        {

            this.Startup += new System.EventHandler(ThisAddIn_Startup);

            this.Shutdown += new System.EventHandler(ThisAddIn_Shutdown);

        }



        #endregion

    }



    /// <summary>Associates a workbook's COM identity with its task pane and host control.</summary>

    internal sealed class WorkbookPaneEntry

    {

        internal Excel.Workbook Workbook { get; }

        internal Microsoft.Office.Tools.CustomTaskPane Pane { get; }

        internal TaskPaneHost Host { get; }

        /// <summary>True once the user has opened this surface. See EvictUnopenedSurfaces.</summary>
        internal bool WasShown { get; set; }



        internal WorkbookPaneEntry(

            Excel.Workbook workbook,

            Microsoft.Office.Tools.CustomTaskPane pane,

            TaskPaneHost host)

        {

            Workbook = workbook;

            Pane = pane;

            Host = host;

        }

    }

    /// <summary>Associates a workbook's COM identity with its standalone viewer.</summary>
    internal sealed class WorkbookViewerEntry
    {
        internal Excel.Workbook Workbook { get; }
        internal ViewerWindowHost Window { get; }

        /// <summary>True once the user has opened this surface. See EvictUnopenedSurfaces.</summary>
        internal bool WasShown { get; set; }

        internal WorkbookViewerEntry(Excel.Workbook workbook, ViewerWindowHost window)
        {
            Workbook = workbook;
            Window = window;
        }
    }

    /// <summary>Associates a workbook's COM identity with its file-manager window.</summary>
    internal sealed class WorkbookFileManagerEntry
    {
        internal Excel.Workbook Workbook { get; }
        internal FileManagerHost Window { get; }

        /// <summary>True once the user has opened this surface. See EvictUnopenedSurfaces.</summary>
        internal bool WasShown { get; set; }

        internal WorkbookFileManagerEntry(Excel.Workbook workbook, FileManagerHost window)
        {
            Workbook = workbook;
            Window = window;
        }
    }

    /// <summary>Associates a workbook's COM identity with its Reconcile window.</summary>
    internal sealed class WorkbookReconcileEntry
    {
        internal Excel.Workbook Workbook { get; }
        internal ReconcileHost Window { get; }
        internal bool WasShown { get; set; }

        internal WorkbookReconcileEntry(Excel.Workbook workbook, ReconcileHost window)
        {
            Workbook = workbook;
            Window = window;
        }
    }

    /// <summary>Associates a workbook's COM identity with its document-linker window.</summary>
    internal sealed class WorkbookLinkerEntry
    {
        internal Excel.Workbook Workbook { get; }
        internal DocumentLinkerHost Window { get; }

        /// <summary>True once the user has opened this surface. See EvictUnopenedSurfaces.</summary>
        internal bool WasShown { get; set; }

        internal WorkbookLinkerEntry(Excel.Workbook workbook, DocumentLinkerHost window)
        {
            Workbook = workbook;
            Window = window;
        }
    }

}

