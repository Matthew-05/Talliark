using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;
using Talliark.Addin.Modules.CustomXml;
using Talliark.Addin.Modules.CustomXml.Models;
using Talliark.Addin.Modules.Services;
using Talliark.Addin.Modules.Services.Conversion;
using Talliark.Addin.Modules.UI;
using Excel = Microsoft.Office.Interop.Excel;

namespace Talliark.Addin.Modules.WebView
{
    /// <summary>Hosts the workbook-scoped Reconcile web app in a non-modal window.</summary>
    public sealed class ReconcileHost : Form
    {
        private readonly Excel.Workbook _workbook;
        private readonly WebView2 _webView = new WebView2();
        private readonly ReconcileResultService _results = new ReconcileResultService();
        private readonly ReconcileWorkspaceService _workspaceService = new ReconcileWorkspaceService();
        private readonly OcrService _ocrService;
        private WebViewStartupSurface _startup;
        private string _scanningPdfId;
        private bool _webViewReady;
        private bool _disposed;
        private Task _initTask;

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
            _initTask = InitAsync();
        }

        private async Task InitAsync()
        {
            try
            {                CoreWebView2Environment environment = await WebViewEagerLoader.GetEnvironmentAsync();
                if (_disposed) return;

                await _webView.EnsureCoreWebView2Async(environment);
                if (_disposed) return;

                WebViewContextMenu.Apply(_webView);

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
                    case "import-reconcile-document":
                        _ = ImportDocumentAsync(ReconcileMessageParser.ParseRole(raw));
                        break;
                    case "copy-reconcile-document":
                        CopyDocument(ReconcileMessageParser.ParseRole(raw), ReconcileMessageParser.ParseSourcePdfId(raw));
                        break;
                    case "complete-reconcile-setup":
                        _ = CompleteSetupAsync(ReconcileMessageParser.ParseProjectName(raw));
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
                ReconcileWorkspace workspace = store.LoadReconcileWorkspace();
                Post(ReconcileMessageSerializer.BuildDataLoaded(
                    _results.LoadDocuments(_workbook), content.Pdfs, _scanningPdfId,
                    workspace.ProjectName));
            }
            catch (Exception ex)
            {
                TalliarkLog.Trace("Reconcile data load failed: " + ex.Message);
            }
        }

        private bool ConfirmReplace(string role)
        {
            ReconcileWorkspace workspace = new TalliarkCustomXmlPartStore(_workbook).LoadReconcileWorkspace();
            bool existing = role == ReconcileRoles.Primary
                ? workspace.Primary != null
                : workspace.Comparisons?.Any(c => c.Role == role) == true;
            if (!existing) return true;
            string label = role == ReconcileRoles.Primary ? "current" : "prior";
            string scope = role == ReconcileRoles.Primary
                ? "The existing snapshot and its stored result will be replaced."
                : "The existing snapshot and its stored result will be replaced.";
            return MessageBox.Show(this,
                $"Replace the {label} financial statement?\n\n{scope}",
                "Replace financial statement", MessageBoxButtons.OKCancel, MessageBoxIcon.Warning,
                MessageBoxDefaultButton.Button2) == DialogResult.OK;
        }

        private void CopyDocument(string role, string sourcePdfId)
        {
            if (_ocrService.IsRunning || string.IsNullOrWhiteSpace(sourcePdfId)) return;
            if (!WorkbookProtectionGuard.TryRequireWritable(_workbook, this) || !ConfirmReplace(role)) return;
            try { _workspaceService.CopyFromImported(_workbook, role, sourcePdfId); SendData(); }
            catch (Exception ex) { MessageBox.Show(this, ex.Message, "Copy to Reconcile", MessageBoxButtons.OK, MessageBoxIcon.Error); }
        }

        private async Task ImportDocumentAsync(string role)
        {
            if (_ocrService.IsRunning) return;
            if (!WorkbookProtectionGuard.TryRequireWritable(_workbook, this) || !ConfirmReplace(role)) return;
            string label = role == ReconcileRoles.Primary ? "current" : "prior";
            using (var picker = new OpenFileDialog { Title = $"Select {label} financial statement", Multiselect = false, Filter = "Documents|*.pdf;*.doc;*.docx;*.xls;*.xlsx;*.ppt;*.pptx;*.png;*.jpg;*.jpeg;*.tif;*.tiff;*.eml|All files|*.*" })
            {
                if (picker.ShowDialog(this) != DialogResult.OK) return;
                await ImportDocumentPathAsync(role, picker.FileName);
            }
        }

        private async Task ImportDocumentPathAsync(string role, string path)
        {
            try
            {
                var plan = ImportPreparationService.Plan(this, new[] { new ImportCandidate { Path = path, Name = Path.GetFileName(path) } });
                if (plan.Cancelled) return;
                using (var progress = ThreadedProgressController.Show("Preparing Reconcile statement..."))
                using (PreparedImport prepared = await ImportPreparationService.PrepareAsync(plan, progress))
                {
                    if (!prepared.HasWork) { if (prepared.Errors.Count > 0) MessageBox.Show(this, string.Join("\n", prepared.Errors)); return; }
                    string name;
                    string base64;
                    if (prepared.PathRequests.Count > 0)
                    {
                        PdfPathImportRequest request = prepared.PathRequests[0];
                        name = string.IsNullOrWhiteSpace(request.DisplayName) ? Path.GetFileName(request.Path) : request.DisplayName;
                        base64 = Convert.ToBase64String(File.ReadAllBytes(request.Path));
                    }
                    else
                    {
                        PdfBase64ImportRequest request = prepared.Base64Requests[0];
                        name = request.Name;
                        base64 = request.Base64;
                    }
                    _workspaceService.ReplaceDocument(_workbook, role, name, base64);
                }
                SendData();
            }
            catch (Exception ex)
            {
                TalliarkLog.Trace("Reconcile import failed: " + ex);
                MessageBox.Show(this, ex.Message, "Import to Reconcile", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }

        private async Task CompleteSetupAsync(string projectName)
        {
            if (_ocrService.IsRunning) return;
            if (!WorkbookProtectionGuard.TryRequireWritable(_workbook, this)) return;
            try
            {
                _workspaceService.CompleteSetup(_workbook, projectName);
                ReconcileDocument primary = new TalliarkCustomXmlPartStore(_workbook)
                    .LoadReconcileWorkspace().Primary;
                SendData();
                if (primary != null) await RunScanAsync(primary.Id);
            }
            catch (Exception ex)
            {
                TalliarkLog.Trace("Reconcile setup failed: " + ex);
                MessageBox.Show(this, ex.Message, "Set up Reconcile", MessageBoxButtons.OK, MessageBoxIcon.Error);
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

                    // Deferred while init is in flight: disposing a WebView2 mid-initialization
                    // is a native fail-fast in the runtime, and Excel blames the add-in for it.
                    WebViewDisposal.DisposeWhenInitialized(_webView, _initTask);
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
