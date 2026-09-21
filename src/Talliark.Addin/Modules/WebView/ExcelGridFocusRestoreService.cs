using System;
using System.Runtime.InteropServices;
using System.Text;
using System.Windows.Forms;

namespace Talliark.Addin.Modules.WebView
{
    /// <summary>
    /// Restores Excel keyboard focus when a click lands on the worksheet grid after
    /// an embedded WebView input has focus.
    /// </summary>
    internal sealed class ExcelGridFocusRestoreService : IDisposable
    {
        private const int WH_MOUSE = 7;
        private const int HC_ACTION = 0;
        private const int WM_LBUTTONDOWN = 0x0201;
        private const int WM_RBUTTONDOWN = 0x0204;
        private const int WM_MBUTTONDOWN = 0x0207;
        private const int WM_XBUTTONDOWN = 0x020B;
        private const string ExcelGridClassName = "EXCEL7";

        private readonly Control _viewerSurface;
        private readonly MouseHookProc _hookProc;
        private IntPtr _hookHandle;
        private bool _disposed;

        internal ExcelGridFocusRestoreService(Control viewerSurface)
        {
            _viewerSurface = viewerSurface ?? throw new ArgumentNullException(nameof(viewerSurface));
            _hookProc = HandleMouseMessage;
            _hookHandle = SetWindowsHookEx(WH_MOUSE, _hookProc, IntPtr.Zero, GetCurrentThreadId());
            _viewerSurface.Disposed += OnViewerSurfaceDisposed;
        }

        [DllImport("user32.dll")]
        private static extern IntPtr SetFocus(IntPtr hWnd);

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern int GetClassName(IntPtr hWnd, StringBuilder className, int maxCount);

        [DllImport("user32.dll")]
        private static extern IntPtr SetWindowsHookEx(int idHook, MouseHookProc lpfn, IntPtr hMod, int dwThreadId);

        [DllImport("user32.dll")]
        private static extern bool UnhookWindowsHookEx(IntPtr hhk);

        [DllImport("user32.dll")]
        private static extern IntPtr CallNextHookEx(IntPtr hhk, int nCode, IntPtr wParam, IntPtr lParam);

        [DllImport("kernel32.dll")]
        private static extern int GetCurrentThreadId();

        private delegate IntPtr MouseHookProc(int nCode, IntPtr wParam, IntPtr lParam);

        public void Dispose()
        {
            if (_disposed) return;

            _disposed = true;
            if (_hookHandle != IntPtr.Zero)
            {
                UnhookWindowsHookEx(_hookHandle);
                _hookHandle = IntPtr.Zero;
            }
            _viewerSurface.Disposed -= OnViewerSurfaceDisposed;
        }

        private IntPtr HandleMouseMessage(int nCode, IntPtr wParam, IntPtr lParam)
        {
            // A hook procedure runs inside Excel's message pump: an exception escaping it
            // faults the host process, so nothing here may throw.
            try
            {
                if (!_disposed && nCode == HC_ACTION && IsMouseDown(wParam.ToInt32()))
                    RestoreFocusForExcelGridClick(lParam);
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"[Talliark] mouse hook failed: {ex.Message}");
            }

            return CallNextHookEx(_hookHandle, nCode, wParam, lParam);
        }

        private void RestoreFocusForExcelGridClick(IntPtr lParam)
        {
            if (IsPointerInsideViewerSurface())
                return;

            var mouseInfo = (MouseHookStruct)Marshal.PtrToStructure(lParam, typeof(MouseHookStruct));
            if (!IsExcelGridWindow(mouseInfo.Hwnd))
                return;

            RestoreExcelFocus();
        }

        internal static void RestoreExcelFocus()
        {
            try
            {
                int appHwnd = Globals.ThisAddIn.Application?.Hwnd ?? 0;
                if (appHwnd != 0)
                {
                    SetFocus(new IntPtr(appHwnd));
                }
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"[Talliark] RestoreExcelFocus failed: {ex.Message}");
            }
        }

        private void OnViewerSurfaceDisposed(object sender, EventArgs e)
        {
            Dispose();
        }

        private bool IsPointerInsideViewerSurface()
        {
            if (_viewerSurface.IsDisposed || !_viewerSurface.IsHandleCreated)
                return false;

            return _viewerSurface.RectangleToScreen(_viewerSurface.ClientRectangle)
                .Contains(Control.MousePosition);
        }

        private static bool IsMouseDown(int message) =>
            message == WM_LBUTTONDOWN
            || message == WM_RBUTTONDOWN
            || message == WM_MBUTTONDOWN
            || message == WM_XBUTTONDOWN;

        private static bool IsExcelGridWindow(IntPtr hWnd) =>
            string.Equals(GetWindowClassName(hWnd), ExcelGridClassName, StringComparison.Ordinal);

        private static string GetWindowClassName(IntPtr hWnd)
        {
            if (hWnd == IntPtr.Zero)
                return string.Empty;

            var className = new StringBuilder(256);
            return GetClassName(hWnd, className, className.Capacity) > 0
                ? className.ToString()
                : string.Empty;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct MouseHookStruct
        {
            public Point Point;
            public IntPtr Hwnd;
            public uint HitTestCode;
            public UIntPtr ExtraInfo;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct Point
        {
            public int X;
            public int Y;
        }
    }
}
