using System;
using System.IO;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace Talliark.Addin.Modules.Services.Conversion
{
    /// <summary>
    /// Renders HTML to PDF using an offscreen WebView2.
    ///
    /// This is the only HTML rendering engine Talliark ships, and it is already a
    /// hard dependency of the task pane and viewer, so HTML-shaped sources (.html,
    /// .eml and every spreadsheet normalised by the Python worker, .msg exported by
    /// Outlook) all funnel through here rather than adding a second renderer.
    ///
    /// One instance spans an import batch, like <see cref="PythonConversionConverter"/>.
    /// The browser is created on first use and reused for every file after that,
    /// because standing one up is not cheap: creating the environment and awaiting
    /// EnsureCoreWebView2Async costs roughly 400ms, which used to be paid per file
    /// and came to about half the wall time of a mixed batch once spreadsheets
    /// started coming through here too. Rendering itself is tens of milliseconds.
    ///
    /// The WebView2 control must be created and driven on the UI thread, so this
    /// must be constructed, used and disposed there — <see cref="DocumentConversionService"/>
    /// is awaited on the UI thread for exactly this reason.
    /// </summary>
    internal sealed class HtmlToPdfConverter : IDisposable
    {
        private static readonly TimeSpan NavigationTimeout = TimeSpan.FromSeconds(45);

        /// <summary>Offscreen parent; WebView2 will not initialise without a real handle.</summary>
        private Form _host;

        private WebView2 _webView;
        private bool _disposed;

        /// <summary>
        /// Loads <paramref name="htmlPath"/> in the offscreen browser and prints it
        /// to <paramref name="outputPdfPath"/>. Must be awaited on the UI thread.
        /// </summary>
        public async Task ConvertFileAsync(string htmlPath, string outputPdfPath)
        {
            if (_disposed)
                throw new ObjectDisposedException(nameof(HtmlToPdfConverter));
            if (string.IsNullOrWhiteSpace(htmlPath))
                throw new ArgumentException("An HTML source path is required.", nameof(htmlPath));
            if (!File.Exists(htmlPath))
                throw new FileNotFoundException("HTML source not found.", htmlPath);

            string navigateUri = new Uri(htmlPath).AbsoluteUri;

            try
            {
                await EnsureBrowserAsync();
                await RenderAsync(navigateUri, outputPdfPath);
            }
            catch
            {
                // A browser that failed mid-render may be sitting on a half-loaded
                // document or a dead host process. Tear it down so the next file in
                // the batch starts from a known state rather than inheriting the
                // fault — the same reasoning as PythonConversionConverter's dead
                // session check.
                TearDownBrowser();
                throw;
            }
        }

        private async Task EnsureBrowserAsync()
        {
            if (_webView?.CoreWebView2 != null)
                return;

            TearDownBrowser();

            var host = new Form
            {
                FormBorderStyle = FormBorderStyle.None,
                ShowInTaskbar = false,
                StartPosition = FormStartPosition.Manual,
                Location = new System.Drawing.Point(-10000, -10000),
                Size = new System.Drawing.Size(1024, 1320),
                Opacity = 0,
            };

            var webView = new WebView2 { Dock = DockStyle.Fill };

            try
            {
                host.Controls.Add(webView);
                host.Show();          // Realises the handle; the form stays off-screen.

                // The process-wide environment, same as every other host uses.
                CoreWebView2Environment environment = await WebViewEagerLoader.GetEnvironmentAsync();

                await webView.EnsureCoreWebView2Async(environment);

                webView.CoreWebView2.Settings.AreDefaultContextMenusEnabled = false;
                webView.CoreWebView2.Settings.IsStatusBarEnabled = false;

                _host = host;
                _webView = webView;
            }
            catch
            {
                // Ownership never transferred to the fields, so clean up here.
                try { webView.Dispose(); } catch { }
                try { host.Dispose(); } catch { }
                throw;
            }
        }

        private async Task RenderAsync(string navigateUri, string outputPdfPath)
        {
            await NavigateAsync(_webView, navigateUri);

            // Give late-arriving layout (web fonts, inline images) a moment to
            // settle before capturing; NavigationCompleted fires before subresources
            // have necessarily been laid out.
            await Task.Delay(250);

            bool printed = await _webView.CoreWebView2.PrintToPdfAsync(outputPdfPath);
            if (!printed)
                throw new IOException("WebView2 could not print the document to PDF.");
        }

        private static Task NavigateAsync(WebView2 webView, string uri)
        {
            var completion = new TaskCompletionSource<bool>();
            var timeout = new Timer { Interval = (int)NavigationTimeout.TotalMilliseconds };

            void OnNavigationCompleted(object sender, CoreWebView2NavigationCompletedEventArgs args)
            {
                Cleanup();

                if (args.IsSuccess)
                    completion.TrySetResult(true);
                else
                    completion.TrySetException(new IOException(
                        $"Could not load the document for rendering ({args.WebErrorStatus})."));
            }

            void OnTimeout(object sender, EventArgs args)
            {
                Cleanup();
                completion.TrySetException(new TimeoutException(
                    "Timed out loading the document for rendering."));
            }

            void Cleanup()
            {
                timeout.Stop();
                timeout.Tick -= OnTimeout;
                timeout.Dispose();

                // Unsubscribing matters now the browser outlives one render: a
                // leaked handler would resolve a later document's navigation.
                webView.CoreWebView2.NavigationCompleted -= OnNavigationCompleted;
            }

            webView.CoreWebView2.NavigationCompleted += OnNavigationCompleted;
            timeout.Tick += OnTimeout;
            timeout.Start();

            try
            {
                webView.CoreWebView2.Navigate(uri);
            }
            catch (Exception ex)
            {
                Cleanup();
                completion.TrySetException(ex);
            }

            return completion.Task;
        }

        private void TearDownBrowser()
        {
            if (_webView != null)
            {
                try { _webView.Dispose(); }
                catch (Exception ex) { TalliarkLog.Trace($"Could not dispose the render WebView2: {ex.Message}"); }
                _webView = null;
            }

            if (_host != null)
            {
                try
                {
                    _host.Hide();
                    _host.Dispose();
                }
                catch (Exception ex) { TalliarkLog.Trace($"Could not dispose the render host: {ex.Message}"); }
                _host = null;
            }
        }

        public void Dispose()
        {
            if (_disposed) return;
            _disposed = true;

            TearDownBrowser();
        }
    }
}
