using System.Windows.Forms;
using Talliark.Addin.Modules;
using Excel = Microsoft.Office.Interop.Excel;

namespace Talliark.Addin.Modules.WebView
{
    /// <summary>Hosts the document-viewer web UI inside a task pane WebView2 control.</summary>
    public sealed class TaskPaneHost : UserControl, IDocumentViewerHost
    {
        private readonly DocumentViewerController _controller;

        public TaskPaneHost(Excel.Workbook workbook)
        {
            Dock = DockStyle.Fill;
            _controller = new DocumentViewerController(this, "task pane", workbook);
            Controls.Add(_controller.Surface);
            _controller.Start();
        }

        public void SendClearRectangleHighlight() => _controller.SendClearRectangleHighlight();

        public void SendLinkSelectionChanged(System.Collections.Generic.IList<LinkSelectionEntry> entries) =>
            _controller.SendLinkSelectionChanged(entries);

        public void SendSearchQuery(string query) => _controller.SendSearchQuery(query);

        public void SendLinkRectanglesRemoved(System.Collections.Generic.IList<string> ids) =>
            _controller.SendLinkRectanglesRemoved(ids);

        public void SendCharBboxesVisible(bool visible) =>
            _controller.SendCharBboxesVisible(visible);
        public void SendTableDetectionEnabled(bool enabled) =>
            _controller.SendTableDetectionEnabled(enabled);

        public void SendValuesVisible(bool visible) =>
            _controller.SendValuesVisible(visible);

        public void SendReferencesVisible(bool visible) =>
            _controller.SendReferencesVisible(visible);

        public void SendStructureVisible(bool visible) =>
            _controller.SendStructureVisible(visible);

        public void SendValueNoiseVisible(bool visible) =>
            _controller.SendValueNoiseVisible(visible);

        public void NotifyViewerShown() => _controller.NotifyViewerShown();

        public void SendNavigateToRectangle(string id, string pdfId, int page) =>
            _controller.SendNavigateToRectangle(id, pdfId, page);

        public void RefreshDataIfReady() => _controller.RefreshDataIfReady();

        public void InvalidateData() => _controller.InvalidateData();

        public void SendPdfUpdated(string pdfId) => _controller.SendPdfUpdated(pdfId);

        public void SendPdfAdded(string pdfId) => _controller.SendPdfAdded(pdfId);

        public void SendPdfNameUpdated(string id, string name) => _controller.SendPdfNameUpdated(id, name);

        public void SendPdfRemoved(string id) => _controller.SendPdfRemoved(id);

        public void SendShowPdf(string pdfId) => _controller.SendShowPdf(pdfId);

        public void SendFoldersToWebView() => _controller.SendFoldersToWebView();

        protected override void Dispose(bool disposing)
        {
            TalliarkLog.Trace($"ENTER disposing={disposing}");
            if (disposing)
                _controller.Dispose();
            base.Dispose(disposing);
            TalliarkLog.Trace("EXIT");
        }
    }
}
