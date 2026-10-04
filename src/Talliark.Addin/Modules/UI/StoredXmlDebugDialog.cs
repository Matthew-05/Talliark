using System;
using System.Collections.Generic;
using System.Drawing;
using System.Runtime.InteropServices;
using System.Windows.Forms;
using Talliark.Addin.Modules.Services;

namespace Talliark.Addin.Modules.UI
{
    /// <summary>Read-only browser for the raw Talliark Custom XML in one workbook.</summary>
    internal sealed class StoredXmlDebugDialog : Form
    {
        private readonly IList<StoredXmlDebugEntry> _entries;
        private readonly ListBox _partList;
        private readonly Label _details;
        private readonly RichTextBox _xmlViewer;
        private readonly Button _copyButton;

        internal StoredXmlDebugDialog(
            string workbookName, IList<StoredXmlDebugEntry> entries)
        {
            _entries = entries ?? throw new ArgumentNullException(nameof(entries));

            Text = "Talliark Stored XML";
            FormBorderStyle = FormBorderStyle.Sizable;
            MinimumSize = new Size(720, 480);
            ClientSize = new Size(920, 640);
            MaximizeBox = true;
            MinimizeBox = false;
            ShowInTaskbar = false;
            StartPosition = FormStartPosition.CenterParent;
            BackColor = DialogTheme.Canvas;
            Font = DialogTheme.BodyFont;
            ForeColor = DialogTheme.Text;

            var layout = new TableLayoutPanel
            {
                Dock = DockStyle.Fill,
                ColumnCount = 1,
                RowCount = 3,
                BackColor = DialogTheme.Canvas,
                Padding = Padding.Empty
            };
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 72));
            layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 56));
            Controls.Add(layout);

            var header = new Panel
            {
                Dock = DockStyle.Fill,
                BackColor = DialogTheme.Canvas
            };
            header.Controls.Add(DialogTheme.CreateTitle(
                "Stored XML", new Point(22, 16)));
            header.Controls.Add(DialogTheme.CreateCaption(
                string.IsNullOrWhiteSpace(workbookName)
                    ? "Read-only snapshot of this workbook's Talliark Custom XML parts."
                    : $"Read-only snapshot of Talliark Custom XML parts in {workbookName}.",
                new Point(24, 45)));
            layout.Controls.Add(header, 0, 0);

            var split = new SplitContainer
            {
                Dock = DockStyle.Fill,
                SplitterDistance = 230,
                FixedPanel = FixedPanel.Panel1,
                BackColor = DialogTheme.Border,
                Padding = new Padding(20, 0, 20, 0)
            };
            layout.Controls.Add(split, 0, 1);

            _partList = new ListBox
            {
                Dock = DockStyle.Fill,
                BorderStyle = BorderStyle.FixedSingle,
                Font = DialogTheme.BodyFont,
                ForeColor = DialogTheme.Text,
                BackColor = DialogTheme.Surface,
                IntegralHeight = false,
                HorizontalScrollbar = true
            };
            _partList.SelectedIndexChanged += SelectPart;
            split.Panel1.Controls.Add(_partList);

            var viewerLayout = new TableLayoutPanel
            {
                Dock = DockStyle.Fill,
                ColumnCount = 1,
                RowCount = 2,
                BackColor = DialogTheme.Surface,
                Padding = Padding.Empty
            };
            viewerLayout.RowStyles.Add(new RowStyle(SizeType.Absolute, 44));
            viewerLayout.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            split.Panel2.Controls.Add(viewerLayout);

            _details = new Label
            {
                Dock = DockStyle.Fill,
                Padding = new Padding(10, 7, 10, 4),
                Font = DialogTheme.CaptionFont,
                ForeColor = DialogTheme.MutedText,
                BackColor = DialogTheme.Surface,
                AutoEllipsis = true
            };
            viewerLayout.Controls.Add(_details, 0, 0);

            _xmlViewer = new RichTextBox
            {
                Dock = DockStyle.Fill,
                BorderStyle = BorderStyle.FixedSingle,
                BackColor = DialogTheme.Surface,
                ForeColor = DialogTheme.Text,
                Font = new Font("Consolas", 9f),
                ReadOnly = true,
                DetectUrls = false,
                WordWrap = false
            };
            viewerLayout.Controls.Add(_xmlViewer, 0, 1);

            var footer = new Panel
            {
                Dock = DockStyle.Fill,
                BackColor = DialogTheme.Surface
            };
            footer.Controls.Add(new Panel
            {
                Dock = DockStyle.Top,
                Height = 1,
                BackColor = DialogTheme.Border
            });

            footer.Controls.Add(DialogTheme.CreateCaption(
                $"{_entries.Count} Talliark XML part{(_entries.Count == 1 ? string.Empty : "s")}",
                new Point(22, 21)));

            var closeButton = new Button
            {
                Text = "Close",
                DialogResult = DialogResult.Cancel,
                Size = new Size(96, 30),
                Margin = Padding.Empty
            };
            DialogTheme.StyleSecondaryButton(closeButton);

            _copyButton = new Button
            {
                Text = "Copy XML",
                Size = new Size(106, 30),
                Margin = new Padding(0, 0, 10, 0)
            };
            DialogTheme.StyleSecondaryButton(_copyButton);
            _copyButton.Click += CopySelectedXml;

            var footerButtons = new FlowLayoutPanel
            {
                Dock = DockStyle.Right,
                Width = 232,
                Padding = new Padding(0, 13, 20, 0),
                FlowDirection = FlowDirection.RightToLeft,
                WrapContents = false,
                BackColor = DialogTheme.Surface
            };
            footerButtons.Controls.Add(closeButton);
            footerButtons.Controls.Add(_copyButton);
            footer.Controls.Add(footerButtons);
            layout.Controls.Add(footer, 0, 2);

            CancelButton = closeButton;

            foreach (StoredXmlDebugEntry entry in _entries)
                _partList.Items.Add(entry.DisplayName);

            if (_entries.Count > 0)
            {
                _partList.SelectedIndex = 0;
            }
            else
            {
                _details.Text = "No Talliark Custom XML parts were found.";
                _xmlViewer.Text = "This workbook does not currently contain stored Talliark XML data.";
                _copyButton.Enabled = false;
            }
        }

        private void SelectPart(object sender, EventArgs e)
        {
            int index = _partList.SelectedIndex;
            if (index < 0 || index >= _entries.Count) return;

            StoredXmlDebugEntry entry = _entries[index];
            _details.Text =
                $"{entry.NamespaceUri}\r\n{entry.StoredCharacterCount:N0} stored characters";
            _xmlViewer.Text = entry.Xml;
            _xmlViewer.SelectionStart = 0;
            _xmlViewer.SelectionLength = 0;
            _xmlViewer.ScrollToCaret();
            _copyButton.Enabled = entry.Xml.Length > 0;
        }

        private void CopySelectedXml(object sender, EventArgs e)
        {
            int index = _partList.SelectedIndex;
            if (index < 0 || index >= _entries.Count || _entries[index].Xml.Length == 0)
                return;

            try
            {
                Clipboard.SetText(_entries[index].Xml);
            }
            catch (ExternalException ex)
            {
                TalliarkLog.Trace($"Could not copy stored XML to the clipboard: {ex.Message}");
                MessageBox.Show(
                    this,
                    "The stored XML could not be copied to the clipboard.",
                    "Talliark",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error);
            }
        }
    }
}
