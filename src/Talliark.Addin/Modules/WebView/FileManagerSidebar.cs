using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Windows.Forms;
using Talliark.Addin.Modules.CustomXml.Models;

namespace Talliark.Addin.Modules.WebView
{
    /// <summary>
    /// How an Explorer drop resolved its destination, which decides what happens to a
    /// dropped <em>directory</em>.
    /// </summary>
    internal enum FileDropScope
    {
        /// <summary>
        /// The drop did not name a folder: it landed on the drop strip, on the All Files
        /// row, or on the file table. Files go to <c>folderId</c> — the current selection, or
        /// uncategorised — but a directory becomes a new folder named after itself, because
        /// its own name is the one piece of intent the user gave.
        /// </summary>
        SelectedFolder,

        /// <summary>
        /// The drop landed on a specific folder row, so the user has already said where it
        /// goes. Everything is flattened into that folder, directory structure discarded.
        /// </summary>
        ExplicitFolder,
    }

    /// <summary>
    /// The file-manager's left column: the folder list, and the drop strip beneath it.
    /// </summary>
    /// <remarks>
    /// <para>
    /// This panel replaced the web UI's folder column so that a folder row is a real OLE drop
    /// target. Explorer drops onto a WebView2 surface do not surface HTML5 drop events
    /// inside Office, so a per-row drop cannot be built from DOM events — it has to be
    /// WinForms. <see cref="DragEventArgs.X"/> and <see cref="DragEventArgs.Y"/> arrive
    /// in screen coordinates, so every hit test converts them back to client space.
    /// </para>
    /// <para>
    /// Two drop surfaces share this control and one drag-over treatment: an individual row,
    /// and the wide strip below the list. A row is an explicit destination; the strip is the
    /// ambient one, which is why the two report different <see cref="FileDropScope"/>.
    /// </para>
    /// <para>
    /// Two drags reach those surfaces, and they arrive by unrelated mechanisms. An Explorer
    /// drop is real OLE, so it lands in the DragEnter/DragDrop handlers. A file dragged out
    /// of the web table never becomes OLE at all — an HTML5 drag is settled inside Chromium
    /// and a WebView2 control will not hand one to the host — so the web UI reports the drag
    /// instead, and <see cref="BeginRowDrag"/> takes the mouse. Both then share the same
    /// hit test, highlight and rows, and differ only in what the release means: a row drag
    /// moves files, and it is not offered the drop strip, whose "wherever you are looking"
    /// meaning belongs to importing.
    /// </para>
    /// <para>
    /// The panel raises intent and nothing else: it never touches the workbook. The host
    /// owns every service call and pushes the result back through <see cref="Update"/>, so
    /// a refused write leaves the row list untouched rather than optimistic.
    /// </para>
    /// <para>
    /// Colours mirror src/web/packages/shared/base.css. That coupling used to be a hazard
    /// because the row list was painted here and measured in CSS; with the list entirely on
    /// this side, only <c>--color-*</c> values need matching.
    /// </para>
    /// </remarks>
    internal sealed class FileManagerSidebar : Panel, IMessageFilter
    {
        // ── Layout metrics                                        ────
        // One inset governs the column: the row pills and the drop strip both start and end
        // at it, so the header, the list and the strip read as a single aligned edge rather
        // than three blocks that each picked their own margin.
        private const int ColumnInset = 8;

        /// <summary>Text inset inside a row pill, and the gap the header title shares.</summary>
        private const int RowPadding = 8;

        private const int HeaderHeight = 40;
        private const int RowHeight = 32;
        private const int RowGap = 2;
        private const int ActionSize = 22;
        private const int ActionGap = 4;

        /// <summary>The words the delete confirmation's two pills carry.</summary>
        private const string ConfirmAcceptText = "Confirm";
        private const string ConfirmDeclineText = "Cancel";

        /// <summary>Horizontal padding either side of the word inside a confirm pill.</summary>
        private const int ConfirmButtonPadding = 6;

        /// <summary>Breathing room between a folder name and its count badge.</summary>
        private const int BadgeGap = 10;

        private const int EditHeight = 22;
        private const int ListPadding = 6;

        /// <summary>Rows per autoscroll tick once the pointer is inside the margin.</summary>
        private const int AutoScrollStep = 6;

        /// <summary>Autoscroll margin at the top and bottom of the list, in pixels.</summary>
        private const int AutoScrollEdge = 32;

        /// <summary>Autoscroll tick interval — about 60Hz, matching the web view's scroller.</summary>
        private const int AutoScrollInterval = 16;

        /// <summary>
        /// The drop strip at the foot of the sidebar. It is painted by this control rather
        /// than hosted as a sibling, so the folder list and the drop target share one
        /// layout pass, one hit test and one set of design tokens.
        /// </summary>
        private const int DropZoneHeight = 122;

        /// <summary>Vertical air around the strip: the gap below it and the gap above it.</summary>
        private const int DropZoneMargin = 12;
        private const int DropZoneRadius = 6;

        // ── Design tokens (src/web/packages/shared/base.css)      ────
        private static readonly Color Surface = Color.FromArgb(0xFF, 0xFF, 0xFF);
        private static readonly Color SurfaceHover = Color.FromArgb(0xEC, 0xEC, 0xF2);
        private static readonly Color Border = Color.FromArgb(0xD4, 0xD4, 0xE0);
        private static readonly Color TextPrimary = Color.FromArgb(0x1A, 0x1A, 0x28);
        private static readonly Color TextMuted = Color.FromArgb(0x5C, 0x5C, 0x70);
        private static readonly Color Accent = Color.FromArgb(0x7C, 0x6A, 0xF7);
        private static readonly Color AccentHover = Color.FromArgb(0x6A, 0x5A, 0xE0);
        private static readonly Color AccentSoft = Color.FromArgb(0xEE, 0xF2, 0xFF);
        private static readonly Color AccentFaint = Color.FromArgb(0xB4, 0xA8, 0xFC);
        private static readonly Color Danger = Color.FromArgb(0xC4, 0x2B, 0x2B);

        /// <summary>Mirrors <c>--color-danger-hover</c>; the deepening a solid danger button takes on hover.</summary>
        private static readonly Color DangerHover = Color.FromArgb(0xA8, 0x24, 0x24);

        /// <summary>
        /// A danger tint light enough to sit behind a glyph the way <see cref="AccentSoft"/> sits behind
        /// a drop target — the rest state of a destructive affordance whose hover is the full danger.
        /// </summary>
        private static readonly Color DangerSoft = Color.FromArgb(0xFB, 0xE9, 0xE9);

        /// <summary>White at 65% / 75% / 90%, standing in for the CSS rgba() literals.</summary>
        private static readonly Color OnAccentCount = Color.FromArgb(166, 255, 255, 255);
        private static readonly Color OnAccentAction = Color.FromArgb(191, 255, 255, 255);
        private static readonly Color OnAccentConfirm = Color.FromArgb(230, 255, 255, 255);
        private static readonly Color OnAccentActionHover = Color.FromArgb(46, 255, 255, 255);

        private const string DeletePrompt = "Delete?";

        private enum EditMode { None, Create, Rename, ConfirmDelete }

        private enum HitZone { None, Row, Rename, Delete, CreateOk, CreateCancel, ConfirmAccept, ConfirmDecline }

        /// <summary>Which of the two drop surfaces the pointer is over.</summary>
        private enum DropRegion { None, Row, Panel }

        private sealed class FolderRow
        {
            public string Id;
            public string Name;
            public int Count;
        }

        /// <summary>Raised when the user picks a row. <c>null</c> means All Files.</summary>
        public event Action<string> FolderSelected;

        /// <summary>Raised when the user confirms a new folder name.</summary>
        public event Action<string> FolderCreateRequested;

        /// <summary>Raised with (folderId, newName) when a rename is confirmed.</summary>
        public event Action<string, string> FolderRenameRequested;

        /// <summary>Raised with the folderId once the inline delete confirmation is accepted.</summary>
        public event Action<string> FolderRemoveRequested;

        /// <summary>
        /// Raised for an Explorer drop on either drop surface.
        /// </summary>
        /// <param name="folderId">
        /// The target folder, or <c>null</c> for uncategorised. Always the row's own id for
        /// <see cref="FileDropScope.ExplicitFolder"/>.
        /// </param>
        public event Action<string[], string, FileDropScope> PathsDropped;

        /// <summary>
        /// Raised with (fileIds, folderId) when a web-originated row drag is released over a
        /// folder row. <c>folderId</c> is <c>null</c> for the All Files row: it is a filter
        /// and has no destination, but a drag released there means "uncategorised", which is
        /// the same thing <c>move-file</c> says by omitting its folderId.
        /// </summary>
        public event Action<List<string>, string> FilesDropped;

        /// <summary>
        /// Raised once when a live row drag is over, whatever the release decided — moved,
        /// refused, or released onto nothing. The web UI cannot work this out for itself: it
        /// holds no capture and hears no release, so without this its drag highlight outlives
        /// the gesture.
        /// </summary>
        public event Action RowDragEnded;



        /// <summary>
        /// Raised when the user asks to browse for documents, by clicking the drop strip.
        /// </summary>
        public event Action BrowseRequested;

        private readonly List<FolderRow> _rows = new List<FolderRow>();
        private readonly Font _rowFont = new Font("Segoe UI", 9.75f);
        private readonly Font _countFont = new Font("Segoe UI", 9f, FontStyle.Bold);
        private readonly Font _headerFont = new Font("Segoe UI", 10f, FontStyle.Bold);
        private readonly Font _deleteFont = new Font("Segoe UI", 8.25f, FontStyle.Bold);
        private readonly Font _glyphFont = new Font("Segoe UI", 9.75f);

        private readonly Button _addButton = new Button();
        private readonly Panel _editHost = new Panel();
        private readonly TextBox _editBox = new TextBox();

        private string _selectedId;
        private bool _locked;

        /// <summary>
        /// The list's scrollbar. A real control rather than something painted here, because the
        /// thumb, the track paging and the grab are exactly the behaviour a hand-rolled version
        /// gets wrong — and this list has to stay scrollable while the host holds the mouse for a
        /// row drag, which is when it is least forgiving to be wrong about. It carries the
        /// scroll position and nothing else: <see cref="_scrollOffset"/> is a view of its value.
        /// </summary>
        private readonly VScrollBar _scrollBar = new VScrollBar();

        private int _hoverRow = -1;
        private HitZone _hoverZone = HitZone.None;
        private DropRegion _hoverRegion = DropRegion.None;
        private DropRegion _dropRegion = DropRegion.None;
        private int _dropRow = -1;
        private int _pressRow = -1;
        private HitZone _pressZone = HitZone.None;
        private bool _pressDropZone;

        /// <summary>Pointer position during a row drag, in client coordinates.</summary>
        private Point _dragPoint;

        /// <summary>
        /// The last pointer position this control was told about. Hover is held as a row
        /// <em>index</em>, so scrolling the list under a stationary pointer invalidates it
        /// without producing a mouse-move to recompute it with — the wheel in particular, which
        /// is delivered to the focused window and never to this panel.
        /// </summary>
        private Point _hoverPoint;

        private readonly Timer _autoScrollTimer = new Timer();

        /// <summary>
        /// File GUIDs held by a web-originated row drag, or <c>null</c> when no such drag is
        /// live. Armed from a <c>row-drag-started</c> message and disarmed by the release,
        /// which reaches this control because the host took the mouse when it armed.
        /// </summary>
        private List<string> _rowDragIds;

        private EditMode _editMode = EditMode.None;

        /// <summary>Row index being edited. The create row is the virtual index <c>_rows.Count</c>.</summary>
        private int _editRow = -1;

        private string _renameOriginal = string.Empty;

        /// <summary>
        /// Non-zero while a click inside the edited row is being pressed. Focus leaves the
        /// edit box before that click reaches <c>MouseUp</c>, and the <c>LostFocus</c>
        /// backstop must not treat that as an abandonment.
        /// </summary>
        private int _holdFocus;

        private bool _disposed;

        public FileManagerSidebar()
        {
            DoubleBuffered = true;
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint | ControlStyles.ResizeRedraw, true);
            BackColor = Surface;
            ForeColor = TextPrimary;
            Font = _rowFont;
            TabStop = false;
            AllowDrop = true;

            _addButton.Text = "+";
            _addButton.Font = _rowFont;
            _addButton.Size = new Size(ActionSize, ActionSize);
            _addButton.FlatStyle = FlatStyle.Flat;
            _addButton.BackColor = Surface;
            _addButton.ForeColor = TextMuted;
            _addButton.UseVisualStyleBackColor = false;
            _addButton.TabStop = false;
            _addButton.Cursor = Cursors.Hand;
            _addButton.FlatAppearance.BorderSize = 0;
            _addButton.FlatAppearance.MouseOverBackColor = SurfaceHover;
            _addButton.Click += OnAddButtonClick;
            _addButton.MouseWheel += OnSidebarMouseWheel;
            Controls.Add(_addButton);

            _editHost.BackColor = SurfaceHover;
            _editHost.Size = new Size(0, EditHeight);
            _editHost.Visible = false;
            _editHost.Paint += OnEditHostPaint;
            Controls.Add(_editHost);

            _editBox.BorderStyle = BorderStyle.None;
            _editBox.BackColor = SurfaceHover;
            _editBox.ForeColor = TextPrimary;
            _editBox.Font = _rowFont;
            _editBox.MaxLength = 64;
            _editHost.Controls.Add(_editBox);
            _editBox.LostFocus += OnEditBoxLostFocus;
            _editBox.KeyDown += OnEditBoxKeyDown;
            _editBox.MouseWheel += OnSidebarMouseWheel;

            // Sized and placed over the list band rather than docked: docking right would run it the
            // full height of the panel, alongside the header and the drop strip as well.
            _scrollBar.Minimum = 0;

            // Never in the tab order. The sidebar has always left the keyboard to the file table
            // beside it, and a scrollbar in that chain would take Ctrl+A from the table.
            _scrollBar.TabStop = false;
            _scrollBar.Scroll += OnScrollBarScroll;
            _scrollBar.Width = SystemInformation.VerticalScrollBarWidth;
            Controls.Add(_scrollBar);

            DragEnter += OnSidebarDragEnter;
            DragOver += OnSidebarDragEnter;
            DragLeave += OnSidebarDragLeave;
            DragDrop += OnSidebarDragDrop;

            _autoScrollTimer.Interval = AutoScrollInterval;
            _autoScrollTimer.Tick += OnAutoScrollTick;

            // Registered here and dropped in Dispose, because the wheel arrives at the focused
            // window and this control is not the focused one — see PreFilterMessage.
            Application.AddMessageFilter(this);

            LayoutChrome();
            SyncScrollBar();
        }

        /// <summary>True for the whole OCR run, which disables folder CRUD and every drop.</summary>
        public bool IsLocked => _locked;

        /// <summary>The GUID of the selected row, or <c>null</c> for All Files.</summary>
        public string SelectedFolderId => _selectedId;

        /// <summary>
        /// Replaces the row list from the workbook's current content. A selected folder
        /// that no longer exists falls back to All Files and reports the change, because
        /// the file table is filtered by selection and would otherwise keep showing
        /// a folder the user cannot see selected.
        /// </summary>
        public void Update(IList<PdfFolder> folders, IList<PdfMetadata> pdfs)
        {
            _rows.Clear();
            _rows.Add(new FolderRow { Id = null, Name = "All Files", Count = pdfs != null ? pdfs.Count : 0 });

            if (folders != null)
            {
                foreach (PdfFolder folder in folders)
                {
                    if (folder == null) continue;
                    _rows.Add(new FolderRow { Id = folder.Id, Name = folder.Name ?? string.Empty, Count = CountIn(pdfs, folder.Id) });
                }
            }

            if (_selectedId != null && !HasFolder(_selectedId))
            {
                _selectedId = null;
                FolderSelected?.Invoke(null);
            }

            CancelEdit();
            SyncScrollBar();
            Invalidate();
        }

        /// <summary>
        /// Locks or unlocks folder CRUD while OCR runs. Selection stays available so the
        /// user can still browse, and drops are refused outright.
        /// </summary>
        public void SetLocked(bool locked)
        {
            if (_locked == locked) return;
            _locked = locked;

            _addButton.Enabled = !locked;
            _addButton.ForeColor = locked ? DisabledGlyph : TextMuted;
            _addButton.Cursor = locked ? Cursors.No : Cursors.Hand;

            if (locked) CancelEdit();

            // OCR taking the list mid-drag ends the drag: the files it is carrying are the
            // ones being locked, and the move would land on a list that refuses drops.
            if (locked) EndRowDrag();

            // Locking removes the row actions from hit testing, so whatever is hovered
            // now resolves to a different zone or none.
            _hoverRow = -1;
            _hoverZone = HitZone.None;
            _hoverRegion = DropRegion.None;
            _dropRegion = DropRegion.None;
            _dropRow = -1;
            Cursor = locked ? Cursors.No : Cursors.Default;
            Invalidate();
        }

        /// <summary>
        /// Returns the column to its opening state when the window hides: no selection, no
        /// inline edit, and no drop highlight left lit against a list that is about to be
        /// rebuilt from a different workbook state.
        /// </summary>
        public void Reset()
        {
            CancelEdit();
            EndRowDrag();
            _dropRegion = DropRegion.None;
            _dropRow = -1;
            _hoverRegion = DropRegion.None;

            if (_selectedId == null) return;
            _selectedId = null;
            FolderSelected?.Invoke(null);
            Invalidate();
        }

        protected override void OnSizeChanged(EventArgs e)
        {
            base.OnSizeChanged(e);
            if (_disposed) return;
            LayoutChrome();
            SyncScrollBar();
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing && !_disposed)
            {
                _disposed = true;

                // A filter left registered would keep being asked about the wheel for a control
                // that no longer exists, and an unticketed timer would outlive the window.
                Application.RemoveMessageFilter(this);
                _autoScrollTimer.Stop();
                _autoScrollTimer.Tick -= OnAutoScrollTick;
                _autoScrollTimer.Dispose();

                _rowFont.Dispose();
                _countFont.Dispose();
                _headerFont.Dispose();
                _deleteFont.Dispose();
                _glyphFont.Dispose();
            }

            base.Dispose(disposing);
        }

        // ── Chrome                                                ────

        private void LayoutChrome()
        {
            _addButton.Location = new Point(
                Math.Max(ColumnInset, ClientSize.Width - ColumnInset - ActionSize),
                (HeaderHeight - ActionSize) / 2);

            _scrollBar.Location = new Point(
                Math.Max(0, ClientSize.Width - _scrollBar.Width),
                ListTop);

            // The bar spans the folder items' whole container — down to the drop strip —
            // rather than stopping where the rows' clip does. No row ever paints into the
            // padding above the strip, but the white column runs to it, and a bar that ends
            // with the clip reads as cut short of the container's bottom edge.
            _scrollBar.Height = Math.Max(0, DropZoneRect.Top - ListTop);

            PositionEditBox();
        }

        private int ListTop => HeaderHeight + ListPadding;

        /// <summary>
        /// The right edge the rows may paint to: the scrollbar's left when there is one, the
        /// full width when there is not. Everything row-shaped measures from here, so the pills,
        /// the count badge and the action glyphs all move together when the bar appears.
        /// </summary>
        private int RowsRightEdge => _scrollBar.Visible ? Math.Max(0, _scrollBar.Left) : ClientSize.Width;

        /// <summary>
        /// The drop strip, sharing <see cref="ColumnInset"/> with the row pills so both
        /// blocks start and end on the same vertical edges. Only its vertical margins differ,
        /// and those are the same on each side.
        /// </summary>
        private Rectangle DropZoneRect =>
            new Rectangle(
                ColumnInset,
                ClientSize.Height - DropZoneMargin - DropZoneHeight,
                Math.Max(0, ClientSize.Width - ColumnInset * 2),
                DropZoneHeight);

        /// <summary>
        /// The rows stop short of the drop strip's top margin, so the two never touch and the
        /// strip stays a distinct surface.
        /// </summary>
        private int ListHeight => Math.Max(0, ClientSize.Height - ListTop - ListPadding - DropZoneHeight - DropZoneMargin * 2);

        private int ContentHeight => _rows.Count * RowHeight + (_editMode == EditMode.Create ? RowHeight : 0);

        /// <summary>
        /// The list's scroll position, in pixels. The scrollbar owns it: this is a view of the
        /// control's own value so that every reader and writer in this file stays unchanged, and
        /// assigning it is what makes the thumb move, whether the change came from the wheel, the
        /// autoscroller, or the user dragging the thumb.
        /// </summary>
        /// <remarks>
        /// The clamp is here rather than left to the control because
        /// <see cref="System.Windows.Forms.ScrollBar.Value"/> throws
        /// <see cref="ArgumentOutOfRangeException"/> on an out-of-range assignment instead of
        /// clamping. Every caller works in deltas and overshoots the end of the list routinely —
        /// one wheel notch is 120px and the autoscroll steps 6 — so without this a single notch
        /// past the end throws and the list does not move at all.
        /// </remarks>
        private int _scrollOffset
        {
            get => _scrollBar.Value;
            set
            {
                int max = MaxScroll;
                if (value < 0) value = 0;
                else if (value > max) value = max;
                _scrollBar.Value = value;
            }
        }

        private bool NeedsScroll => ContentHeight > ListHeight;

        private int MaxScroll => Math.Max(0, ContentHeight - ListHeight);

        /// <summary>The list's own rectangle, stopping short of the scrollbar when there is one.</summary>
        private Rectangle ListAreaRect =>
            new Rectangle(0, ListTop, RowsRightEdge, ListHeight);

        /// <summary>
        /// Tells the scrollbar how far there is to scroll and how big a step is, then shows it
        /// only when there is somewhere to go. The one place the two sides are kept in step;
        /// everything else reads or writes <see cref="_scrollOffset"/>, which clamps to the
        /// content's own range.
        /// </summary>
        private void SyncScrollBar()
        {
            _scrollBar.LargeChange = Math.Max(1, ListHeight);
            _scrollBar.SmallChange = RowHeight;

            // A Win32 scrollbar's thumb can only reach Maximum - LargeChange + 1, so a Maximum
            // equal to the content's scroll range would leave the last page unreachable —
            // one full page short, always. Inflating the maximum by the page is what makes the
            // reachable bottom equal the real bottom; the offset property still clamps writes
            // to the content's range, so nothing can scroll past the end.
            _scrollBar.Maximum = MaxScroll + _scrollBar.LargeChange - 1;
            _scrollBar.Visible = MaxScroll > 0;

            // The range just moved, so the position needs the same clamp applied — shortening
            // the window shrinks the range under a value this code wrote a moment ago — and
            // hover needs re-deriving, because the list's height changed under a pointer that
            // cannot move while the window edge is being dragged.
            int offset = _scrollOffset;
            _scrollOffset = offset;
            OnListScrolled();
        }

        // ── Painting                                              ────

        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e);

            Graphics g = e.Graphics;
            g.Clear(Surface);
            g.SmoothingMode = SmoothingMode.AntiAlias;

            PaintHeader(g);

            Rectangle listArea = ListAreaRect;

            Region savedClip = g.Clip;
            g.SetClip(listArea);
            for (int i = 0; i < VisibleRowCount; i++)
                PaintRow(g, i, listArea);
            g.Clip = savedClip;

            PaintDropZone(g);
        }

        private int VisibleRowCount => _rows.Count + (_editMode == EditMode.Create ? 1 : 0);

        private void PaintHeader(Graphics g)
        {
            TextRenderer.DrawText(
                g,
                "Folders".ToUpperInvariant(),
                _headerFont,
                new Rectangle(ColumnInset + RowPadding, 0, ClientSize.Width, HeaderHeight),
                TextMuted,
                TextFormatFlags.SingleLine | TextFormatFlags.VerticalCenter | TextFormatFlags.NoPrefix);

            using (var pen = new Pen(Border))
                g.DrawLine(pen, 0, HeaderHeight - 1, ClientSize.Width, HeaderHeight - 1);
        }

        private void PaintRow(Graphics g, int index, Rectangle listArea)
        {
            bool isCreate = _editMode == EditMode.Create && index == _rows.Count;
            var rect = RowRect(index);

            if (rect.Bottom < listArea.Top || rect.Top > listArea.Bottom) return;

            // Text is GDI (TextRenderer) and does not obey the GDI+ clip that holds the pills
            // inside the list band — it draws straight through it, so a row scrolled part-way
            // past the top edge would paint its name over the header. Every text draw is given
            // the row's intersection with the band instead, which for a fully visible row is
            // the row itself and for a cut row lays the text out inside what is on screen. The
            // pill keeps the natural rect: GDI+ clips it hard at the band edge, which is the
            // look a cut pill should have.
            Rectangle visible = Rectangle.Intersect(rect, listArea);

            string id = isCreate ? null : RowAt(index).Id;
            bool isSelected = !isCreate && _selectedId == id;
            bool isDropTarget = _dropRegion == DropRegion.Row && _dropRow == index && !isCreate;
            bool isEditing = _editMode != EditMode.None && _editRow == index;
            bool showsActions = !isCreate && !isEditing && id != null && !_locked && !isDropTarget;

            // Actions belong to the pointer, not to the selection: a selected row that the
            // pointer has left is a filter, not something being edited. Hiding them also
            // stops the glyph column from reflowing the count badge on every selection.
            bool actionsVisible = showsActions && _hoverRow == index;

            Color fill = Surface;
            if (isDropTarget) fill = AccentSoft;
            else if (isSelected) fill = (_hoverRow == index && !_locked) ? AccentHover : Accent;
            else if (_hoverRow == index && !isEditing && !_locked) fill = SurfaceHover;

            // Rounded pills sitting flush against each other read as one block rather than as rows,
            // so each is inset by half the row gap on both edges.
            var pill = new Rectangle(
                ColumnInset,
                rect.Top + RowGap / 2,
                RowsRightEdge - ColumnInset * 2,
                rect.Height - RowGap);
            if (fill != Surface)
            {
                using (var brush = new SolidBrush(fill))
                    FillRounded(g, pill, DropZoneRadius, brush);
            }

            // The dashed accent outline is the same drag-over signal the drop strip uses, so
            // the two surfaces read as one language. A solid rectangle over the rounded fill
            // would square off the corners and misread as a different state.
            if (isDropTarget)
                StrokeRounded(g, pill, Accent, 2f, DashStyle.Dash);

            // A row can be selected and be the drop target at once — selecting a folder and
            // then dragging onto it is the ordinary case — and those two states want opposite
            // text colours, because the drop target sits on the near-white accent tint.
            bool onAccent = isSelected && !isDropTarget;
            Color nameColor = onAccent ? Color.White : TextPrimary;
            Color countColor = onAccent ? OnAccentCount : TextMuted;

            if (isEditing && _editMode == EditMode.Rename)
            {
                // The edit box is a child control; the row behind it is left blank.
            }
            else if (isCreate)
            {
                HitZone hover = _hoverRow == index ? _hoverZone : HitZone.None;
                DrawConfirmGlyph(g, CreateCancelRect(visible), "✕", nameColor, HitZone.CreateCancel, hover);
                DrawConfirmGlyph(g, CreateOkRect(visible), "✓", nameColor, HitZone.CreateOk, hover);
            }
            else
            {
                int countLeft = DrawCountBadge(g, visible, index, countColor, actionsVisible);

                if (actionsVisible)
                {
                    Color actionColor = onAccent ? OnAccentAction : TextMuted;

                    // Each action hovers in its own meaning: delete deepens toward danger,
                    // rename toward the accent. On a selected row both fall back to the one
                    // hover language that reads on the accent fill — white over it.
                    DrawRowAction(g, DeleteRect(visible), "✕", actionColor,
                        onAccent ? OnAccentActionHover : DangerSoft,
                        onAccent ? Color.White : Danger,
                        index, HitZone.Delete);
                    DrawRowAction(g, RenameRect(visible), "✎", actionColor,
                        onAccent ? OnAccentActionHover : AccentSoft,
                        onAccent ? Color.White : AccentHover,
                        index, HitZone.Rename);
                }

                if (_editMode == EditMode.ConfirmDelete && isEditing)
                    DrawDeleteConfirmation(g, visible, onAccent);
                else
                    DrawName(g, visible, RowAt(index).Name, nameColor, countLeft);
            }
        }

        /// <summary>
/// Draws a folder name. No <see cref="TextFormatFlags.NoClipping"/>: the layout rect is the
/// row's visible slice, and without a clip a thin slice at the band's edge would paint the
/// full-height glyphs past it — the very leak this layout rect exists to close.
/// </summary>
private void DrawName(Graphics g, Rectangle rect, string name, Color color, int rightLimit)
        {
            TextRenderer.DrawText(
                g,
                name ?? string.Empty,
                _rowFont,
                new Rectangle(
                    rect.Left + ColumnInset + RowPadding,
                    rect.Top,
                    Math.Max(0, rightLimit - rect.Left - ColumnInset - RowPadding * 2),
                    rect.Height),
                color,
                TextFormatFlags.SingleLine | TextFormatFlags.VerticalCenter |
                TextFormatFlags.EndEllipsis | TextFormatFlags.NoPrefix);
        }

        /// <summary>Draws the per-folder file count and returns the x it starts at.</summary>
        private int DrawCountBadge(Graphics g, Rectangle rect, int index, Color color, bool actionsVisible)
        {
            if (_editMode == EditMode.ConfirmDelete && _editRow == index)
                return rect.Left + ColumnInset + RowPadding;

            int right = RowsRightEdge - ColumnInset - RowPadding;
            if (actionsVisible)
                right = RenameRect(rect).Left - BadgeGap;

            const int minCountWidth = 18;
            int measured = TextRenderer.MeasureText(g, "0", _countFont).Width;
            int width = Math.Max(minCountWidth, measured);
            var area = new Rectangle(right - width, rect.Top, width, rect.Height);

            TextRenderer.DrawText(
                g,
                RowAt(index).Count.ToString(),
                _countFont,
                area,
                color,
                TextFormatFlags.SingleLine | TextFormatFlags.VerticalCenter |
                TextFormatFlags.Right | TextFormatFlags.NoPrefix);

            // The gap the name has to stop short of, so a long folder name truncates against
            // a margin rather than butting up against the number.
            return area.Left - BadgeGap;
        }

        /// <summary>
        /// Draws a row action glyph with its hover pad. The pad and glyph colours arrive as
        /// parameters because the two actions mean different things — delete hovers in the
        /// danger tint, rename in the accent one — and the caller is the one that knows which
        /// row state (plain or selected) the hover has to read against.
        /// </summary>
        private void DrawRowAction(Graphics g, Rectangle rect, string glyph, Color color, Color hoverFill, Color hoverGlyph, int index, HitZone zone)
        {
            HitZone hover = _hoverRow == index ? _hoverZone : HitZone.None;

            if (hover == zone)
            {
                // Inset from the glyph box so the hover pad does not run into its neighbour.
                const int pad = 2;
                var hoverFillRect = new Rectangle(
                    rect.Left + pad,
                    rect.Top + pad,
                    Math.Max(0, rect.Width - pad * 2),
                    Math.Max(0, rect.Height - pad * 2));
                using (var brush = new SolidBrush(hoverFill))
                    FillRounded(g, hoverFillRect, 4, brush);
            }

            TextRenderer.DrawText(
                g,
                glyph,
                _glyphFont,
                rect,
                hover == zone ? hoverGlyph : color,
                TextFormatFlags.SingleLine | TextFormatFlags.VerticalCenter |
                TextFormatFlags.HorizontalCenter | TextFormatFlags.NoPrefix);
        }

        private void DrawConfirmGlyph(Graphics g, Rectangle rect, string glyph, Color color, HitZone zone, HitZone hover)
        {
            if (hover == zone)
            {
                var hoverFill = new Rectangle(rect.Left + 1, rect.Top + 3, rect.Width - 2, rect.Height - 6);
                using (var brush = new SolidBrush(SurfaceHover))
                    FillRounded(g, hoverFill, 4, brush);
            }

            TextRenderer.DrawText(
                g,
                glyph,
                _glyphFont,
                rect,
                color,
                TextFormatFlags.SingleLine | TextFormatFlags.VerticalCenter |
                TextFormatFlags.HorizontalCenter | TextFormatFlags.NoPrefix);
        }

        private void DrawDeleteConfirmation(Graphics g, Rectangle rect, bool isSelected)
        {
            var declineRect = ConfirmDeclineRect(rect);
            var acceptRect = ConfirmAcceptRect(rect);
            int labelWidth = TextRenderer.MeasureText(g, DeletePrompt, _deleteFont).Width;
            var labelRect = new Rectangle(acceptRect.Left - ActionGap - labelWidth, rect.Top, labelWidth, rect.Height);

            DrawName(g, rect, RowAt(_editRow).Name, isSelected ? Color.White : TextPrimary, labelRect.Left - ActionGap);

            TextRenderer.DrawText(
                g,
                DeletePrompt,
                _deleteFont,
                labelRect,
                isSelected ? OnAccentConfirm : Danger,
                TextFormatFlags.SingleLine | TextFormatFlags.VerticalCenter | TextFormatFlags.NoPrefix);

            DrawConfirmButton(g, acceptRect, ConfirmAcceptText, HitZone.ConfirmAccept, destructive: true);
            DrawConfirmButton(g, declineRect, ConfirmDeclineText, HitZone.ConfirmDecline, destructive: false);
        }

        /// <summary>
        /// Draws one Confirm/Cancel pill for the inline delete confirmation, in the language the web
        /// modal's buttons speak: Confirm is the danger button — danger tint at rest, solid
        /// danger with a white label on hover — and Cancel is the secondary one — surface and
        /// border at rest, surface-hover on hover. The pill carries its own fill, so both
        /// read the same whether the row behind them is selected or not.
        /// </summary>
        private void DrawConfirmButton(Graphics g, Rectangle row, string label, HitZone zone, bool destructive)
        {
            var pill = new Rectangle(row.Left + 1, row.Top + 5, row.Width - 2, row.Height - 10);
            bool hover = _hoverZone == zone;

            Color fill = destructive ? DangerSoft : Surface;
            Color edge = destructive ? Danger : Border;
            Color labelColor = destructive ? Danger : TextPrimary;
            if (hover)
            {
                if (destructive)
                {
                    fill = Danger;
                    labelColor = Color.White;
                }
                else
                {
                    fill = SurfaceHover;
                }
            }

            using (GraphicsPath path = RoundedPath(pill, DropZoneRadius))
            {
                using (var brush = new SolidBrush(fill))
                    g.FillPath(brush, path);
                using (var pen = new Pen(edge))
                    g.DrawPath(pen, path);
            }

            TextRenderer.DrawText(
                g,
                label,
                _deleteFont,
                pill,
                labelColor,
                TextFormatFlags.SingleLine | TextFormatFlags.VerticalCenter |
                TextFormatFlags.HorizontalCenter | TextFormatFlags.NoPrefix);
        }

        private void OnEditHostPaint(object sender, PaintEventArgs e)
        {
            using (var pen = new Pen(Accent))
                e.Graphics.DrawRectangle(pen, 0, 0, _editHost.Width - 1, _editHost.Height - 1);
        }

        /// <summary>
        /// The wide drop strip: the general way to add documents, and the only clickable
        /// surface. Its drag-over state is the dashed accent outline over the soft accent
        /// fill, matching a targeted folder row so both drop targets look alike.
        /// </summary>
        private void PaintDropZone(Graphics g)
        {
            Rectangle rect = DropZoneRect;
            if (rect.Width <= 0 || rect.Height <= 0) return;

            bool dragOver = !_locked && _dropRegion == DropRegion.Panel;
            bool hover = !_locked && _hoverRegion == DropRegion.Panel;

            Color fill = _locked ? SurfaceHover : dragOver ? AccentSoft : Surface;
            using (var brush = new SolidBrush(fill))
                FillRounded(g, rect, DropZoneRadius, brush);

            // Grey at rest, a paler accent on hover, and the full accent while a drag is over it —
// the same dashed outline a targeted folder row gets, so both drop surfaces match.
Color borderColor = _locked ? Border
    : dragOver ? Accent
    : hover ? AccentFaint
    : Border;

StrokeRounded(g, rect, borderColor, 2f, DashStyle.Dash);

            Color iconColor = dragOver || hover ? Accent : TextMuted;
            float iconWeight = hover && !dragOver ? 2f : 1.5f;

            string title = _locked ? "Adding files is paused" : "Drop documents or folders here";
            string body = _locked ? "OCR is running" : "or click to browse";

            using (var titleFont = new Font(Font.FontFamily, 9.75f, FontStyle.Bold))
            using (var bodyFont = new Font(Font.FontFamily, 9.75f, FontStyle.Regular))
            {
                Size titleSize = TextRenderer.MeasureText(g, title, titleFont);
                Size bodySize = TextRenderer.MeasureText(g, body, bodyFont);

                // Stack the icon, title and caption as one centred unit.
                const int iconSize = 24;
                const int iconGap = 6;
                const int textGap = 5;
                int totalHeight = iconSize + iconGap + titleSize.Height + textGap + bodySize.Height;
                int top = rect.Top + (rect.Height - totalHeight) / 2;

                int iconTop = top;
                int titleTop = top + iconSize + iconGap;
                int bodyTop = titleTop + titleSize.Height + textGap;

                DrawDocumentIcon(g, rect, iconTop, iconSize, iconColor, iconWeight);

                TextRenderer.DrawText(
                    g, title, titleFont,
                    new Rectangle(rect.Left, titleTop, rect.Width, titleSize.Height),
                    _locked ? TextMuted : TextPrimary,
                    TextFormatFlags.SingleLine | TextFormatFlags.VerticalCenter |
                    TextFormatFlags.HorizontalCenter | TextFormatFlags.NoPrefix);

                TextRenderer.DrawText(
                    g, body, bodyFont,
                    new Rectangle(rect.Left, bodyTop, rect.Width, bodySize.Height),
                    TextMuted,
                    TextFormatFlags.SingleLine | TextFormatFlags.VerticalCenter |
                    TextFormatFlags.HorizontalCenter | TextFormatFlags.NoPrefix);
            }
        }

        /// <summary>A sheet with a folded corner, drawn rather than loaded from an image resource.</summary>
        private void DrawDocumentIcon(Graphics g, Rectangle rect, int top, int size, Color color, float weight)
        {
            using (var pen = new Pen(color, weight))
            using (var brush = new SolidBrush(color))
            {
                int left = rect.Left + (rect.Width - size) / 2;
                int fold = 6;

                g.DrawRectangle(pen, left + 3, top, size - 6, size);
                g.FillPolygon(brush, new[]
                {
                    new Point(left + size - 3 - fold, top),
                    new Point(left + size - 3, top + fold),
                    new Point(left + size - 3 - fold, top + fold),
                });
            }
        }

        private static void FillRounded(Graphics g, Rectangle rect, int radius, Brush brush)
        {
            if (radius <= 0 || rect.Width <= 0 || rect.Height <= 0)
            {
                g.FillRectangle(brush, rect);
                return;
            }

            using (GraphicsPath path = RoundedPath(rect, radius))
                g.FillPath(brush, path);
        }

        /// <summary>
        /// Strokes a rounded outline, inset by half the pen width so a dashed stroke is not
        /// clipped at the edge and its dashes sit evenly inside the fill.
        /// </summary>
        private static void StrokeRounded(Graphics g, Rectangle rect, Color color, float width, DashStyle dash)
        {
            if (rect.Width <= width || rect.Height <= width) return;

            float inset = width / 2f + 1f;
            var bounds = new RectangleF(rect.X + inset, rect.Y + inset, rect.Width - inset * 2, rect.Height - inset * 2);

            using (var pen = new Pen(color, width) { DashStyle = dash, Alignment = PenAlignment.Center })
            using (GraphicsPath path = RoundedPath(
                Rectangle.Round(bounds), Math.Max(1, DropZoneRadius - (int)inset)))
            {
                g.DrawPath(pen, path);
            }
        }

        private static GraphicsPath RoundedPath(Rectangle rect, int radius)
        {
            var path = new GraphicsPath();
            if (radius <= 0 || rect.Width <= radius * 2 || rect.Height <= radius * 2)
            {
                path.AddRectangle(rect);
                return path;
            }

            int d = radius * 2;
            path.AddArc(rect.X, rect.Y, d, d, 180, 90);
            path.AddArc(rect.Right - d, rect.Y, d, d, 270, 90);
            path.AddArc(rect.Right - d, rect.Bottom - d, d, d, 0, 90);
            path.AddArc(rect.X, rect.Bottom - d, d, d, 90, 90);
            path.CloseFigure();
            return path;
        }

        // ── Row geometry                                          ────

        private Rectangle RowRect(int index) =>
            new Rectangle(0, ListTop + index * RowHeight - _scrollOffset, RowsRightEdge, RowHeight);

        private Rectangle RowHitRect(int index) =>
            new Rectangle(0, RowRect(index).Top, RowsRightEdge, RowHeight);

        private FolderRow RowAt(int index) =>
            index >= 0 && index < _rows.Count ? _rows[index] : null;

        private Rectangle RenameRect(Rectangle row) =>
            new Rectangle(
                RowsRightEdge - ColumnInset - ActionSize * 2 - ActionGap,
                row.Top,
                ActionSize,
                row.Height);

        private Rectangle DeleteRect(Rectangle row) =>
            new Rectangle(
                RowsRightEdge - ColumnInset - ActionSize,
                row.Top,
                ActionSize,
                row.Height);

        private Rectangle CreateOkRect(Rectangle row) =>
            new Rectangle(RowsRightEdge - ColumnInset - ActionSize, row.Top, ActionSize, row.Height);

        private Rectangle CreateCancelRect(Rectangle row) =>
            new Rectangle(RowsRightEdge - ColumnInset - ActionSize * 2, row.Top, ActionSize, row.Height);

        /// <summary>
        /// Each pill is as wide as its own word plus padding, so "Confirm" and "Cancel" never
        /// share a width that fits only the shorter one. Measured rather than fixed because
        /// the words are the pill's own constants and the font never changes at runtime.
        /// </summary>
        private Rectangle ConfirmDeclineRect(Rectangle row)
        {
            int width = TextRenderer.MeasureText(ConfirmDeclineText, _deleteFont).Width + ConfirmButtonPadding * 2;
            return new Rectangle(RowsRightEdge - ColumnInset - width, row.Top, width, row.Height);
        }

        private Rectangle ConfirmAcceptRect(Rectangle row)
        {
            int width = TextRenderer.MeasureText(ConfirmAcceptText, _deleteFont).Width + ConfirmButtonPadding * 2;
            return new Rectangle(ConfirmDeclineRect(row).Left - ActionGap - width, row.Top, width, row.Height);
        }

        /// <summary>
        /// Row index under <paramref name="point"/>, or -1 when the point is not on a
        /// visible row.
        /// </summary>
        private int HitTestRow(Point point)
        {
            if (point.Y < ListTop) return -1;

            // Both axes, not just height. During a row drag this control holds the mouse and is
            // told about every move in the window, most of which happen over the file table with
            // an x far beyond this column; an OLE drag over the scrollbar child reaches here the
            // same way, the child having no drop target of its own. A height-only test would
            // light whatever folder happens to share the pointer's height — and on release, drop
            // onto it — so nothing is a row unless the pointer is horizontally over the rows,
            // stopping short of the scrollbar's column like the pills do.
            if (point.X < 0 || point.X >= RowsRightEdge) return -1;

            int index = (point.Y - ListTop + _scrollOffset) / RowHeight;
            if (index < 0 || index >= VisibleRowCount) return -1;

            // The drop strip takes height away from the list, so rows scrolled past the
            // bottom are still within the index range but are no longer under the pointer.
            // Without this, a click in the gap above the strip would hit an invisible row.
            if (RowRect(index).Top >= ListTop + ListHeight) return -1;

            return index;
        }

        /// <summary>
        /// Which drop surface the pointer is over. The gap between the list and the strip is
        /// deliberately not one of them: a drop there is refused rather than guessed at.
        /// </summary>
        private DropRegion HitTestRegion(Point point)
        {
            if (DropZoneRect.Contains(point)) return DropRegion.Panel;

            int index = HitTestRow(point);
            return index >= 0 ? DropRegion.Row : DropRegion.None;
        }

        private HitZone HitTestZone(int index, Point point)
        {
            if (index < 0) return HitZone.None;

            Rectangle row = RowHitRect(index);
            if (!row.Contains(point)) return HitZone.None;

            bool isEditing = _editMode != EditMode.None && _editRow == index;

            if (isEditing)
            {
                if (_editMode == EditMode.ConfirmDelete)
                {
                    if (ConfirmAcceptRect(row).Contains(point)) return HitZone.ConfirmAccept;
                    if (ConfirmDeclineRect(row).Contains(point)) return HitZone.ConfirmDecline;
                    return HitZone.Row;
                }

                if (_editMode == EditMode.Create)
                {
                    if (CreateOkRect(row).Contains(point)) return HitZone.CreateOk;
                    if (CreateCancelRect(row).Contains(point)) return HitZone.CreateCancel;
                }

                return HitZone.Row;
            }

            // The OCR lock takes away folder CRUD, not browsing: the row itself stays
            // selectable so the user can still read the table through a filter. The glyph
            // zones are gated on the row being hovered so that what the hit test accepts
            // always matches what is on screen — the pointer being over the row is what
            // reveals them.
            FolderRow target = RowAt(index);
            if (target != null && target.Id != null && !_locked && _hoverRow == index)
            {
                if (DeleteRect(row).Contains(point)) return HitZone.Delete;
                if (RenameRect(row).Contains(point)) return HitZone.Rename;
            }

            // Anywhere else on the row is the row itself. This must resolve to Row rather
            // than to the previous result: a press and its release are matched by comparing
            // zones, so a zone that only ever echoes its own last value never matches and
            // every plain click on a folder is discarded.
            return HitZone.Row;
        }

        // ── Mouse                                                 ────

        protected override void OnMouseMove(MouseEventArgs e)
        {
            base.OnMouseMove(e);
            if (_disposed) return;

            // A row drag owns the pointer until its release, and says so with the drop
            // highlight rather than with hover: the pointer may be over the web table or off
            // the window by now, and neither of those can be a target.
            if (_rowDragIds != null)
            {
                _hoverPoint = e.Location;
                RefreshRowDrag(e.Location);
                return;
            }

            _hoverPoint = e.Location;
            RefreshHover(e.Location);
            UpdateHoverCursor(e.Location);
        }

        /// <summary>
        /// Re-resolves hover against the pointer's last known position after the list has moved
        /// under it. Without this the hover sits on a row index, the scroll slides a different
        /// folder onto that index, and the highlight looks stuck to a row the pointer has left.
        /// </summary>
        private void RehoverAfterScroll()
        {
            // (0, 0) is the header, which resolves to no row and no strip — the correct
            // "nothing hovered" state, so a scroll before any mouse-move is harmless.
            RefreshHover(_hoverPoint);
            UpdateHoverCursor(_hoverPoint);
        }

        private void UpdateHoverCursor(Point location)
        {
            // The strip is a button, so it says so; rows keep the arrow.
            Cursor = _locked
                ? Cursors.No
                : HitTestRegion(location) == DropRegion.Panel ? Cursors.Hand : Cursors.Default;
        }

        protected override void OnMouseLeave(EventArgs e)
        {
            base.OnMouseLeave(e);
            if (_disposed) return;

            if (_rowDragIds != null)
            {
                // Leaving the column does not end a row drag. The mouse is held, so the
                // pointer is likely over the file table and the release is still coming; the
                // highlight stays where it was because only the release may clear it.
                return;
            }

            // A press that never sees a MouseUp (drag out of the window, capture stolen by
            // a modal) must not leave the focus guard latched forever.
            _holdFocus = 0;
            _pressRow = -1;
            _pressZone = HitZone.None;
            _pressDropZone = false;

            if (_hoverRow < 0 && _hoverRegion == DropRegion.None) return;
            _hoverRow = -1;
            _hoverZone = HitZone.None;
            _hoverRegion = DropRegion.None;
            Invalidate();
        }

        protected override void OnMouseDown(MouseEventArgs e)
        {
            base.OnMouseDown(e);
            if (_disposed || e.Button != MouseButtons.Left) return;

            // A press here during a row drag is not a second gesture: the press that started
            // the drag is in the web view, and this control is only holding the mouse for it.
            if (_rowDragIds != null) return;

            int index = HitTestRow(e.Location);
            _pressDropZone = HitTestRegion(e.Location) == DropRegion.Panel;

            if (_pressDropZone)
            {
                // Nothing but this control paints the strip, and it has no native button to
                // capture for it, so a click that wanders a few pixels before release would
                // otherwise deliver nothing at all.
                Capture = true;
                _pressRow = -1;
                _pressZone = HitZone.None;
                return;
            }

            // Leaving a name edit applies it before the new row handles this click. Delete
            // confirmation is not a name edit, so clicking away still declines it.
            if (_editMode != EditMode.None && !IsInsideEditRow(index))
            {
                CompleteEditOnFocusLoss();
            }
            else if (IsInsideEditRow(index))
            {
                _holdFocus++;
            }

            _pressRow = index;
            _pressZone = HitTestZone(index, e.Location);
        }

        protected override void OnMouseUp(MouseEventArgs e)
        {
            base.OnMouseUp(e);
            if (_disposed) return;

            if (_rowDragIds != null)
            {
                // Any other button's release is ignored rather than treated as a click: the
                // left button is the one that started this drag, and a release here is the
                // only thing that ends it.
                if (e.Button == MouseButtons.Left) CompleteRowDrag(e.Location);
                return;
            }

            if (e.Button != MouseButtons.Left)
            {
                ClearPress();
                return;
            }

            int index = HitTestRow(e.Location);
            bool onDropZone = HitTestRegion(e.Location) == DropRegion.Panel;
            HitZone zone = onDropZone ? HitZone.None : HitTestZone(index, e.Location);

            bool pressed = _pressDropZone
                ? onDropZone
                : _pressRow == index && _pressZone == zone && zone != HitZone.None;
            ClearPress();

            if (!pressed) return;

            if (onDropZone) BrowseRequested?.Invoke();
            else Activate(index, zone);
        }

        protected override void OnMouseDoubleClick(MouseEventArgs e)
        {
            base.OnMouseDoubleClick(e);
            if (_disposed || e.Button != MouseButtons.Left || _rowDragIds != null) return;
            if (_locked || _editMode != EditMode.None) return;

            int index = HitTestRow(e.Location);

            // Only the row body is a rename shortcut. All Files has no backing folder,
            // while the action glyphs retain their own single-click behaviour.
            if (HitTestZone(index, e.Location) != HitZone.Row) return;

            FolderRow row = RowAt(index);
            if (row == null || row.Id == null) return;

            BeginRename(index);
        }

        private void ClearPress()
        {
            _pressRow = -1;
            _pressZone = HitZone.None;
            _pressDropZone = false;
            _holdFocus = 0;

            if (Capture) Capture = false;
        }

        private void RefreshHover(Point location)
        {
            int index = HitTestRow(location);
            DropRegion region = HitTestRegion(location);
            HitZone zone = region == DropRegion.Panel ? HitZone.None : HitTestZone(index, location);
            if (index == _hoverRow && zone == _hoverZone && region == _hoverRegion) return;

            int previous = _hoverRow;
            _hoverRow = index;
            _hoverZone = zone;
            _hoverRegion = region;
            InvalidateRow(previous);
            InvalidateRow(index);
            InvalidateDropZone();
        }

        /// <summary>The strip repaints on hover, drag and lock changes, none of which move a row.</summary>
        private void InvalidateDropZone()
        {
            if (_disposed) return;
            Rectangle rect = DropZoneRect;
            if (rect.Width > 0 && rect.Height > 0) Invalidate(rect);
        }

        private bool IsInsideEditRow(int index)
        {
            if (_editMode == EditMode.None) return false;
            return _editMode == EditMode.Create ? index == _rows.Count : index == _editRow;
        }

        private void Activate(int index, HitZone zone)
        {
            switch (zone)
            {
                case HitZone.CreateOk:
                    CommitCreate();
                    break;
                case HitZone.CreateCancel:
                    CancelEdit();
                    break;
                case HitZone.ConfirmAccept:
                    {
                        string id = RowAt(_editRow)?.Id;
                        CancelEdit();
                        if (id != null) FolderRemoveRequested?.Invoke(id);
                        break;
                    }
                case HitZone.ConfirmDecline:
                    CancelEdit();
                    break;
                case HitZone.Rename:
                    BeginRename(index);
                    break;
                case HitZone.Delete:
                    BeginConfirmDelete(index);
                    break;
                default:
                    SelectRow(index);
                    break;
            }
        }

        private void SelectRow(int index)
        {
            if (index < 0) return;

            FolderRow row = RowAt(index);
            if (row == null) return;

            string id = row.Id;
            if (_selectedId == id) return;

            _selectedId = id;
            FolderSelected?.Invoke(id);
            Invalidate();
        }

        // ── Inline edit                                           ────

        private void OnAddButtonClick(object sender, EventArgs e)
        {
            if (_locked) return;
            BeginCreate();
        }

        private void BeginCreate()
        {
            if (_locked) return;
            CancelEdit();

            _editMode = EditMode.Create;
            _editRow = _rows.Count;
            _editBox.Text = string.Empty;

            ScrollIntoView(_editRow);
            LayoutChrome();
            PositionEditBox();
            Invalidate();
            _editBox.Focus();
        }

        private void BeginRename(int index)
        {
            if (_locked) return;

            FolderRow row = RowAt(index);
            if (row == null || row.Id == null) return;

            _editMode = EditMode.Rename;
            _editRow = index;
            _renameOriginal = row.Name ?? string.Empty;
            _editBox.Text = _renameOriginal;

            PositionEditBox();
            Invalidate();
            _editBox.Focus();
            _editBox.SelectionStart = 0;
            _editBox.SelectionLength = _editBox.TextLength;
        }

        private void BeginConfirmDelete(int index)
        {
            if (_locked) return;

            FolderRow row = RowAt(index);
            if (row == null || row.Id == null) return;

            _editMode = EditMode.ConfirmDelete;
            _editRow = index;
            Invalidate();
        }

        private void OnEditBoxKeyDown(object sender, KeyEventArgs e)
        {
            if (e.Control && !e.Alt && e.KeyCode == Keys.Back)
            {
                e.SuppressKeyPress = true;
                e.Handled = true;
                DeletePreviousToken();
            }
            else if (e.KeyCode == Keys.Enter)
            {
                e.SuppressKeyPress = true;
                e.Handled = true;
                CommitEdit();
            }
            else if (e.KeyCode == Keys.Escape)
            {
                e.SuppressKeyPress = true;
                e.Handled = true;
                _holdFocus++;
                CancelEdit();
            }
        }

        private void OnEditBoxLostFocus(object sender, EventArgs e)
        {
            if (_disposed) return;
            if (_holdFocus > 0) return;
            if (_editMode == EditMode.None) return;

            CompleteEditOnFocusLoss();
        }

        /// <summary>
        /// Applies a folder name when focus moves elsewhere. Delete confirmation remains an
        /// explicitly confirmed action and is therefore cancelled on the same transition.
        /// </summary>
        private void CompleteEditOnFocusLoss()
        {
            if (_editMode == EditMode.Create || _editMode == EditMode.Rename) CommitEdit();
            else CancelEdit();
        }

        /// <summary>
        /// Gives the inline editor the Ctrl+Backspace behaviour expected of a modern text
        /// field: remove any selection, or the whitespace and complete token before the caret.
        /// Letters, digits and underscores form words; consecutive punctuation is one token.
        /// </summary>
        private void DeletePreviousToken()
        {
            if (_editBox.SelectionLength > 0)
            {
                _editBox.SelectedText = string.Empty;
                return;
            }

            int end = _editBox.SelectionStart;
            if (end <= 0) return;

            string text = _editBox.Text;
            int start = end;

            while (start > 0 && char.IsWhiteSpace(text[start - 1])) start--;

            if (start > 0)
            {
                bool word = IsWordCharacter(text[start - 1]);
                while (start > 0 && !char.IsWhiteSpace(text[start - 1]) &&
                    IsWordCharacter(text[start - 1]) == word)
                {
                    start--;
                }
            }

            _editBox.SelectionStart = start;
            _editBox.SelectionLength = end - start;
            _editBox.SelectedText = string.Empty;
        }

        private static bool IsWordCharacter(char value)
        {
            return char.IsLetterOrDigit(value) || value == '_';
        }

        private void CommitEdit()
        {
            if (_editMode == EditMode.Rename) CommitRename();
            else CommitCreate();
        }

        private void CommitCreate()
        {
            if (_editMode != EditMode.Create) return;

            string name = _editBox.Text.Trim();
            _editMode = EditMode.None;
            _editRow = -1;
            HideEditBox();
            Invalidate();

            if (name.Length > 0) FolderCreateRequested?.Invoke(name);
        }

        private void CommitRename()
        {
            if (_editMode != EditMode.Rename) return;

            int index = _editRow;
            string original = _renameOriginal;
            string name = _editBox.Text.Trim();

            _editMode = EditMode.None;
            _editRow = -1;
            HideEditBox();
            Invalidate();

            if (name.Length == 0 || name == original) return;

            string id = RowAt(index)?.Id;
            if (id != null) FolderRenameRequested?.Invoke(id, name);
        }

        private void CancelEdit()
        {
            if (_editMode == EditMode.None) return;

            _editMode = EditMode.None;
            _editRow = -1;
            HideEditBox();
            SyncScrollBar();
            Invalidate();
        }

        private void HideEditBox()
        {
            _editHost.Visible = false;
            _editBox.Text = string.Empty;
            _holdFocus = 0;
            // Removing the virtual create row can leave the list scrolled past its end.
            SyncScrollBar();
        }

        private void PositionEditBox()
        {
            if (_editMode == EditMode.None || _editRow < 0)
            {
                _editHost.Visible = false;
                return;
            }

            Rectangle row = RowRect(_editRow);
            Rectangle pill = new Rectangle(
                ColumnInset,
                row.Top + RowGap / 2,
                RowsRightEdge - ColumnInset * 2,
                row.Height - RowGap);

            Rectangle input;
            if (_editMode == EditMode.Create)
            {
                int right = CreateCancelRect(row).Left - ActionGap;
                input = new Rectangle(pill.Left + RowPadding, pill.Top + 4, right - pill.Left - RowPadding * 2, EditHeight);
            }
            else
            {
                input = new Rectangle(
                    pill.Left + RowPadding,
                    pill.Top + (pill.Height - EditHeight) / 2,
                    pill.Width - RowPadding * 2,
                    EditHeight);
            }

            _editHost.Location = input.Location;
            _editHost.Size = input.Size;

            const int pad = 3;
            _editBox.SetBounds(pad, pad, Math.Max(0, input.Width - pad * 2), Math.Max(0, input.Height - pad * 2));

            _editHost.Visible = true;
            _editHost.BringToFront();
        }

        private void ScrollIntoView(int index)
        {
            int top = index * RowHeight;
            int bottom = top + RowHeight;

            if (top < _scrollOffset) _scrollOffset = top;
            else if (bottom > _scrollOffset + ListHeight) _scrollOffset = bottom - ListHeight;
        }

        // ── Drag and drop                                         ────

        private void OnSidebarDragEnter(object sender, DragEventArgs e)
        {
            if (_disposed)
            {
                e.Effect = DragDropEffects.None;
                return;
            }

            DropRegion region = ResolveDropRegion(e);
            int row = region == DropRegion.Row ? HitTestRow(PointToClient(new Point(e.X, e.Y))) : -1;
            SetDropHighlight(region, row);

            e.Effect = region == DropRegion.None ? DragDropEffects.None : DragDropEffects.Copy;
        }

        private void OnSidebarDragLeave(object sender, EventArgs e)
        {
            if (_disposed || _dropRegion == DropRegion.None) return;

            int previous = _dropRow;
            _dropRegion = DropRegion.None;
            _dropRow = -1;
            InvalidateRow(previous);
            InvalidateDropZone();
        }

        private void OnSidebarDragDrop(object sender, DragEventArgs e)
        {
            if (_disposed)
            {
                e.Effect = DragDropEffects.None;
                return;
            }

            Point at = PointToClient(new Point(e.X, e.Y));
            DropRegion region = ResolveDropRegion(e);
            int row = region == DropRegion.Row ? HitTestRow(at) : -1;

            _dropRegion = DropRegion.None;
            _dropRow = -1;
            Invalidate();
            InvalidateDropZone();

            if (region == DropRegion.None)
            {
                e.Effect = DragDropEffects.None;
                return;
            }

            e.Effect = DragDropEffects.Copy;

            string[] paths = e.Data?.GetData(DataFormats.FileDrop) as string[];
            if (paths == null || paths.Length == 0) return;

            if (region == DropRegion.Panel)
            {
                PathsDropped?.Invoke(paths, SelectedFolderId, FileDropScope.SelectedFolder);
                return;
            }

            FolderRow target = RowAt(row);
            if (target == null)
            {
                e.Effect = DragDropEffects.None;
                return;
            }

            // The All Files row is the ambient destination, so it shares the strip's rule
            // about directories even though it is not a folder.
            FileDropScope scope = target.Id == null
                ? FileDropScope.SelectedFolder
                : FileDropScope.ExplicitFolder;

            PathsDropped?.Invoke(paths, target.Id, scope);
        }

        /// <summary>
        /// Which drop surface, if any, will take this drag. Null when OCR holds the sidebar
        /// or the payload carries no file paths.
        /// </summary>
        private DropRegion ResolveDropRegion(DragEventArgs e)
        {
            if (_locked) return DropRegion.None;
            if (e.Data == null || !e.Data.GetDataPresent(DataFormats.FileDrop)) return DropRegion.None;

            // DragEventArgs carries screen coordinates, unlike every other WinForms
            // mouse event.
            return HitTestRegion(PointToClient(new Point(e.X, e.Y)));
        }

        /// <summary>
        /// Lights the drop highlight, repainting only what moved. Both drag origins resolve
        /// to a region and a row and then report them here, so an Explorer drop and a row
        /// drag cannot drift apart on what a target looks like.
        /// </summary>
        private void SetDropHighlight(DropRegion region, int row)
        {
            if (_dropRegion == region && _dropRow == row) return;

            DropRegion previousRegion = _dropRegion;
            int previousRow = _dropRow;
            _dropRegion = region;
            _dropRow = row;

            InvalidateRow(previousRow);
            InvalidateRow(row);
            if (previousRegion != region) InvalidateDropZone();
        }

        // ── Row drag: files dragged out of the web table            ────

        /// <summary>
        /// Arms a row drag and takes the mouse, so the release is ours even though the press
        /// was in the web view beside us. Returns false when the drag cannot be honoured —
        /// nothing to carry, or OCR holding the list — and the caller must then tell the web
        /// view no drag began, because it cannot work that out from this side.
        /// </summary>
        public bool BeginRowDrag(IList<string> fileIds)
        {
            if (_disposed || _locked || fileIds == null || fileIds.Count == 0) return false;

            _rowDragIds = new List<string>(fileIds);

            // Both of these were reachable from the web view's side of the press: an edit the
            // user opened there, and a strip press that gave us the capture we are about to
            // reuse for the drag.
            CancelEdit();
            ClearPress();

            // The capture is the whole mechanism. A mouse-down inside WebView2 puts Chromium
            // in a capture of its own, and without ours the release would go back to the file
            // table and this list would never learn where the files were dropped. SetCapture
            // is last-write-wins on this thread, so this takes it; the read-back matters
            // because a drag whose release we would never see is worse than no drag, leaving
            // a highlight lit over a list that cannot be acted on.
            Capture = true;
            if (!Capture)
            {
                // Hand the gesture back rather than fake it. The caller reports the refusal,
                // and no release will arrive to clear a highlight that is never lit.
                ClearRowDrag();
                return false;
            }

            Cursor = Cursors.No;

            // The autoscroll timer is not started here. Arming says the user has dragged past
            // the threshold, but the host has not told this control where the pointer is, and a
            // tick before the first mouse-move would scroll in a direction nobody asked for. It
            // starts from RefreshRowDrag, which has a real point.
            return true;
        }

        /// <summary>
        /// Disarms a live row drag, clears its highlight and reports that the drag is over.
        /// A no-op when none is live, so the host can call it unconditionally.
        /// </summary>
        public void EndRowDrag()
        {
            if (_rowDragIds == null) return;
            ClearRowDrag();
            RowDragEnded?.Invoke();
        }

        /// <summary>Unarms without reporting, for the paths that never had a drag to report.</summary>
        private void ClearRowDrag()
        {
            _rowDragIds = null;

            _autoScrollTimer.Stop();

            int previous = _dropRow;
            _dropRegion = DropRegion.None;
            _dropRow = -1;
            InvalidateRow(previous);
            InvalidateDropZone();

            if (Capture) Capture = false;
            Cursor = Cursors.Default;
        }

        /// <summary>
        /// Tracks the pointer during a row drag. A folder row is the only target: the strip
        /// below the list is an import surface and its "wherever you are looking" reading has
        /// no bearing on a file that is already in the workbook.
        /// </summary>
        private void RefreshRowDrag(Point location)
        {
            // The autoscroller runs off this position rather than off mouse events: the host
            // holds the mouse, so a pointer resting near an edge produces no further moves at
            // all and a move-triggered scroll would stall with the target just out of reach.
            _dragPoint = location;

            if (NeedsScroll && InAutoScrollEdge(location)) _autoScrollTimer.Start();
            else _autoScrollTimer.Stop();

            DropRegion region = HitTestRegion(location);
            int row = region == DropRegion.Row ? HitTestRow(location) : -1;
            if (row < 0 || RowAt(row) == null) region = DropRegion.None;

            SetDropHighlight(region, region == DropRegion.Row ? row : -1);

            // SizeAll over a target, No over everything else. The four arrows say "these files
            // land here"; the refused cursor says the same thing over the drop strip, the
            // gaps and the whole file table, which is most of the window once the pointer
            // crosses the border. Neither is a hand — a hand is what the drop strip shows at
            // rest, and reusing it here would make importing and moving look like one act.
            Cursor = region == DropRegion.None ? Cursors.No : Cursors.SizeAll;
        }

        /// <summary>
        /// Settles a row drag on the release. That release can land anywhere, because the
        /// mouse is held: over the file table, over a different window, or nowhere at all.
        /// Only a folder row answers it, and anything else is a cancelled drag rather than a
        /// move to no destination in particular.
        /// </summary>
        private void CompleteRowDrag(Point location)
        {
            int row = HitTestRow(location);
            bool onRow = HitTestRegion(location) == DropRegion.Row;
            FolderRow target = onRow ? RowAt(row) : null;

            List<string> ids = _rowDragIds;
            EndRowDrag();

            if (target == null) return;
            FilesDropped?.Invoke(ids, target.Id);
        }

        // ── Scrolling                                             ────

        private const int WM_MOUSEWHEEL = 0x020A;

        private void OnSidebarMouseWheel(object sender, MouseEventArgs e)
        {
            ScrollRows(e.Delta);
        }

        /// <summary>
        /// The scrollbar moved, from its own thumb, from our wheel or from the autoscroller.
        /// Everything the list owes a repaint for happens here, so those paths cannot each
        /// remember a slightly different set.
        /// </summary>
        private void OnScrollBarScroll(object sender, ScrollEventArgs e)
        {
            OnListScrolled();
        }

        /// <summary>
        /// Everything the list owes after its scroll position changed: the inline edit box
        /// follows the row it belongs to, hover is re-derived, and the list repaints.
        /// </summary>
        /// <remarks>
        /// Both this and <see cref="ScrollRows"/> call it, because the two paths cannot be
        /// relied on to share one trigger: the <see cref="ScrollBar.Scroll"/> event is
        /// documented for actions the user took on the control, and whether it also fires for
        /// a programmatic assignment of <see cref="ScrollBar.Value"/> is not something this
        /// code should stand or fall on. Calling it from both makes the work happen at least
        /// once either way, and it is idempotent — a re-derive and a repaint that already
        /// happened are cheap to repeat.
        /// </remarks>
        private void OnListScrolled()
        {
            if (_disposed) return;

            // The list slides under a pointer that may not have moved at all — the wheel and
            // the autoscroller both move it without one — so hover has to be re-derived here or
            // it stays welded to a row index the scroll has already slid a different folder
            // onto.
            PositionEditBox();
            RehoverAfterScroll();
            Invalidate();
        }

        /// <summary>
        /// Scrolls by wheel notches, where a positive delta moves toward the top of the list.
        /// One implementation for the wheel and the autoscroller so the two cannot disagree
        /// about what a step does. Returns whether the list actually moved.
        /// </summary>
        private bool ScrollRows(int delta)
        {
            if (_disposed || delta == 0) return false;

            int before = _scrollOffset;
            _scrollOffset = before - delta;
            if (_scrollOffset == before) return false;

            OnListScrolled();
            return true;
        }

        /// <summary>
        /// Takes the wheel over this list, which it would otherwise never see.
        ///
        /// <para>
        /// WM_MOUSEWHEEL goes to the window with keyboard focus, not to the window under the
        /// pointer, and the focus belongs to the WebView2 file table beside this panel — so the
        /// wheel over the folder list scrolled the table, and the handler that exists for it was
        /// only ever reachable by focusing the add button or the rename box. An application
        /// message filter is the one place that sees the wheel before that routing happens, so
        /// the list claims it and swallows it.
        /// </para>
        /// <para>
        /// The filter sees the wheel for every window in the application, which is why the hit
        /// test is a full bounds test and not just a band. The file table shares the list's
        /// vertical band exactly, so a Y-only test would let this filter claim the wheel over
        /// the table — and the viewer, and Excel's own grid — and scroll the folders from
        /// underneath whatever the user was actually pointing at. Both axes have to agree the
        /// pointer is over this list before the message is swallowed.
        /// </para>
        /// </summary>
        bool IMessageFilter.PreFilterMessage(ref Message m)
        {
            if (_disposed || m.Msg != WM_MOUSEWHEEL) return false;
            if (!NeedsScroll) return false;

            // LParam is the cursor as a screen POINT, WParam carries the notches in its high word.
            int packed = unchecked((int)m.LParam.ToInt64());
            var screenPoint = new Point(packed & 0xFFFF, (packed >> 16) & 0xFFFF);
            Point local = PointToClient(screenPoint);

            if (local.X < 0 || local.X >= ClientSize.Width) return false;
            if (local.Y < ListTop || local.Y >= ListTop + ListHeight) return false;

            ScrollRows((short)((long)m.WParam >> 16));
            return true;
        }

        /// <summary>
        /// Scrolls the list toward a folder the pointer is holding near, then re-resolves the
        /// drop target. A drag cannot use the wheel — the host has the mouse, so the pointer
        /// never sends this control a wheel message — and this is what stands in for it.
        /// Returns whether the list actually moved.
        /// </summary>
        private bool AutoScrollToward(Point at)
        {
            if (!InAutoScrollEdge(at)) return false;

            // ScrollRows takes a positive delta toward the top of the list — the same
            // convention the wheel uses — so a pointer held at the top edge feeds it positive
            // steps and the list chases upward, and the bottom edge feeds it negative ones.
            int step = at.Y < ListTop + AutoScrollBand ? AutoScrollStep : -AutoScrollStep;
            if (!ScrollRows(step)) return false;

            // Re-hit-test every tick: the rows under a stationary pointer have moved under it,
            // and the highlight has to follow them onto rows the user never pointed at.
            RefreshRowDrag(at);
            return true;
        }

        /// <summary>
        /// The autoscroll band at each end of the list, held to under a third of the list so a
        /// short window cannot make the two bands meet. If they met, every point in the list
        /// would count as near an edge, and the point would fall in the overlap where the
        /// direction is arbitrary — the list would ratchet one way and refuse the other.
        /// </summary>
        private int AutoScrollBand => Math.Min(AutoScrollEdge, ListHeight / 3);

        /// <summary>
        /// True when the point is over the list and close enough to an edge for the list to
        /// chase it. The horizontal test matters as much as the vertical one: a held drag
        /// reports positions from over the file table as well, and a list that scrolls while
        /// the pointer is nowhere over it is reacting to a hover that never happened.
        /// </summary>
        private bool InAutoScrollEdge(Point at)
        {
            int band = AutoScrollBand;
            if (band <= 0) return false;
            if (at.X < 0 || at.X >= RowsRightEdge) return false;

            return at.Y < ListTop + band || at.Y > ListTop + ListHeight - band;
        }

        private void OnAutoScrollTick(object sender, EventArgs e)
        {
            if (_disposed || _rowDragIds == null || !NeedsScroll)
            {
                _autoScrollTimer.Stop();
                return;
            }

            // Running the list out of room ends the chase. A tick that can no longer move
            // anything still cost a full repaint of the column, sixty times a second, for as
            // long as the pointer rested there — which reads as a stuck column rather than as
            // "this is the end of it". A short window reaches that state in one tick instead of
            // several, so it got stuck sooner and for longer the smaller the list was.
            if (!AutoScrollToward(_dragPoint)) _autoScrollTimer.Stop();
        }

        // ── Helpers                                               ────

        private void InvalidateRow(int index)
        {
            if (index < 0 || _disposed) return;
            Invalidate(new Rectangle(0, RowRect(index).Top, RowsRightEdge, RowHeight));
        }

        private static int CountIn(IList<PdfMetadata> pdfs, string folderId)
        {
            if (pdfs == null) return 0;

            int count = 0;
            foreach (PdfMetadata pdf in pdfs)
            {
                if (pdf != null && string.Equals(pdf.FolderId, folderId, StringComparison.Ordinal))
                    count++;
            }

            return count;
        }

        private bool HasFolder(string id)
        {
            foreach (FolderRow row in _rows)
            {
                if (string.Equals(row.Id, id, StringComparison.Ordinal)) return true;
            }

            return false;
        }

        private static Color DisabledGlyph => Color.FromArgb(160, TextMuted);
    }
}
