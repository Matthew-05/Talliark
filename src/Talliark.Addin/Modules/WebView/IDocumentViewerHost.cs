namespace Talliark.Addin.Modules.WebView
{
    /// <summary>Common surface for document-viewer hosts (task pane or pop-out window).</summary>
    internal interface IDocumentViewerHost
    {
        void RefreshDataIfReady();

        void InvalidateData();

        void NotifyViewerShown();

        void SendPdfUpdated(string pdfId);

        void SendPdfAdded(string pdfId);

        void SendPdfNameUpdated(string id, string name);

        void SendPdfRemoved(string id);

        void SendShowPdf(string pdfId);

        void SendFoldersToWebView();

        void SendNavigateToRectangle(string id, string pdfId, int page);

        void SendClearRectangleHighlight();

        void SendLinkSelectionChanged(System.Collections.Generic.IList<LinkSelectionEntry> entries);

        void SendSearchQuery(string query);

        void SendLinkRectanglesRemoved(System.Collections.Generic.IList<string> ids);

        void SendCharBboxesVisible(bool visible);
        void SendTableDetectionEnabled(bool enabled);
    }
}
