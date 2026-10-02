using System;
using Microsoft.Web.WebView2.WinForms;
using Talliark.Addin.Modules.Infrastructure;

namespace Talliark.Addin.Modules.WebView
{
    /// <summary>
    /// Applies the user's preference for the browser's own right-click menu to a host's
    /// WebView. Off by default: the viewer and file manager draw their own context menus
    /// and the native one would appear over them. On, it is the only route to the
    /// built-in copy, save and inspect items.
    /// </summary>
    internal static class WebViewContextMenu
    {
        internal static void Apply(WebView2 webView)
        {
            if (webView?.CoreWebView2 == null) return;
            try
            {
                webView.CoreWebView2.Settings.AreDefaultContextMenusEnabled =
                    ExperimentalSettings.BrowserContextMenu;
            }
            catch (Exception ex)
            {
                TalliarkLog.Trace($"Could not apply html context-menu setting: {ex.Message}");
            }
        }
    }
}
