using System;
using System.Drawing;
using System.Windows.Forms;
using Microsoft.Web.WebView2.WinForms;

namespace Talliark.Addin.Modules.WebView
{
    /// <summary>
    /// The themed panel a WebView2 host shows while its page loads, and the message it shows
    /// instead if the page never arrives.
    /// </summary>
    /// <remarks>
    /// A WebView2 paints nothing until its first frame, so a host that adds the control and
    /// shows its window straight away puts a blank white rectangle on screen for as long as
    /// the environment takes to start and the bundle takes to parse. That is not a rounding
    /// error: the linker's own trace puts init to linker-app-ready at 2.06–2.31s across
    /// sessions, all of it blank. Covering the gap with the add-in's background colour costs
    /// nothing and is what makes the window read as loading rather than broken.
    ///
    /// Every WebView2 surface should own one. The host decides when to <see cref="Reveal"/>,
    /// and the right moment is the web app's own ready message rather than navigation
    /// completing — the page still has to build its first screen after that.
    /// </remarks>
    internal sealed class WebViewStartupSurface : IDisposable
    {
        /// <summary>
        /// Page background of the web apps. Kept in step with the shared web base.css: a
        /// mismatch here is visible as a flash at the moment the real page appears.
        /// </summary>
        internal static readonly Color Background = Color.FromArgb(244, 244, 249);

        private static readonly Color PlaceholderText = Color.FromArgb(92, 92, 112);

        private readonly Label _placeholder = new Label();
        private readonly Control _owner;
        private readonly WebView2 _webView;
        private bool _disposed;

        /// <param name="owner">
        /// The container holding the WebView2 — a Form or the panel inside one. Its
        /// BackColor is taken over, so pass the control the web page fills, not a parent
        /// that has native chrome of its own.
        /// </param>
        /// <param name="webView">The WebView2 this surface covers until its page is ready.</param>
        /// <param name="loadingText">What to say while waiting.</param>
        internal WebViewStartupSurface(
            Control owner,
            WebView2 webView,
            string loadingText = "Talliark Initializing...")
        {
            _owner = owner ?? throw new ArgumentNullException(nameof(owner));
            _webView = webView ?? throw new ArgumentNullException(nameof(webView));

            // Without this the control flashes its own white default in the moment between
            // getting a handle and painting the page, which defeats the placeholder.
            _webView.DefaultBackgroundColor = Background;
            _owner.BackColor = Background;

            _placeholder.Dock = DockStyle.Fill;
            _placeholder.BackColor = Background;
            _placeholder.ForeColor = PlaceholderText;
            _placeholder.Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point);
            _placeholder.Text = loadingText;
            _placeholder.TextAlign = ContentAlignment.MiddleCenter;

            _owner.Controls.Add(_placeholder);
            _placeholder.BringToFront();
        }

        /// <summary>
        /// Swaps the placeholder for the live page. Safe to call more than once, and safe
        /// from a background thread — the web app's ready message can arrive on either.
        /// </summary>
        internal void Reveal()
        {
            if (_disposed) return;

            if (_owner.InvokeRequired)
            {
                _owner.BeginInvoke(new Action(Reveal));
                return;
            }

            _placeholder.Visible = false;
            _webView.BringToFront();
        }

        /// <summary>
        /// Leaves the placeholder up carrying a failure message. Preferred over a message box:
        /// these hosts are created invisibly and warmed, and a modal raised by a window the
        /// user cannot see has no context to explain it.
        /// </summary>
        internal void ShowFailure(string message)
        {
            if (_disposed) return;

            if (_owner.InvokeRequired)
            {
                _owner.BeginInvoke(new Action(() => ShowFailure(message)));
                return;
            }

            _placeholder.Text = $"Talliark failed to load.\n\n{message}";
            _placeholder.Visible = true;
            _placeholder.BringToFront();
        }

        public void Dispose()
        {
            if (_disposed) return;
            _disposed = true;

            try
            {
                _placeholder.Dispose();
            }
            catch (Exception ex)
            {
                TalliarkLog.Trace($"startup surface dispose failed: {ex.Message}");
            }
        }
    }
}
