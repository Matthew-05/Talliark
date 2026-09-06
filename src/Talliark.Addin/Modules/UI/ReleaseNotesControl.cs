using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Text;
using System.Threading.Tasks;
using System.Windows.Forms;
using Talliark.Addin.Modules.Services;
using Markdig;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace Talliark.Addin.Modules.UI
{
    /// <summary>
    /// Renders the GitHub release history as styled Markdown. Native HTML details
    /// elements provide accessible disclosure controls without a script dependency.
    /// </summary>
    internal sealed class ReleaseNotesControl : UserControl
    {
        private static readonly MarkdownPipeline MarkdownPipeline =
            new MarkdownPipelineBuilder()
                .UsePipeTables()
                .UseTaskLists()
                .UseAutoLinks()
                .UseEmphasisExtras()
                .DisableHtml()
                .Build();

        private readonly WebView2 _webView;
        private readonly Label _statusLabel;
        private string _pendingHtml;
        private string _htmlPath;
        private bool _webViewReady;
        private bool _initializationStarted;
        private bool _disposed;

        internal ReleaseNotesControl()
        {
            BorderStyle = BorderStyle.FixedSingle;

            _webView = new WebView2
            {
                Dock = DockStyle.Fill,
                DefaultBackgroundColor = System.Drawing.Color.White,
            };

            _statusLabel = new Label
            {
                Dock = DockStyle.Fill,
                TextAlign = System.Drawing.ContentAlignment.MiddleCenter,
                ForeColor = System.Drawing.SystemColors.GrayText,
                BackColor = System.Drawing.SystemColors.Window,
                Text = "Preparing release notes…",
            };

            Controls.Add(_webView);
            Controls.Add(_statusLabel);
            _statusLabel.BringToFront();
        }

        protected override void OnHandleCreated(EventArgs e)
        {
            base.OnHandleCreated(e);
            if (_initializationStarted) return;

            _initializationStarted = true;
            _ = InitializeWebViewAsync();
        }

        internal void SetReleases(IReadOnlyList<ReleaseNote> releases)
        {
            _pendingHtml = BuildPage(releases);
            if (_webViewReady)
                NavigateToPendingHtml();
        }

        /// <summary>
        /// Renders a single release's notes without the collapsible header, for
        /// callers that already name the release in their own chrome.
        /// </summary>
        internal void SetRelease(ReleaseNote release)
        {
            _pendingHtml = BuildSingleReleasePage(release);
            if (_webViewReady)
                NavigateToPendingHtml();
        }

        internal void ShowStatusMessage(string message)
        {
            _pendingHtml = null;
            _statusLabel.Text = message;
            _statusLabel.Visible = true;
            _statusLabel.BringToFront();
        }

        private async Task InitializeWebViewAsync()
        {
            try
            {
                // The process-wide environment, same as every other host uses.
                var environment = await WebViewEagerLoader.GetEnvironmentAsync();
                await _webView.EnsureCoreWebView2Async(environment);
                if (_disposed) return;

                _webView.CoreWebView2.Settings.AreDefaultContextMenusEnabled = false;
                _webView.CoreWebView2.Settings.AreDevToolsEnabled = false;
                _webView.CoreWebView2.Settings.IsStatusBarEnabled = false;
                _webView.CoreWebView2.Settings.IsZoomControlEnabled = false;
                _webView.CoreWebView2.NavigationStarting += OnNavigationStarting;
                _webView.CoreWebView2.NavigationCompleted += OnNavigationCompleted;
                _webViewReady = true;

                if (_pendingHtml != null)
                    NavigateToPendingHtml();
            }
            catch (Exception ex)
            {
                if (_disposed) return;
                TalliarkLog.Trace($"Release notes renderer failed to initialize: {ex.Message}");
                _statusLabel.Text = "Release notes could not be displayed.";
                _statusLabel.Visible = true;
                _statusLabel.BringToFront();
            }
        }

        private void NavigateToPendingHtml()
        {
            if (!_webViewReady || _pendingHtml == null || _disposed) return;

            try
            {
                DeleteHtmlFile();
                _htmlPath = Path.Combine(
                    Path.GetTempPath(),
                    "Talliark-ReleaseNotes-" + Guid.NewGuid().ToString("N") + ".html");
                File.WriteAllText(_htmlPath, _pendingHtml, new UTF8Encoding(false));

                _statusLabel.Text = "Preparing release notes…";
                _statusLabel.Visible = true;
                _statusLabel.BringToFront();
                _webView.CoreWebView2.Navigate(new Uri(_htmlPath).AbsoluteUri);
            }
            catch (Exception ex)
            {
                TalliarkLog.Trace($"Release notes navigation failed: {ex.Message}");
                _statusLabel.Text = "Release notes could not be displayed.";
                _statusLabel.Visible = true;
                _statusLabel.BringToFront();
            }
        }

        private void OnNavigationStarting(object sender, CoreWebView2NavigationStartingEventArgs e)
        {
            if (IsReleaseNotesDocument(e.Uri)) return;

            e.Cancel = true;
            OpenExternalLink(e.Uri);
        }

        private void OnNavigationCompleted(object sender, CoreWebView2NavigationCompletedEventArgs e)
        {
            if (_disposed) return;

            if (e.IsSuccess)
            {
                _statusLabel.Visible = false;
                return;
            }

            _statusLabel.Text = "Release notes could not be displayed.";
            _statusLabel.Visible = true;
            _statusLabel.BringToFront();
        }

        private bool IsReleaseNotesDocument(string url)
        {
            if (string.IsNullOrEmpty(_htmlPath) || !Uri.TryCreate(url, UriKind.Absolute, out var uri))
                return false;

            return uri.IsFile && string.Equals(
                Path.GetFullPath(uri.LocalPath),
                Path.GetFullPath(_htmlPath),
                StringComparison.OrdinalIgnoreCase);
        }

        private static void OpenExternalLink(string url)
        {
            if (!Uri.TryCreate(url, UriKind.Absolute, out var uri) ||
                (uri.Scheme != Uri.UriSchemeHttp && uri.Scheme != Uri.UriSchemeHttps))
                return;

            try
            {
                Process.Start(uri.AbsoluteUri);
            }
            catch
            {
                // A missing system browser should not make the update dialog fail.
            }
        }

        private static string BuildPage(IReadOnlyList<ReleaseNote> releases)
        {
            var html = new StringBuilder();
            html.Append(PageStart);

            if (releases == null || releases.Count == 0)
            {
                html.Append("<p class=\"empty\">No release notes were provided.</p>");
            }
            else
            {
                for (var index = 0; index < releases.Count; index++)
                    AppendRelease(html, releases[index]);
            }

            html.Append("</main></body></html>");
            return html.ToString();
        }

        private static string BuildSingleReleasePage(ReleaseNote release)
        {
            var html = new StringBuilder();
            html.Append(PageStart);

            if (release == null)
            {
                html.Append("<p class=\"empty\">No release notes were provided.</p>");
            }
            else
            {
                var markdown = string.IsNullOrWhiteSpace(release.Body)
                    ? "*No release notes were provided for this release.*"
                    : release.Body.Trim();

                html.Append("<article class=\"markdown-body single\">");
                html.Append(Markdown.ToHtml(markdown, MarkdownPipeline));
                html.Append("</article>");
            }

            html.Append("</main></body></html>");
            return html.ToString();
        }

        /// <summary>
        /// Renders one collapsed disclosure row: version, the release's own title,
        /// the publication date, and an "Installed" badge on the running version.
        /// </summary>
        private static void AppendRelease(StringBuilder html, ReleaseNote release)
        {
            var version = string.IsNullOrWhiteSpace(release.Version)
                ? "Untitled release"
                : "v" + release.Version;
            var title = DescribeTitle(release);
            var date = release.PublishedAt.HasValue
                ? release.PublishedAt.Value.ToLocalTime().ToString("MMMM d, yyyy")
                : string.Empty;
            var markdown = string.IsNullOrWhiteSpace(release.Body)
                ? "*No release notes were provided for this release.*"
                : release.Body.Trim();

            html.Append("<details class=\"release\">");
            html.Append("<summary><span class=\"version\">");
            html.Append(WebUtility.HtmlEncode(version));
            html.Append("</span>");
            if (IsInstalled(release))
                html.Append("<span class=\"installed\">Installed</span>");
            if (title.Length > 0)
            {
                html.Append("<span class=\"separator\">·</span><span class=\"title\">");
                html.Append(WebUtility.HtmlEncode(title));
                html.Append("</span>");
            }
            if (date.Length > 0)
            {
                html.Append("<span class=\"separator\">·</span><time>");
                html.Append(WebUtility.HtmlEncode(date));
                html.Append("</time>");
            }
            html.Append("</summary><article class=\"markdown-body\">");
            html.Append(Markdown.ToHtml(markdown, MarkdownPipeline));
            html.Append("</article></details>");
        }

        /// <summary>
        /// The release's headline, dropped when it only repeats the version so the
        /// row does not read "v1.2.0 · v1.2.0".
        /// </summary>
        private static string DescribeTitle(ReleaseNote release)
        {
            var title = (release.Title ?? string.Empty).Trim();
            if (title.Length == 0) return string.Empty;

            var normalizedTitle = title.TrimStart('v', 'V');
            var version = (release.Version ?? string.Empty).Trim();

            return string.Equals(normalizedTitle, version, StringComparison.OrdinalIgnoreCase)
                ? string.Empty
                : title;
        }

        /// <summary>Whether a release is the build currently running.</summary>
        private static bool IsInstalled(ReleaseNote release)
        {
            var version = (release.Version ?? string.Empty).Trim().TrimStart('v', 'V');
            if (version.Length == 0) return false;

            var current = (AppVersion.Current ?? string.Empty).Trim().TrimStart('v', 'V');
            return current.Length > 0
                && string.Equals(version, current, StringComparison.OrdinalIgnoreCase);
        }

        private void DeleteHtmlFile()
        {
            if (string.IsNullOrEmpty(_htmlPath)) return;

            try
            {
                if (File.Exists(_htmlPath)) File.Delete(_htmlPath);
            }
            catch
            {
                // The OS can reclaim this temporary file if WebView2 still has it open.
            }
            finally
            {
                _htmlPath = null;
            }
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing && !_disposed)
            {
                _disposed = true;
                if (_webView.CoreWebView2 != null)
                {
                    _webView.CoreWebView2.NavigationStarting -= OnNavigationStarting;
                    _webView.CoreWebView2.NavigationCompleted -= OnNavigationCompleted;
                }
                _webView.Dispose();
                DeleteHtmlFile();
            }

            base.Dispose(disposing);
        }

        private const string PageStart = @"<!doctype html>
<html lang=""en""><head><meta charset=""utf-8"">
<meta name=""viewport"" content=""width=device-width,initial-scale=1"">
<meta http-equiv=""Content-Security-Policy"" content=""default-src 'none'; img-src https: data:; style-src 'unsafe-inline'; font-src 'none'; base-uri 'none'; form-action 'none'"">
<style>
:root { color-scheme: light; font-family: 'Segoe UI', system-ui, sans-serif; color: #24292f; background: #fff; }
* { box-sizing: border-box; }
html, body { margin: 0; min-height: 100%; background: #fff; }
body { font-size: 14px; line-height: 1.55; }
.releases { width: 100%; }
.release { border-bottom: 1px solid #d8dee4; }
.release:last-child { border-bottom: 0; }
summary { display: flex; align-items: center; gap: 8px; min-height: 46px; padding: 0 18px; cursor: pointer; user-select: none; background: #f6f8fa; list-style: none; }
summary::-webkit-details-marker { display: none; }
summary::before { content: '›'; width: 14px; color: #57606a; font-size: 22px; line-height: 1; transform-origin: center; transition: transform 120ms ease; }
details[open] > summary::before { transform: rotate(90deg); }
summary:hover { background: #eef2f6; }
summary:focus-visible { outline: 2px solid #0969da; outline-offset: -2px; }
.version { flex: 0 0 auto; color: #1f2328; font-weight: 650; }
.title { flex: 0 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #1f2328; font-size: 13px; }
.separator, time { flex: 0 0 auto; color: #656d76; font-size: 13px; }
.installed { flex: 0 0 auto; margin-left: 2px; padding: 1px 8px; border: 1px solid rgba(31,136,61,.35); border-radius: 999px; background: #dafbe1; color: #1a7f37; font-size: 11px; font-weight: 650; white-space: nowrap; }
.markdown-body { padding: 18px 28px 24px 40px; overflow-wrap: anywhere; }
.markdown-body.single { padding: 4px 20px 18px; }
.markdown-body > :first-child { margin-top: 0 !important; }
.markdown-body > :last-child { margin-bottom: 0 !important; }
h1, h2, h3, h4, h5, h6 { margin: 22px 0 10px; color: #1f2328; line-height: 1.25; font-weight: 650; }
h1 { font-size: 22px; padding-bottom: 8px; border-bottom: 1px solid #d8dee4; }
h2 { font-size: 18px; padding-bottom: 7px; border-bottom: 1px solid #d8dee4; }
h3 { font-size: 16px; }
h4, h5, h6 { font-size: 14px; }
p { margin: 0 0 12px; }
ul, ol { margin: 0 0 14px; padding-left: 26px; }
li + li { margin-top: 4px; }
li > p { margin-bottom: 6px; }
a { color: #0969da; text-decoration: none; }
a:hover { text-decoration: underline; }
strong { font-weight: 650; }
blockquote { margin: 0 0 14px; padding: 8px 14px; color: #57606a; background: #f6f8fa; border-left: 4px solid #afb8c1; }
blockquote > :last-child { margin-bottom: 0; }
code { padding: 2px 5px; border-radius: 4px; background: #eff1f3; font: 12px Consolas, 'Courier New', monospace; }
pre { margin: 0 0 14px; padding: 14px 16px; overflow: auto; border: 1px solid #d8dee4; border-radius: 6px; background: #f6f8fa; }
pre code { padding: 0; background: transparent; font-size: 12px; }
table { display: block; max-width: 100%; margin: 0 0 14px; overflow: auto; border-spacing: 0; border-collapse: collapse; }
th, td { padding: 7px 12px; border: 1px solid #d0d7de; text-align: left; }
th { font-weight: 650; background: #f6f8fa; }
tr:nth-child(even) { background: #f8fafc; }
hr { height: 1px; margin: 20px 0; border: 0; background: #d8dee4; }
img { max-width: 100%; height: auto; border-radius: 4px; }
input[type='checkbox'] { margin: 0 6px 0 -20px; accent-color: #0969da; }
.empty { margin: 0; padding: 24px; color: #656d76; text-align: center; }
</style></head><body><main class=""releases"">";
    }
}
