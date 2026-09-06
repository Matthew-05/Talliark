using System;
using System.IO;
using System.Threading.Tasks;
using Microsoft.Web.WebView2.Core;
using Excel = Microsoft.Office.Interop.Excel;

namespace Talliark.Addin
{
    /// <summary>
    /// Owns the one WebView2 cost worth paying up front: the shared
    /// <see cref="CoreWebView2Environment"/>, created once per Excel process and handed to
    /// every host that needs one.
    /// </summary>
    /// <remarks>
    /// Two things are warmed, at different scopes.
    ///
    /// The environment is per process. All hosts point at the same user data folder, so they
    /// already share one browser process, and creating the environment is what starts it.
    /// Doing that once, off the calling thread, means no host ever pays the browser cold
    /// start.
    ///
    /// The surfaces are per workbook, and warming them is only affordable because
    /// <see cref="ThisAddIn.EvictUnopenedSurfaces"/> takes them back. An earlier version
    /// warmed on every workbook event and kept everything, so a session that touched twenty
    /// workbooks held renderers for all twenty. Now the newest
    /// <see cref="WarmWorkbookLimit"/> workbooks stay warm and the rest give up whatever the
    /// user never opened — so the cost tracks how many workbooks are in play, not how many
    /// are open.
    /// </remarks>
    internal static class WebViewEagerLoader
    {
        private static readonly object _sync = new object();
        private static Task<CoreWebView2Environment> _environmentTask;

        /// <summary>
        /// How many recently-activated workbooks keep surfaces the user has not opened.
        /// </summary>
        /// <remarks>
        /// Two, not one: alternating between a pair of workbooks is ordinary, and a limit of
        /// one would tear down and rebuild every surface on each switch — the thrash would
        /// cost more than the memory it saved.
        /// </remarks>
        internal const int WarmWorkbookLimit = 2;

        /// <summary>
        /// Starts the shared environment in the background and warms whatever is already
        /// active. Returns immediately: the add-in must not block Excel's startup.
        /// </summary>
        internal static void Initialize(ThisAddIn addIn)
        {
            Modules.TalliarkLog.Trace("ENTER eager load (shared WebView2 environment)");

            // Faults are observed here rather than left on a fire-and-forget task. Nothing
            // needs to recover at startup — the next caller retries, and whichever host asks
            // first surfaces the failure in its own window — but an unobserved task exception
            // is a landmine that goes off later, on the finalizer thread, far from the cause.
            GetEnvironmentAsync().ContinueWith(
                task => Modules.TalliarkLog.Trace(
                    $"eager load failed: {task.Exception?.GetBaseException().Message}"),
                TaskContinuationOptions.OnlyOnFaulted);

            // Usually a no-op: Excel starts the add-in before it has a workbook, and the
            // lifecycle events warm each one as it arrives. It matters when the add-in is
            // enabled or reloaded into a session that already has workbooks open.
            WarmUp(addIn, addIn?.Application?.ActiveWorkbook);
        }

        /// <summary>
        /// Builds one workbook's four surfaces invisibly so each WebView2 starts initializing
        /// now, then reclaims the warm surfaces of workbooks that have fallen out of use.
        /// Idempotent, so the workbook lifecycle events can all call it unconditionally.
        /// </summary>
        internal static void WarmUp(ThisAddIn addIn, Excel.Workbook workbook)
        {
            if (addIn == null || workbook == null) return;

            // Add-in and macro workbooks are open but have no window the user can bring to the
            // front, so a surface warmed for one could never be shown.
            if (!addIn.HasVisibleWindow(workbook))
            {
                Modules.TalliarkLog.Trace("warm-up skipped: workbook has no visible window");
                return;
            }

            addIn.NoteWorkbookActivated(workbook);

            using (Modules.TalliarkLog.Time("warm-up total"))
            {
                // Ordered by how likely the user is to reach for each one.
                addIn.WarmUpTaskPaneFor(workbook);
                addIn.WarmUpFileManagerFor(workbook);
                addIn.WarmUpViewerWindowFor(workbook);
                addIn.WarmUpLinkerWindowFor(workbook);
            }

            addIn.EvictUnopenedSurfaces(WarmWorkbookLimit);
        }

        /// <summary>
        /// The shared environment, created on first request. Every WebView2 host awaits this
        /// rather than calling <see cref="CoreWebView2Environment.CreateAsync(string, string,
        /// CoreWebView2EnvironmentOptions)"/> itself — separate environments over one user
        /// data folder is what the runtime refuses outright.
        /// </summary>
        internal static Task<CoreWebView2Environment> GetEnvironmentAsync()
        {
            lock (_sync)
            {
                // A faulted task is not cached as the answer: WebView2 can fail to start for
                // reasons that clear on their own (a runtime update mid-session, a locked user
                // data folder), and a host asking later deserves a fresh attempt rather than
                // an inherited failure.
                if (_environmentTask == null
                    || _environmentTask.IsFaulted
                    || _environmentTask.IsCanceled)
                {
                    _environmentTask = CreateEnvironmentAsync();
                }

                return _environmentTask;
            }
        }

        private static async Task<CoreWebView2Environment> CreateEnvironmentAsync()
        {
            string userDataFolder = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "Talliark",
                "WebView2");

            using (Modules.TalliarkLog.Time("shared WebView2 environment create"))
            {
                CoreWebView2Environment environment = await CoreWebView2Environment.CreateAsync(
                    browserExecutableFolder: null,
                    userDataFolder: userDataFolder).ConfigureAwait(true);

                Modules.TalliarkLog.Trace("shared WebView2 environment ready");
                return environment;
            }
        }
    }
}
