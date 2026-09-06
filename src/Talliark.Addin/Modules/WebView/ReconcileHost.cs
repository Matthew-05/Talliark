using System;
using System.IO;
using System.Reflection;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;
using Talliark.Addin.Modules.CustomXml;
using Talliark.Addin.Modules.Services;
using Excel = Microsoft.Office.Interop.Excel;

namespace Talliark.Addin.Modules.WebView
{
    /// <summary>Hosts the workbook-scoped Reconcile web app in a non-modal window.</summary>
    public sealed class ReconcileHost : Form
    {
        private readonly Excel.Workbook _workbook;
        private readonly WebView2 _webView = new WebView2();
        private readonly ReconcileResultService _results = new ReconcileResultService();
        private readonly OcrService _ocrService;
        private WebViewStartupSurface _startup;
        private string _scanningPdfId;
        private bool _webViewReady;
        private bool _disposed;

        public ReconcileHost(Excel.Workbook workbook)
        {
            _workbook = workbook ?? throw new ArgumentNullException(nameof(workbook));
            string workbookName;
            try { workbookName = workbook.Name; } catch { workbookName = null; }
            Text = string.IsNullOrWhiteSpace(workbookName)
                ? "Talliark – Reconcile" : $"Talliark – Reconcile – {workbookName}";
            Width = 1040;
            Height = 680;
            MinimumSize = new System.Drawing.Size(700, 480);
            StartPosition = FormStartPosition.CenterScreen;
            _webView.Dock = DockStyle.Fill;
            Controls.Add(_webView);
            _startup = new WebViewStartupSurface(this, _webView, "Opening Reconcile...");
            _ocrService = new OcrService(this);
            _ = InitAsync();
        }

        private async Task InitAsync()
        {
            try
            {
                CoreWebView2Environment environment = await WebViewEagerLoader.GetEnvironmentAsync();
                await _webView.EnsureCoreWebView2Async(environment);
                if (_disposed) return;

                string uiPath = GetWebUiPath();
                if (!Directory.Exists(uiPath))
                    throw new DirectoryNotFoundException("Web UI folder not found: " + uiPath);
                _webView.CoreWebView2.SetVirtualHostNameToFolderMapping(
                    "talliark.local", uiPath, CoreWebView2HostResourceAccessKind.Allow);
                _webView.CoreWebView2.WebMessageReceived += OnWebMessageReceived;
                _webView.CoreWebView2.Navigate("https://talliark.local/reconcile/index.html");
            }
            catch (Exception ex)
            {
                if (!_disposed) _startup?.ShowFailure(ex.Message);
            }
        }

        private void OnWebMessageReceived(object sender, CoreWebView2WebMessageReceivedEventArgs e)
        {
            if (_disposed) return;
            try
            {
                string raw = e.TryGetWebMessageAsString();
                switch (ReconcileMessageParser.GetMessageType(raw))
                {
                    case "reconcile-ready":
                        _webViewReady = true;
                        _startup?.Reveal();
                        SendData();
                        break;
                    case "run-reconcile-scan":
                        _ = RunScanAsync(ReconcileMessageParser.ParsePdfId(raw));
                        break;
                    case "cancel-reconcile-scan":
                        _ocrService.Cancel();
                        break;
                    case "request-reconcile-result":
                        SendResult(ReconcileMessageParser.ParsePdfId(raw));
                        break;
                }
            }
            catch (Exception ex)
            {
                TalliarkLog.Trace("Reconcile message failed: " + ex.Message);
            }
        }

        private async Task RunScanAsync(string pdfId)
        {
            if (_ocrService.IsRunning || string.IsNullOrEmpty(pdfId)) return;
            if (!WorkbookProtectionGuard.TryRequireWritable(_workbook, this)) return;

            _scanningPdfId = pdfId;
            SendData();
            try
            {
                await _ocrService.RunReconcileScanAsync(
                    pdfId, _workbook, (id, status, detail) =>
                    {
                        string webStatus = status == "processing" ? "scanning" : status;
                        if (webStatus != "queued" && webStatus != "scanning"
                            && webStatus != "complete" && webStatus != "cancelled"
                            && webStatus != "error")
                            return;
                        Post(ReconcileMessageSerializer.BuildScanStatus(id, webStatus, detail));
                        if (webStatus == "complete")
                            Globals.ThisAddIn.RefreshTaskPanePdf(_workbook, id);
                    });
            }
            catch (Exception ex)
            {
                TalliarkLog.Trace("Reconcile scan failed: " + ex);
                Post(ReconcileMessageSerializer.BuildScanStatus(pdfId, "error", ex.Message));
            }
            finally
            {
                _scanningPdfId = null;
                SendData();
            }
        }

        public void RefreshDataIfReady()
        {
            if (_webViewReady) SendData();
        }

        private void SendData()
        {
            if (_disposed || !_webViewReady) return;
            try
            {
                var store = new TalliarkCustomXmlPartStore(_workbook);
                var content = store.LoadContent();
                Post(ReconcileMessageSerializer.BuildDataLoaded(
                    _results.LoadDocuments(_workbook), content.Folders, _scanningPdfId));
            }
            catch (Exception ex)
            {
                TalliarkLog.Trace("Reconcile data load failed: " + ex.Message);
            }
        }

        private void SendResult(string pdfId)
        {
            Post(ReconcileMessageSerializer.BuildResultLoaded(
                _results.LoadResult(_workbook, pdfId)));
        }

        private void Post(string json)
        {
            if (_disposed || string.IsNullOrEmpty(json)) return;
            if (InvokeRequired)
            {
                if (IsHandleCreated && !IsDisposed)
                    BeginInvoke(new Action(() => Post(json)));
                return;
            }
            try { _webView.CoreWebView2?.PostWebMessageAsString(json); }
            catch (Exception ex) { TalliarkLog.Trace("Reconcile post failed: " + ex.Message); }
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            if (e.CloseReason == CloseReason.UserClosing)
            {
                e.Cancel = true;
                Hide();
                return;
            }
            base.OnFormClosing(e);
        }

        protected override void Dispose(bool disposing)
        {
            if (!_disposed)
            {
                _disposed = true;
                try { _ocrService?.Cancel(); } catch { }
                try
                {
                    if (_webView.CoreWebView2 != null)
                        _webView.CoreWebView2.WebMessageReceived -= OnWebMessageReceived;
                }
                catch { }
                if (disposing)
                {
                    _startup?.Dispose();
                    _startup = null;
                }
            }
            base.Dispose(disposing);
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
