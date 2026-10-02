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
    internal sealed class FileManagerSidebar : Panel
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

        /// <summary>Breathing room between a folder name and its count badge.</summary>
        private const int BadgeGap = 10;

        private const int EditHeight = 22;
        private const int ScrollBarWidth = 6;
        private const int ListPadding = 6;

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

        /// <summary>White at 65% / 75% / 90%, standing in for the CSS rgba() literals.</summary>
        private static readonly Color OnAccentCount = Color.FromArgb(166, 255, 255, 255);
        private static readonly Color OnAccentAction = Color.FromArgb(191, 255, 255, 255);
        private static readonly Color OnAccentConfirm = Color.FromArgb(230, 255, 255, 255);
        private static readonly Color OnAccentActionHover = Color.FromArgb(46, 255, 255, 255);

        private const string DeletePrompt = "Delete?";

        private enum EditMode { None, Create, Rename, ConfirmDelete }

        private enum HitZone { None, Row, Rename, Delete, CreateOk, CreateCancel, ConfirmYes, ConfirmNo }

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
        /// Raised when the user asks to browse for documents, by clicking the drop strip.
        /// </summary>
        public event Action BrowseRequested;

        private readonly List<FolderRow> _rows = new List<FolderRow>();
        private readonly Font _rowFont = new Font("Segoe UI", 9.75f);
        private readonly Font _countFont = new Font("Segoe UI", 7.5f, FontStyle.Bold);
        private readonly Font _headerFont = new Font("Segoe UI", 8.25f, FontStyle.Bold);
        private readonly Font _deleteFont = new Font("Segoe UI", 8.25f, FontStyle.Bold);
        private readonly Font _glyphFont = new Font("Segoe UI", 9.75f);

        private readonly Button _addButton = new Button();
        private readonly Panel _editHost = new Panel();
        private readonly TextBox _editBox = new TextBox();

        private string _selectedId;
        private bool _locked;
        private int _scrollOffset;

        private int _hoverRow = -1;
        private HitZone _hoverZone = HitZone.None;
        private DropRegion _hoverRegion = DropRegion.None;
        private DropRegion _dropRegion = DropRegion.None;
        private int _dropRow = -1;
        private int _pressRow = -1;
        private HitZone _pressZone = HitZone.None;
        private bool _pressDropZone;

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

            DragEnter += OnSidebarDragEnter;
            DragOver += OnSidebarDragEnter;
            DragLeave += OnSidebarDragLeave;
            DragDrop += OnSidebarDragDrop;

            LayoutChrome();
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
            ClampScroll();
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
            ClampScroll();
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing && !_disposed)
            {
                _disposed = true;
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

            PositionEditBox();
        }

        private int ListTop => HeaderHeight + ListPadding;

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

        private bool NeedsScroll => ContentHeight > ListHeight;

        private int MaxScroll => Math.Max(0, ContentHeight - ListHeight);

        private void ClampScroll()
        {
            int max = MaxScroll;
            int clamped = Math.Min(Math.Max(_scrollOffset, 0), max);
            if (clamped == _scrollOffset) return;
            _scrollOffset = clamped;
            Invalidate();
        }

        // ── Painting                                              ────

        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e);

            Graphics g = e.Graphics;
            g.Clear(Surface);
            g.SmoothingMode = SmoothingMode.AntiAlias;

            PaintHeader(g);

            var listArea = new Rectangle(
                0,
                ListTop,
                NeedsScroll ? Math.Max(0, ClientSize.Width - ScrollBarWidth) : ClientSize.Width,
                ListHeight);

            Region savedClip = g.Clip;
            g.SetClip(listArea);
            for (int i = 0; i < VisibleRowCount; i++)
                PaintRow(g, i, listArea);
            g.Clip = savedClip;

            PaintDropZone(g);

            if (NeedsScroll) PaintScrollBar(g, listArea);
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
                ClientSize.Width - ColumnInset * 2,
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
                DrawConfirmGlyph(g, CreateCancelRect(rect), "✕", nameColor, HitZone.CreateCancel, hover);
                DrawConfirmGlyph(g, CreateOkRect(rect), "✓", nameColor, HitZone.CreateOk, hover);
            }
            else
            {
                int countLeft = DrawCountBadge(g, rect, index, countColor, actionsVisible);

                if (actionsVisible)
                {
                    Color actionColor = onAccent ? OnAccentAction : TextMuted;
                    DrawRowAction(g, DeleteRect(rect), "✕", actionColor, index, HitZone.Delete, onAccent);
                    DrawRowAction(g, RenameRect(rect), "✎", actionColor, index, HitZone.Rename, onAccent);
                }

                if (_editMode == EditMode.ConfirmDelete && isEditing)
                    DrawDeleteConfirmation(g, rect, onAccent);
                else
                    DrawName(g, rect, RowAt(index).Name, nameColor, countLeft);
            }
        }

        private void DrawName(Graphics g, Rectangle rect, string name, Color color, int rightLimit)
        {
            TextRenderer.DrawText(
                g,
                name ?? string.Empty,
                _rowFont,
                new Rectangle(
                    rect.Left + RowPadding,
                    rect.Top,
                    Math.Max(0, rightLimit - rect.Left - RowPadding * 2),
                    rect.Height),
                color,
                TextFormatFlags.SingleLine | TextFormatFlags.VerticalCenter |
                TextFormatFlags.EndEllipsis | TextFormatFlags.NoPrefix | TextFormatFlags.NoClipping);
        }

        /// <summary>Draws the per-folder file count and returns the x it starts at.</summary>
        private int DrawCountBadge(Graphics g, Rectangle rect, int index, Color color, bool actionsVisible)
        {
            if (_editMode == EditMode.ConfirmDelete && _editRow == index)
                return rect.Left + RowPadding;

            int right = ClientSize.Width - ColumnInset;
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

        private void DrawRowAction(Graphics g, Rectangle rect, string glyph, Color color, int index, HitZone zone, bool isSelected)
        {
            HitZone hover = _hoverRow == index ? _hoverZone : HitZone.None;

            if (hover == zone)
            {
                // Inset from the glyph box so the hover pad does not run into its neighbour.
                const int pad = 2;
                var hoverFill = new Rectangle(
                    rect.Left + pad,
                    rect.Top + pad,
                    Math.Max(0, rect.Width - pad * 2),
                    Math.Max(0, rect.Height - pad * 2));
                using (var brush = new SolidBrush(isSelected ? OnAccentActionHover : SurfaceHover))
                    FillRounded(g, hoverFill, 4, brush);
            }

            TextRenderer.DrawText(
                g,
                glyph,
                _glyphFont,
                rect,
                hover == zone && !isSelected ? TextPrimary : color,
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
            var noRect = ConfirmNoRect(rect);
            var yesRect = ConfirmYesRect(rect);
            int labelWidth = TextRenderer.MeasureText(g, DeletePrompt, _deleteFont).Width;
            var labelRect = new Rectangle(yesRect.Left - ActionGap - labelWidth, rect.Top, labelWidth, rect.Height);

            DrawName(g, rect, RowAt(_editRow).Name, isSelected ? Color.White : TextPrimary, labelRect.Left - ActionGap);

            TextRenderer.DrawText(
                g,
                DeletePrompt,
                _deleteFont,
                labelRect,
                isSelected ? OnAccentConfirm : Danger,
                TextFormatFlags.SingleLine | TextFormatFlags.VerticalCenter | TextFormatFlags.NoPrefix);

            DrawRowAction(g, yesRect, "Yes", _hoverZone == HitZone.ConfirmYes ? Accent : (isSelected ? OnAccentAction : TextMuted), _editRow, HitZone.ConfirmYes, isSelected);
            DrawRowAction(g, noRect, "No", _hoverZone == HitZone.ConfirmNo ? TextPrimary : (isSelected ? OnAccentAction : TextMuted), _editRow, HitZone.ConfirmNo, isSelected);
        }

        private void PaintScrollBar(Graphics g, Rectangle listArea)
        {
            int max = MaxScroll;
            if (max <= 0) return;

            var track = new Rectangle(ClientSize.Width - ScrollBarWidth, listArea.Top, ScrollBarWidth, listArea.Height);
            using (var brush = new SolidBrush(SurfaceHover))
                FillRounded(g, track, 3, brush);

            float ratio = (float)listArea.Height / ContentHeight;
            int thumbHeight = Math.Max(24, (int)(listArea.Height * ratio));
            int travel = listArea.Height - thumbHeight;
            int thumbTop = listArea.Top + (int)(travel * (_scrollOffset / (float)max));

            var thumb = new Rectangle(track.X, thumbTop, track.Width, thumbHeight);
            using (var brush = new SolidBrush(Accent))
                FillRounded(g, thumb, 3, brush);
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
            new Rectangle(0, ListTop + index * RowHeight - _scrollOffset, ClientSize.Width, RowHeight);

        private Rectangle RowHitRect(int index) =>
            new Rectangle(0, RowRect(index).Top, ClientSize.Width, RowHeight);

        private FolderRow RowAt(int index) =>
            index >= 0 && index < _rows.Count ? _rows[index] : null;

        private Rectangle RenameRect(Rectangle row) =>
            new Rectangle(
                ClientSize.Width - ColumnInset - ActionSize * 2 - ActionGap,
                row.Top,
                ActionSize,
                row.Height);

        private Rectangle DeleteRect(Rectangle row) =>
            new Rectangle(
                ClientSize.Width - ColumnInset - ActionSize,
                row.Top,
                ActionSize,
                row.Height);

        private Rectangle CreateOkRect(Rectangle row) =>
            new Rectangle(ClientSize.Width - ColumnInset - ActionSize, row.Top, ActionSize, row.Height);

        private Rectangle CreateCancelRect(Rectangle row) =>
            new Rectangle(ClientSize.Width - ColumnInset - ActionSize * 2, row.Top, ActionSize, row.Height);

        private Rectangle ConfirmNoRect(Rectangle row) =>
            new Rectangle(ClientSize.Width - ColumnInset - ActionSize, row.Top, ActionSize, row.Height);

        private Rectangle ConfirmYesRect(Rectangle row) =>
            new Rectangle(ClientSize.Width - ColumnInset - ActionSize * 2 - ActionGap, row.Top, ActionSize, row.Height);

        /// <summary>
        /// Row index under <paramref name="point"/>, or -1 when the point is not on a
        /// visible row.
        /// </summary>
        private int HitTestRow(Point point)
        {
            if (point.Y < ListTop) return -1;

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
                    if (ConfirmYesRect(row).Contains(point)) return HitZone.ConfirmYes;
                    if (ConfirmNoRect(row).Contains(point)) return HitZone.ConfirmNo;
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

            DropRegion region = HitTestRegion(e.Location);
            RefreshHover(e.Location);

            // The strip is a button, so it says so; rows keep the default arrow.
            Cursor = _locked
                ? Cursors.No
                : region == DropRegion.Panel ? Cursors.Hand : Cursors.Default;
        }

        protected override void OnMouseLeave(EventArgs e)
        {
            base.OnMouseLeave(e);
            if (_disposed) return;

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

            // A click outside the row being edited abandons it, the way clicking another
            // folder did when this list was web-rendered.
            if (_editMode != EditMode.None && !IsInsideEditRow(index))
            {
                CancelEdit();
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
            if (_disposed || e.Button != MouseButtons.Left)
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
                case HitZone.ConfirmYes:
                    {
                        string id = RowAt(_editRow)?.Id;
                        CancelEdit();
                        if (id != null) FolderRemoveRequested?.Invoke(id);
                        break;
                    }
                case HitZone.ConfirmNo:
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
            if (e.KeyCode == Keys.Enter)
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

            // Focus left the sidebar entirely — abandoning the edit beats leaving a
            // half-typed folder name behind in a row the user cannot see focus in.
            CancelEdit();
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
            ClampScroll();
            Invalidate();
        }

        private void HideEditBox()
        {
            _editHost.Visible = false;
            _editBox.Text = string.Empty;
            _holdFocus = 0;
            // Removing the virtual create row can leave the list scrolled past its end.
            ClampScroll();
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
                ClientSize.Width - ColumnInset * 2,
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

            _scrollOffset = Math.Min(Math.Max(_scrollOffset, 0), MaxScroll);
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

            if (_dropRegion != region || _dropRow != row)
            {
                DropRegion previousRegion = _dropRegion;
                int previousRow = _dropRow;
                _dropRegion = region;
                _dropRow = row;

                InvalidateRow(previousRow);
                InvalidateRow(row);
                if (previousRegion != region) InvalidateDropZone();
            }

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

        // ── Scrolling                                             ────

        private void OnSidebarMouseWheel(object sender, MouseEventArgs e)
        {
            if (_disposed) return;

            _scrollOffset -= e.Delta;
            int clamped = Math.Min(Math.Max(_scrollOffset, 0), MaxScroll);
            if (clamped == _scrollOffset) return;

            _scrollOffset = clamped;
            PositionEditBox();
            Invalidate();
        }

        // ── Helpers                                               ────

        private void InvalidateRow(int index)
        {
            if (index < 0 || _disposed) return;
            Invalidate(new Rectangle(0, RowRect(index).Top, ClientSize.Width, RowHeight));
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
