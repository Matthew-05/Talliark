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
    }
}
