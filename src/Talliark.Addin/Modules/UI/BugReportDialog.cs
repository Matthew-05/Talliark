using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Windows.Forms;
using Talliark.Addin.Modules.Services;

namespace Talliark.Addin.Modules.UI
{
    /// <summary>Collects a bug report before attachment consent and save checks.</summary>
    internal sealed class BugReportDialog : Form
    {
        private const int CardInset = 18;
        private const int FieldWidth = 484;
        private const int FooterHeight = 52;

        private readonly BugReportDraft _draft;
        private readonly CardPanel _card;
        private readonly Label _bugTypeLabel;
        private readonly ComboBox _bugType;
        private readonly Label _reconcileSubtypeLabel;
        private readonly ComboBox _reconcileSubtype;
        private readonly Label _descriptionLabel;
        private readonly TextBox _description;
        private readonly Label _attachmentsLabel;
        private readonly Label _additionalFilesLabel;
        private readonly TextBox _additionalFilesSelectText;
        private readonly Button _browseAdditionalFiles;
        private readonly FlowLayoutPanel _additionalFileRows;
        private readonly Label _originalFilesLabel;
        private readonly TextBox _originalFilesSelectText;
        private readonly Button _browseOriginalFiles;
        private readonly FlowLayoutPanel _originalFileRows;
        private readonly CheckBox _includeWorkbook;
        private readonly CheckBox _includeLog;
        private readonly Button _nextButton;
        private readonly List<string> _additionalFiles;
        private readonly List<string> _originalFilePaths;

        internal BugReportDialog(BugReportDraft draft, bool workbookAvailable)
        {
            _draft = draft ?? throw new ArgumentNullException(nameof(draft));
            _additionalFiles = new List<string>(draft.AdditionalFiles);
            _originalFilePaths = new List<string>(draft.OriginalFilePaths);

            Text = "Report a Talliark Bug";
            FormBorderStyle = FormBorderStyle.FixedDialog;
            StartPosition = FormStartPosition.CenterParent;
            MaximizeBox = false;
            MinimizeBox = false;
            ShowInTaskbar = false;
            ClientSize = new Size(560, 560);
            BackColor = DialogTheme.Canvas;
            Font = DialogTheme.BodyFont;
            ForeColor = DialogTheme.Text;

            Controls.Add(DialogTheme.CreateTitle("Report a bug", new Point(22, 18)));
            Controls.Add(DialogTheme.CreateCaption(
                "Describe what happened. You can review the email before anything is sent.",
                new Point(24, 50),
                510));

            _card = new CardPanel
            {
                Location = new Point(20, 82),
                Size = new Size(520, 400)
            };
            Controls.Add(_card);

            _bugTypeLabel = DialogTheme.CreateSubHeading("Bug type", Point.Empty);
            _card.Controls.Add(_bugTypeLabel);
            _bugType = new ComboBox
            {
                Size = new Size(FieldWidth, 26),
                DropDownStyle = ComboBoxStyle.DropDownList
            };
            _bugType.Items.AddRange(BugReportTypes.All);
            int selectedIndex = Array.IndexOf(BugReportTypes.All, draft.BugType);
            _bugType.SelectedIndex = selectedIndex >= 0 ? selectedIndex : 0;
            _card.Controls.Add(_bugType);

            _reconcileSubtypeLabel = DialogTheme.CreateSubHeading("Reconcile subtype", Point.Empty);
            _card.Controls.Add(_reconcileSubtypeLabel);
            _reconcileSubtype = new ComboBox
            {
                Size = new Size(FieldWidth, 26),
                DropDownStyle = ComboBoxStyle.DropDownList
            };
            _reconcileSubtype.Items.AddRange(BugReportTypes.ReconcileSubtypes);
            int subtypeIndex = Array.IndexOf(
                BugReportTypes.ReconcileSubtypes,
                draft.ReconcileSubtype);
            _reconcileSubtype.SelectedIndex = subtypeIndex >= 0 ? subtypeIndex : 0;
            _card.Controls.Add(_reconcileSubtype);

            _descriptionLabel = DialogTheme.CreateSubHeading("Description", Point.Empty);
            _card.Controls.Add(_descriptionLabel);
            _description = new TextBox
            {
                Size = new Size(FieldWidth, 110),
                Multiline = true,
                AcceptsReturn = true,
                ScrollBars = ScrollBars.Vertical,
                MaxLength = 8000,
                Text = draft.Description ?? string.Empty
            };
            _card.Controls.Add(_description);

            _attachmentsLabel = DialogTheme.CreateSubHeading("Attachments", Point.Empty);
            _card.Controls.Add(_attachmentsLabel);
            _additionalFilesLabel = CreateFieldLabel("Additional files");
            _card.Controls.Add(_additionalFilesLabel);
            _additionalFilesSelectText = CreateFileSelectText("Select files to attach");
            _card.Controls.Add(_additionalFilesSelectText);
            _browseAdditionalFiles = CreateFileButton("Browse...");
            _browseAdditionalFiles.Click += BrowseAdditionalFiles;
            _card.Controls.Add(_browseAdditionalFiles);
            _additionalFileRows = CreateFileRowsPanel();
            _card.Controls.Add(_additionalFileRows);

            _originalFilesLabel = CreateFieldLabel("Original files");
            _card.Controls.Add(_originalFilesLabel);
            _originalFilesSelectText = CreateFileSelectText("Select original files");
            _card.Controls.Add(_originalFilesSelectText);
            _browseOriginalFiles = CreateFileButton("Browse...");
            _browseOriginalFiles.Click += BrowseOriginalFiles;
            _card.Controls.Add(_browseOriginalFiles);
            _originalFileRows = CreateFileRowsPanel();
            _card.Controls.Add(_originalFileRows);

            _includeWorkbook = new CheckBox
            {
                AutoSize = true,
                Text = workbookAvailable
                    ? "Include active Excel workbook"
                    : "Include active Excel workbook (no workbook is open)",
                Checked = workbookAvailable && draft.IncludeWorkbook,
                Enabled = workbookAvailable,
                Cursor = workbookAvailable ? Cursors.Hand : Cursors.Default
            };
            _card.Controls.Add(_includeWorkbook);

            _includeLog = new CheckBox
            {
                AutoSize = true,
                Text = "Include current Talliark log",
                Checked = draft.IncludeLog,
                Cursor = Cursors.Hand
            };
            _card.Controls.Add(_includeLog);

            var footer = new Panel
            {
                Dock = DockStyle.Bottom,
                Height = FooterHeight,
                BackColor = DialogTheme.Surface
            };
            footer.Controls.Add(new Panel
            {
                Dock = DockStyle.Top,
                Height = 1,
                BackColor = DialogTheme.Border
            });
            Controls.Add(footer);

            var cancelButton = new Button
            {
                Text = "Cancel",
                DialogResult = DialogResult.Cancel,
                Size = new Size(96, 30),
                Location = new Point(340, 12)
            };
            DialogTheme.StyleSecondaryButton(cancelButton);
            footer.Controls.Add(cancelButton);

            _nextButton = new Button
            {
                Text = "Next",
                DialogResult = DialogResult.OK,
                Size = new Size(96, 30),
                Location = new Point(444, 12)
            };
            DialogTheme.StylePrimaryButton(_nextButton);
            footer.Controls.Add(_nextButton);

            AcceptButton = _nextButton;
            CancelButton = cancelButton;
            _description.TextChanged += (sender, args) => UpdateNextEnabled();
            _bugType.SelectedIndexChanged += (sender, args) => LayoutFields();
            UpdateFileRows();
            UpdateNextEnabled();

            FormClosing += SaveDraft;
            Shown += (sender, args) => _description.Focus();
        }

        private static TextBox CreateFileSelectText(string text)
        {
            return new TextBox
            {
                Size = new Size(404, 26),
                ReadOnly = true,
                BackColor = DialogTheme.Surface,
                ForeColor = DialogTheme.MutedText,
                Text = text
            };
        }

        private static FlowLayoutPanel CreateFileRowsPanel()
        {
            return new FlowLayoutPanel
            {
                AutoSize = true,
                AutoSizeMode = AutoSizeMode.GrowAndShrink,
                FlowDirection = FlowDirection.TopDown,
                WrapContents = false,
                Margin = Padding.Empty,
                Padding = Padding.Empty,
                MinimumSize = new Size(FieldWidth, 0),
                MaximumSize = new Size(FieldWidth, 0)
            };
        }

        private static Label CreateFieldLabel(string text)
        {
            return new Label
            {
                Text = text,
                AutoSize = true,
                Font = DialogTheme.BodyFont,
                ForeColor = DialogTheme.Text
            };
        }

        private static Button CreateFileButton(string text)
        {
            var button = new Button
            {
                Text = text,
                Size = new Size(76, 26)
            };
            DialogTheme.StyleSecondaryButton(button);
            return button;
        }

        private void BrowseAdditionalFiles(object sender, EventArgs e)
        {
            using (var picker = new OpenFileDialog
            {
                Title = "Attach files to the bug report",
                Filter = "All files (*.*)|*.*",
                Multiselect = true,
                CheckFileExists = true
            })
            {
                if (picker.ShowDialog(this) != DialogResult.OK) return;
                AddUniquePaths(_additionalFiles, picker.FileNames);
                UpdateFileRows();
            }
        }

        private void BrowseOriginalFiles(object sender, EventArgs e)
        {
            using (var picker = new OpenFileDialog
            {
                Title = "Attach original files",
                Filter = "All files (*.*)|*.*",
                Multiselect = true,
                CheckFileExists = true
            })
            {
                if (picker.ShowDialog(this) != DialogResult.OK) return;
                AddUniquePaths(_originalFilePaths, picker.FileNames);
                UpdateFileRows();
            }
        }

        private static void AddUniquePaths(IList<string> target, IEnumerable<string> paths)
        {
            foreach (string path in paths ?? Enumerable.Empty<string>())
            {
                if (target.Any(existing =>
                    string.Equals(existing, path, StringComparison.OrdinalIgnoreCase)))
                {
                    continue;
                }
                target.Add(path);
            }
        }

        private void UpdateFileRows()
        {
            PopulateFileRows(_additionalFileRows, _additionalFiles);
            PopulateFileRows(_originalFileRows, _originalFilePaths);
            LayoutFields();
        }

        private void PopulateFileRows(
            FlowLayoutPanel rowsPanel,
            IList<string> paths)
        {
            rowsPanel.SuspendLayout();
            try
            {
                rowsPanel.Controls.Clear();
                foreach (string path in paths)
                    rowsPanel.Controls.Add(CreateFileRow(path, paths));
            }
            finally
            {
                rowsPanel.ResumeLayout(true);
            }
        }

        private Control CreateFileRow(string path, IList<string> source)
        {
            var row = new Panel
            {
                Size = new Size(FieldWidth, 24),
                Margin = new Padding(0, 0, 0, 2),
                BackColor = Color.Transparent,
                BorderStyle = BorderStyle.None
            };

            var name = new Label
            {
                Text = "\u2022  " + Path.GetFileName(path),
                AutoEllipsis = true,
                Location = new Point(8, 3),
                Size = new Size(FieldWidth - 48, 18),
                Font = DialogTheme.BodyFont,
                ForeColor = DialogTheme.Text
            };
            row.Controls.Add(name);

            var remove = new Button
            {
                Text = "X",
                AccessibleName = "Remove " + Path.GetFileName(path),
                Size = new Size(24, 22),
                Location = new Point(FieldWidth - 28, 0),
                FlatStyle = FlatStyle.Flat,
                BackColor = Color.Transparent,
                ForeColor = DialogTheme.MutedText,
                Cursor = Cursors.Hand,
                TabStop = true,
                UseVisualStyleBackColor = false
            };
            remove.FlatAppearance.BorderSize = 0;
            remove.FlatAppearance.MouseOverBackColor = DialogTheme.PressedFill;
            remove.Click += (sender, args) =>
            {
                source.Remove(path);
                UpdateFileRows();
            };
            row.Controls.Add(remove);

            return row;
        }

        private void LayoutFields()
        {
            bool showReconcile = SelectedBugType == BugReportTypes.Reconcile;
            bool showOriginalFiles = SelectedBugType == BugReportTypes.Ocr;

            int y = 17;
            PlaceLabelAndField(_bugTypeLabel, _bugType, ref y);

            _reconcileSubtypeLabel.Visible = showReconcile;
            _reconcileSubtype.Visible = showReconcile;
            if (showReconcile)
                PlaceLabelAndField(_reconcileSubtypeLabel, _reconcileSubtype, ref y);

            PlaceLabelAndField(_descriptionLabel, _description, ref y, 18);

            _attachmentsLabel.Location = new Point(CardInset, y);
            y = _attachmentsLabel.Bottom + 12;

            _originalFilesLabel.Visible = showOriginalFiles;
            _originalFilesSelectText.Visible = showOriginalFiles;
            _browseOriginalFiles.Visible = showOriginalFiles;
            _originalFileRows.Visible = showOriginalFiles;
            if (showOriginalFiles)
            {
                PlaceLabelAndFilePicker(
                    _originalFilesLabel,
                    _originalFilesSelectText,
                    _browseOriginalFiles,
                    _originalFileRows,
                    ref y);
            }

            PlaceLabelAndFilePicker(
                _additionalFilesLabel,
                _additionalFilesSelectText,
                _browseAdditionalFiles,
                _additionalFileRows,
                ref y);

            _includeWorkbook.Location = new Point(CardInset, y);
            y = _includeWorkbook.Bottom + 8;
            _includeLog.Location = new Point(CardInset, y);
            y = _includeLog.Bottom + CardInset;

            _card.Height = y;
            ClientSize = new Size(560, _card.Bottom + 20 + FooterHeight);
        }

        private static void PlaceLabelAndField(
            Control label,
            Control field,
            ref int y,
            int gapAfter = 16)
        {
            label.Location = new Point(CardInset, y);
            field.Location = new Point(CardInset, label.Bottom + 7);
            y = field.Bottom + gapAfter;
        }

        private static void PlaceLabelAndFilePicker(
            Control label,
            Control selectText,
            Control browse,
            Control rows,
            ref int y)
        {
            label.Location = new Point(CardInset, y);
            int rowTop = label.Bottom + 7;
            selectText.Location = new Point(CardInset, rowTop);
            browse.Location = new Point(selectText.Right + 4, rowTop);
            y = Math.Max(selectText.Bottom, browse.Bottom) + 6;

            rows.Location = new Point(CardInset, y);
            if (rows.Controls.Count > 0)
                y = rows.Bottom + 10;
            else
                y += 8;
        }

        private string SelectedBugType =>
            _bugType.SelectedItem?.ToString() ?? BugReportTypes.General;

        private void SaveDraft(object sender, FormClosingEventArgs e)
        {
            if (DialogResult != DialogResult.OK) return;

            _draft.BugType = SelectedBugType;
            _draft.Description = _description.Text.Trim();
            _draft.IncludeWorkbook = _includeWorkbook.Checked;
            _draft.IncludeLog = _includeLog.Checked;
            _draft.ReconcileSubtype = _reconcileSubtype.SelectedItem?.ToString()
                ?? BugReportTypes.ReconcileCreation;

            _draft.AdditionalFiles.Clear();
            foreach (string path in _additionalFiles)
                _draft.AdditionalFiles.Add(path);

            _draft.OriginalFilePaths.Clear();
            foreach (string path in _originalFilePaths)
                _draft.OriginalFilePaths.Add(path);
        }

        private void UpdateNextEnabled()
        {
            _nextButton.Enabled = !string.IsNullOrWhiteSpace(_description.Text);
        }
    }
}
