using System;
using Talliark.Addin.Properties;

namespace Talliark.Addin.Modules.Infrastructure
{
    /// <summary>Always-available user feature gates. Reconcile does not consult these.</summary>
    internal static class ExperimentalSettings
    {
        internal static event EventHandler<bool> TableDetectionChanged;
        internal static bool TableDetection
        {
            get
            {
                try { return Settings.Default.ExperimentalTableDetection; }
                catch (Exception ex) { TalliarkLog.Trace("Could not read ExperimentalTableDetection: " + ex.Message); return false; }
            }
            set
            {
                if (TableDetection == value) return;
                try { Settings.Default.ExperimentalTableDetection = value; Settings.Default.Save(); }
                catch (Exception ex) { TalliarkLog.Trace("Could not persist ExperimentalTableDetection: " + ex.Message); }
                TableDetectionChanged?.Invoke(null, value);
            }
        }

        /// <summary>
        /// Whether every Talliark WebView shows the browser's own right-click menu.
        /// Off by default: the viewer and file manager draw their own context menus and
        /// the native menu would appear over them. On, it is the only route to the
        /// built-in copy, save and inspect items. Applied when a surface initializes, so
        /// a change requires restarting Excel.
        /// </summary>
        internal static bool BrowserContextMenu
        {
            get
            {
                try { return Settings.Default.ShowBrowserContextMenu; }
                catch (Exception ex) { TalliarkLog.Trace("Could not read ShowBrowserContextMenu: " + ex.Message); return false; }
            }
            set
            {
                if (BrowserContextMenu == value) return;
                try { Settings.Default.ShowBrowserContextMenu = value; Settings.Default.Save(); }
                catch (Exception ex) { TalliarkLog.Trace("Could not persist ShowBrowserContextMenu: " + ex.Message); }
            }
        }
    }
}
