using System;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Web.WebView2.WinForms;

namespace Talliark.Addin.Modules.WebView
{
    /// <summary>
    /// Disposes a WebView2 without racing its initialization.
    /// </summary>
    /// <remarks>
    /// Eager warm-up creates hosts that can be evicted or closed while
    /// <c>EnsureCoreWebView2Async</c> is still in flight. Disposing the control at that moment
    /// is a native fail-fast inside the WebView2 runtime — an assertion (0x80000003) that takes
    /// Excel down with it, and the crash Excel later blames on the add-in. Detaching the
    /// control from its parent first keeps the parent's disposal walk away from it, and the
    /// disposal is deferred until initialization has settled, when it is safe.
    /// </remarks>
    internal static class WebViewDisposal
    {
        /// <summary>
        /// Disposes <paramref name="webView"/> now if it is not initializing, or as soon as
        /// <paramref name="initTask"/> completes otherwise. Must be called on the UI thread.
        /// </summary>
        internal static void DisposeWhenInitialized(WebView2 webView, Task initTask)
        {
            if (webView == null) return;

            if (initTask == null || initTask.IsCompleted)
            {
                DisposeNow(webView);
                return;
            }

            // Detach before deferring: if the parent is disposed while init is still running,
            // its child walk would otherwise dispose the WebView2 mid-initialization.
            try
            {
                webView.Parent?.Controls.Remove(webView);
            }
            catch (Exception ex)
            {
                TalliarkLog.Trace($"webview detach before deferred dispose failed: {ex.Message}");
            }

            // OnCompleted captures the current synchronization context, which is the UI
            // thread here — the same thread the init continuation returns to.
            SynchronizationContext context = SynchronizationContext.Current;
            initTask.GetAwaiter().OnCompleted(() =>
            {
                if (context != null && context != SynchronizationContext.Current)
                    context.Post(_ => DisposeNow(webView), null);
                else
                    DisposeNow(webView);
            });
        }

        private static void DisposeNow(WebView2 webView)
        {
            try
            {
                webView.Dispose();
            }
            catch (Exception ex)
            {
                TalliarkLog.Trace($"webview dispose failed: {ex.Message}");
            }
        }
    }
}
