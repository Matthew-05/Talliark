using System;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace Talliark.Addin.Modules.Infrastructure
{
    /// <summary>
    /// Last-resort handlers for exceptions the add-in did not catch itself.
    /// </summary>
    /// <remarks>
    /// Excel disables an add-in that lets an exception escape into it. Every event handler
    /// guards its own body; these handlers are the backstop for the paths that were missed,
    /// turning a process-level fault into a log line. They cannot make a crash survivable —
    /// an <see cref="AppDomain.UnhandledException"/> is already fatal — but they name the
    /// culprit, and the task and WinForms handlers can actually absorb the fault.
    /// </remarks>
    internal static class AddinExceptionGuard
    {
        private static bool _installed;
        private static UnhandledExceptionEventHandler _domainHandler;
        private static ThreadExceptionEventHandler _threadHandler;
        private static EventHandler<UnobservedTaskExceptionEventArgs> _taskHandler;

        internal static void Install()
        {
            if (_installed) return;
            _installed = true;

            _domainHandler = (_, e) => TalliarkLog.Trace(
                $"UNHANDLED APPDOMAIN EXCEPTION: {e.ExceptionObject}");
            AppDomain.CurrentDomain.UnhandledException += _domainHandler;

            _threadHandler = (_, e) => TalliarkLog.Trace(
                $"UNHANDLED UI THREAD EXCEPTION: {e.Exception}");
            Application.ThreadException += _threadHandler;

            // CatchException keeps an exception on one of the add-in's own forms from
            // unwinding through Excel's message pump. Best effort: the mode cannot always be
            // changed once the host has started, and the subscription above still holds.
            try
            {
                Application.SetUnhandledExceptionMode(UnhandledExceptionMode.CatchException);
            }
            catch (Exception ex)
            {
                TalliarkLog.Trace($"unhandled exception mode not set: {ex.Message}");
            }

            _taskHandler = (_, e) =>
            {
                TalliarkLog.Trace($"UNOBSERVED TASK EXCEPTION: {e.Exception}");
                e.SetObserved();
            };
            TaskScheduler.UnobservedTaskException += _taskHandler;
        }

        internal static void Uninstall()
        {
            if (!_installed) return;
            _installed = false;

            if (_domainHandler != null)
                AppDomain.CurrentDomain.UnhandledException -= _domainHandler;
            if (_threadHandler != null)
                Application.ThreadException -= _threadHandler;
            if (_taskHandler != null)
                TaskScheduler.UnobservedTaskException -= _taskHandler;

            _domainHandler = null;
            _threadHandler = null;
            _taskHandler = null;
        }
    }
}
