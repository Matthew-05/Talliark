using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Windows.Forms;
using Talliark.Addin.Modules.Infrastructure;
using Talliark.Addin.Modules.Services;

namespace Talliark.Addin.Modules.UI
{
    internal sealed class SettingsDialog : Form
    {
        /// <summary>Gap between the window edge and the cards.</summary>
        private const int ContentMargin = 20;

        /// <summary>Inner padding used by every card.</summary>
        private const int CardPadding = 16;

        /// <summary>Vertical gap between stacked cards.</summary>
        private const int CardGap = 16;

        /// <summary>Where the Experimental card's flowed rows start, below its heading.</summary>
        private const int ExperimentalRowsTop = 62;

        /// <summary>Where the Development card's flowed rows start, below its heading.</summary>
        private const int DevelopmentRowsTop = 82;

        /// <summary>How far a caption is indented under the switch it explains.</summary>
        private const int CaptionIndent = 18;

        /// <summary>Gap between a switch and its caption.</summary>
        private const int CaptionGap = 2;

        /// <summary>Gap between one switch-and-caption row and the next.</summary>
        private const int RowGap = 12;

        /// <summary>Height of the action bar holding the Close button.</summary>
        private const int FooterHeight = 56;

        /// <summary>Where the release-history viewer starts inside the Updates card.</summary>
        private const int UpdateHistoryTop = 90;

        /// <summary>Least vertical room the release-history viewer is worth showing in.</summary>
        private const int MinimumUpdateHistoryHeight = 280;

        /// <summary>Smallest Updates card that preserves the history viewer's minimum.</summary>
        private const int MinimumUpdatesHeight =
            UpdateHistoryTop + MinimumUpdateHistoryHeight + CardPadding;

        private readonly ReleaseNotesControl _updateHistory;
        private readonly Panel _content;
        private readonly CardPanel _experimentalCard;
        private readonly CardPanel _developmentCard;
        private readonly CardPanel _updatesCard;
        private bool _layingOutContent;
        private bool _updateHistoryRequested;

        internal SettingsDialog()
        {
            bool showDevelopmentSection = AppVersion.IsDevelopment || AppVersion.IsBeta;

            Text = "Talliark Settings";
            // Border style first: changing it after ClientSize would resize the client area.
            FormBorderStyle = FormBorderStyle.Sizable;
            MinimumSize = new Size(620, 600);
            ClientSize = new Size(660, 720);
            MaximizeBox = false;
            MinimizeBox = false;
            StartPosition = FormStartPosition.CenterParent;
            BackColor = DialogTheme.Canvas;
            Font = DialogTheme.BodyFont;
            ForeColor = DialogTheme.Text;

            _content = new Panel
            {
                Dock = DockStyle.Fill,
                AutoScroll = true,
                BackColor = DialogTheme.Canvas
            };
            Controls.Add(_content);

            Button closeBtn;
            Panel footer = BuildFooter(out closeBtn);
            Controls.Add(footer);

            // Docking gives both regions their real bounds only once they have a
            // parent. The footer stays fixed while the content above it scrolls.
            PerformLayout();

            closeBtn.Location = new Point(
                footer.ClientSize.Width - ContentMargin - closeBtn.Width, 13);

            int cardWidth = _content.ClientSize.Width - (ContentMargin * 2);

            _content.Controls.Add(DialogTheme.CreateTitle(
                "Settings", new Point(ContentMargin + 2, 20)));
            _content.Controls.Add(DialogTheme.CreateCaption(
                $"Version {AppVersion.Current}", new Point(ContentMargin + 4, 51)));

            int nextCardTop = 82;
            _experimentalCard = BuildExperimentalCard(new Point(ContentMargin, nextCardTop), cardWidth);
            _content.Controls.Add(_experimentalCard);
            // Wrapping makes the card's height depend on the dialog's width, so the cards
            // below it cannot keep a fixed top.
            _experimentalCard.SizeChanged += (sender, args) => LayoutContent();
            nextCardTop += _experimentalCard.Height + CardGap;

            _developmentCard = null;
            if (showDevelopmentSection)
            {
                _developmentCard = BuildDevelopmentCard(
                    new Point(ContentMargin, nextCardTop), cardWidth);
                _content.Controls.Add(_developmentCard);
                nextCardTop += _developmentCard.Height + CardGap;
            }

            // The updates card takes the remaining height so the release history
            // grows with the viewport instead of leaving dead space below it. At a
            // shorter height it keeps its useful minimum and the content panel
            // supplies the scrollbar.
            int updatesHeight = Math.Max(
                MinimumUpdatesHeight,
                _content.ClientSize.Height - ContentMargin - nextCardTop);

            _updatesCard = BuildUpdatesCard(
                new Point(ContentMargin, nextCardTop),
                new Size(cardWidth, updatesHeight),
                out _updateHistory);
            _content.Controls.Add(_updatesCard);

            if (_developmentCard != null)
            {
                // Wrapping makes the development card's height depend on the dialog's
                // width, so the updates card below it cannot keep a fixed top.
                _developmentCard.SizeChanged += (sender, args) => LayoutContent();
            }

            _content.SizeChanged += (sender, args) => LayoutContent();
            LayoutContent();

            CancelButton = closeBtn;
        }

        /// <summary>
        /// Fit the cards to the scrollable viewport and keep enough virtual height
        /// for every card to remain reachable when the window is short.
        /// </summary>
        private void LayoutContent()
        {
            if (_layingOutContent || _content == null || _updatesCard == null) return;

            _layingOutContent = true;
            try
            {
                int cardWidth = Math.Max(
                    1, _content.ClientSize.Width - (ContentMargin * 2));

                _experimentalCard.Width = cardWidth;
                int updatesTop = _experimentalCard.Bottom + CardGap;
                if (_developmentCard != null)
                {
                    _developmentCard.Width = cardWidth;
                    updatesTop = _developmentCard.Bottom + CardGap;
                }

                _updatesCard.Location = new Point(ContentMargin, updatesTop);
                _updatesCard.Size = new Size(
                    cardWidth,
                    Math.Max(
                        MinimumUpdatesHeight,
                        _content.ClientSize.Height - ContentMargin - updatesTop));

                _content.AutoScrollMinSize = new Size(
                    0, _updatesCard.Bottom + ContentMargin);
            }
            finally
            {
                _layingOutContent = false;
            }
        }

        private CardPanel BuildExperimentalCard(Point location, int width)
        {
            var card = new CardPanel
            {
                Location = location,
                Size = new Size(width, ExperimentalRowsTop),
                Anchor = AnchorStyles.Top | AnchorStyles.Left
            };
            card.Controls.Add(DialogTheme.CreateSectionTitle("Experimental", new Point(CardPadding, 14)));
            card.Controls.Add(DialogTheme.CreateSeparator(new Point(CardPadding, 46), width - (CardPadding * 2)));

            AddFlowedToggle(
                card,
                "Table Detection",
                "Detects table structure during ordinary OCR and enables detected-table assistance in the document viewer. " +
                "Reconcile always detects tables. Manually drawn table rectangles and their grids remain available.",
                ExperimentalSettings.TableDetection,
                value => ExperimentalSettings.TableDetection = value);

            LayoutFlowedRows(card, ExperimentalRowsTop);
            int laidOutAt = card.ClientSize.Width;
            card.SizeChanged += (sender, args) =>
            {
                if (card.ClientSize.Width == laidOutAt) return;
                laidOutAt = card.ClientSize.Width;
                LayoutFlowedRows(card, ExperimentalRowsTop);
            };

            return card;
        }

        /// <summary>Developer-only card; shown in development and beta builds.</summary>
        /// <remarks>
        /// The rows are laid out by flowing rather than by fixed coordinates. Every
        /// caption wraps at the card's width, so its height depends on how wide the
        /// dialog is, and a hard-coded Y for the row beneath it would overlap as soon
        /// as one line became two. <see cref="LayoutFlowedRows"/> re-runs on
        /// every width change and gives the card the height its content needs.
        /// </remarks>
        private CardPanel BuildDevelopmentCard(Point location, int width)
        {
            var card = new CardPanel
            {
                Location = location,
                Size = new Size(width, DevelopmentRowsTop),
                Anchor = AnchorStyles.Top | AnchorStyles.Left
            };

            card.Controls.Add(DialogTheme.CreateSectionTitle(
                "Development", new Point(CardPadding, 14)));

            card.Controls.Add(DialogTheme.CreateSeparator(
                new Point(CardPadding, 46), width - (CardPadding * 2)));

            card.Controls.Add(DialogTheme.CreateSubHeading(
                "Debugging tools", new Point(CardPadding + 2, 60)));

            AddFlowedToggle(
                card,
                "Show character bounding boxes",
                "Draws the per-character text boxes over every page in the document viewer.",
                DevSettings.ShowCharBoundingBoxes,
                value => DevSettings.ShowCharBoundingBoxes = value);

            AddFlowedToggle(
                card,
                "Show detected-value debug boxes",
                "Values measure something. Click targets are always active; this draws "
                + "every detected target box.",
                DevSettings.ShowValues,
                value => DevSettings.ShowValues = value);

            AddFlowedToggle(
                card,
                "Show reference boxes",
                "References identify something outside the document — an invoice number, "
                + "a phone number, a note citation. They are click targets either way; "
                + "this draws their boxes.",
                DevSettings.ShowReferences,
                value => DevSettings.ShowReferences = value);

            AddFlowedToggle(
                card,
                "Show structure boxes",
                "Structure is the document indexing itself — a note heading's number, a "
                + "contents-row page number, the ordinal opening a footnote. Nothing in "
                + "it is a click target today.",
                DevSettings.ShowStructure,
                value => DevSettings.ShowStructure = value);

            AddFlowedToggle(
                card,
                "Show refused spans",
                "Draws what is left once values, references and structure are taken: "
                + "damaged tokens, running headers, statute years.",
                DevSettings.ShowValueNoise,
                value => DevSettings.ShowValueNoise = value);

            AddFlowedToggle(
                card,
                "Show html context menu",
                "Shows the browser's own right-click menu. Off by default: the document "
                + "viewer and file manager draw their own menus. Turn on to reach the "
                + "built-in copy, save and inspect items.",
                ExperimentalSettings.BrowserContextMenu,
                value => ExperimentalSettings.BrowserContextMenu = value,
                NotifyContextMenuRestartRequired);

            var openLogsBtn = new Button
            {
                Text = "Open Log Folder",
                Size = new Size(140, 30)
            };
            DialogTheme.StyleSecondaryButton(openLogsBtn);
            openLogsBtn.Click += OpenLogFolder;

            var inspectXmlBtn = new Button
            {
                Text = "Inspect Stored XML",
                Size = new Size(150, 30)
            };
            DialogTheme.StyleSecondaryButton(inspectXmlBtn);
            inspectXmlBtn.Click += OpenStoredXml;

            var debugActions = new FlowLayoutPanel
            {
                AutoSize = true,
                AutoSizeMode = AutoSizeMode.GrowAndShrink,
                FlowDirection = FlowDirection.LeftToRight,
                WrapContents = false,
                Margin = Padding.Empty,
                Tag = FlowedRowTag.Footer
            };
            openLogsBtn.Margin = new Padding(0, 0, 8, 0);
            inspectXmlBtn.Margin = Padding.Empty;
            debugActions.Controls.Add(openLogsBtn);
            debugActions.Controls.Add(inspectXmlBtn);
            card.Controls.Add(debugActions);

            LayoutFlowedRows(card, DevelopmentRowsTop);
            int laidOutAt = card.ClientSize.Width;
            card.SizeChanged += (sender, args) =>
            {
                // Height is this method's own output, so only a width change means
                // the text has to be measured again.
                if (card.ClientSize.Width == laidOutAt) return;
                laidOutAt = card.ClientSize.Width;
                LayoutFlowedRows(card, DevelopmentRowsTop);
            };

            return card;
        }

        /// <summary>What part a control plays when a settings card is flowed.</summary>
        private enum FlowedRowTag
        {
            Toggle,
            Caption,
            Footer
        }

        /// <summary>One switch and the sentence explaining it, laid out by <see cref="LayoutFlowedRows"/>.</summary>
        private void AddFlowedToggle(
            CardPanel card, string text, string caption, bool initial, Action<bool> apply,
            Action afterChange = null)
        {
            var toggle = new CheckBox
            {
                Text = text,
                AutoSize = true,
                Font = DialogTheme.BodyFont,
                ForeColor = DialogTheme.Text,
                Cursor = Cursors.Hand,
                Checked = initial,
                Tag = FlowedRowTag.Toggle
            };
            toggle.CheckedChanged += (sender, args) =>
            {
                apply(toggle.Checked);
                afterChange?.Invoke();
            };
            card.Controls.Add(toggle);

            var hint = DialogTheme.CreateCaption(caption, Point.Empty);
            hint.Tag = FlowedRowTag.Caption;
            card.Controls.Add(hint);
        }

        private void NotifyContextMenuRestartRequired()
        {
            MessageBox.Show(
                this,
                "Close all Excel instances and restart Excel for this change to take effect.",
                "Talliark",
                MessageBoxButtons.OK,
                MessageBoxIcon.Information);
        }

        /// <summary>
        /// Stack the card's rows top to bottom at the width it currently has, then
        /// size the card to whatever that came to.
        /// </summary>
        private void LayoutFlowedRows(CardPanel card, int rowsTop)
        {
            int available = card.ClientSize.Width - (CardPadding * 2);
            if (available <= 0) return;

            int y = rowsTop;
            Control footer = null;

            foreach (Control control in card.Controls)
            {
                if (!(control.Tag is FlowedRowTag tag)) continue;
                if (tag == FlowedRowTag.Footer) { footer = control; continue; }

                bool isCaption = tag == FlowedRowTag.Caption;
                int indent = isCaption ? CaptionIndent : 0;
                DialogTheme.WrapAt(control, available - indent);
                control.Location = new Point(CardPadding + indent, y);
                y = control.Bottom + (isCaption ? RowGap : CaptionGap);
            }

            if (footer != null)
            {
                footer.Location = new Point(CardPadding, y + 6);
                y = footer.Bottom;
            }

            card.Height = y + CardPadding;
        }

        /// <summary>Version actions plus the scrollable release history.</summary>
        private CardPanel BuildUpdatesCard(
            Point location, Size size, out ReleaseNotesControl history)
        {
            var card = new CardPanel
            {
                Location = location,
                Size = size,
                Anchor = AnchorStyles.Top | AnchorStyles.Left
            };

            card.Controls.Add(DialogTheme.CreateSectionTitle(
                "Updates", new Point(CardPadding, 14)));

            var checkBtn = new Button
            {
                Text = "Check for Updates",
                Size = new Size(150, 30),
                Location = new Point(size.Width - CardPadding - 150, 12),
                Anchor = AnchorStyles.Top | AnchorStyles.Right
            };
            DialogTheme.StylePrimaryButton(checkBtn);
            checkBtn.Click += (s, e) => UpdateDialog.ShowSingle(owner: this);
            card.Controls.Add(checkBtn);

            card.Controls.Add(DialogTheme.CreateSeparator(
                new Point(CardPadding, 54), size.Width - (CardPadding * 2)));

            card.Controls.Add(DialogTheme.CreateSubHeading(
                "Release history", new Point(CardPadding + 2, 68)));

            history = new ReleaseNotesControl
            {
                BorderStyle = BorderStyle.None,
                Location = new Point(CardPadding, UpdateHistoryTop),
                MinimumSize = new Size(0, MinimumUpdateHistoryHeight),
                Size = new Size(
                    size.Width - (CardPadding * 2),
                    size.Height - UpdateHistoryTop - CardPadding),
                Anchor = AnchorStyles.Top | AnchorStyles.Bottom
                       | AnchorStyles.Left | AnchorStyles.Right
            };
            card.Controls.Add(history);

            return card;
        }

        /// <summary>Action bar pinned to the bottom of the dialog.</summary>
        private Panel BuildFooter(out Button closeBtn)
        {
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

            closeBtn = new Button
            {
                Text = "Close",
                DialogResult = DialogResult.Cancel,
                Size = new Size(96, 30),
                Anchor = AnchorStyles.Top | AnchorStyles.Right
            };
            DialogTheme.StyleSecondaryButton(closeBtn);
            footer.Controls.Add(closeBtn);

            return footer;
        }

        private void OpenLogFolder(object sender, EventArgs e)
        {
            try
            {
                Directory.CreateDirectory(TalliarkLog.DirectoryPath);
                Process.Start("explorer.exe", $"\"{TalliarkLog.DirectoryPath}\"");
            }
            catch (Exception ex)
            {
                TalliarkLog.Trace($"Could not open log folder: {ex}");
                MessageBox.Show(
                    this,
                    "The Talliark log folder could not be opened.",
                    "Talliark",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error);
            }
        }

        private void OpenStoredXml(object sender, EventArgs e)
        {
            try
            {
                var workbook = Globals.ThisAddIn?.Application?.ActiveWorkbook;
                if (workbook == null)
                {
                    MessageBox.Show(
                        this,
                        "Open a workbook before inspecting stored XML.",
                        "Talliark",
                        MessageBoxButtons.OK,
                        MessageBoxIcon.Information);
                    return;
                }

                IList<StoredXmlDebugEntry> entries =
                    new StoredXmlDebugService().Capture(workbook);
                using (var dialog = new StoredXmlDebugDialog(workbook.Name, entries))
                    dialog.ShowDialog(this);
            }
            catch (Exception ex)
            {
                TalliarkLog.Trace($"Could not inspect stored XML: {ex}");
                MessageBox.Show(
                    this,
                    "The stored Talliark XML could not be read from this workbook.",
                    "Talliark",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error);
            }
        }

        protected override async void OnShown(EventArgs e)
        {
            base.OnShown(e);
            if (_updateHistoryRequested) return;

            _updateHistoryRequested = true;

            UpdateCheckResult result;
            try
            {
                result = await UpdateCheckService.CheckAsync().ConfigureAwait(true);
            }
            catch
            {
                result = null;
            }

            if (IsDisposed || Disposing) return;

            if (result == null)
            {
                _updateHistory.ShowStatusMessage("Update history could not be loaded.");
                return;
            }

            _updateHistory.SetReleases(result.ReleaseNotes);
        }
    }
}
