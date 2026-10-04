using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;

namespace Talliark.Addin.Modules.Infrastructure
{
    /// <summary>
    /// Opens a draft in the Windows default mail client. Simple MAPI is used first
    /// because mailto URIs have no portable attachment support; mailto remains a
    /// useful fallback for clients that register only as the URI handler.
    /// </summary>
    internal static class DefaultMailComposer
    {
        private const int MapiLogonUi = 0x00000001;
        private const int MapiDialog = 0x00000008;
        private const int MapiUserAbort = 1;

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Ansi)]
        private struct MapiMessage
        {
            internal int Reserved;
            [MarshalAs(UnmanagedType.LPStr)] internal string Subject;
            [MarshalAs(UnmanagedType.LPStr)] internal string NoteText;
            [MarshalAs(UnmanagedType.LPStr)] internal string MessageType;
            [MarshalAs(UnmanagedType.LPStr)] internal string DateReceived;
            [MarshalAs(UnmanagedType.LPStr)] internal string ConversationId;
            internal int Flags;
            internal IntPtr Originator;
            internal int RecipientCount;
            internal IntPtr Recipients;
            internal int FileCount;
            internal IntPtr Files;
        }

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Ansi)]
        private struct MapiFile
        {
            internal int Reserved;
            internal int Flags;
            internal int Position;
            [MarshalAs(UnmanagedType.LPStr)] internal string Path;
            [MarshalAs(UnmanagedType.LPStr)] internal string Name;
            internal IntPtr FileType;
        }

        [DllImport("MAPI32.DLL", CharSet = CharSet.Ansi, ExactSpelling = true)]
        private static extern int MAPISendMail(
            IntPtr session,
            IntPtr parentWindow,
            ref MapiMessage message,
            int flags,
            int reserved);

        internal sealed class Result
        {
            internal bool Opened { get; set; }
            internal bool AttachmentsNeedManualAddition { get; set; }
            internal string Error { get; set; }
        }

        internal static Result ShowDraft(
            string subject,
            string body,
            IEnumerable<string> attachmentPaths)
        {
            string[] attachments = (attachmentPaths ?? Enumerable.Empty<string>())
                .Where(path => !string.IsNullOrWhiteSpace(path))
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .ToArray();

            try
            {
                int code = ShowMapiDraft(subject, body, attachments);
                if (code == 0 || code == MapiUserAbort)
                    return new Result { Opened = true };

                TalliarkLog.Trace($"Simple MAPI could not open bug-report draft; result={code}.");
            }
            catch (Exception ex)
            {
                TalliarkLog.Trace($"Simple MAPI unavailable: {ex}");
            }

            string fallbackBody = body;
            if (attachments.Length > 0)
            {
                fallbackBody += Environment.NewLine + Environment.NewLine
                    + "The email app could not add these attachments automatically. "
                    + "Please attach them before sending:"
                    + Environment.NewLine
                    + string.Join(
                        Environment.NewLine,
                        attachments.Select(path => "- " + System.IO.Path.GetFileName(path)));
            }

            try
            {
                string uri = "mailto:?subject=" + Uri.EscapeDataString(subject ?? string.Empty)
                    + "&body=" + Uri.EscapeDataString(fallbackBody ?? string.Empty);

                Process.Start(new ProcessStartInfo(uri) { UseShellExecute = true });
                return new Result
                {
                    Opened = true,
                    AttachmentsNeedManualAddition = attachments.Length > 0
                };
            }
            catch (Exception ex)
            {
                TalliarkLog.Trace($"Default mail client could not be opened: {ex}");
                return new Result
                {
                    Error = "The default email app could not be opened."
                };
            }
        }

        private static int ShowMapiDraft(
            string subject,
            string body,
            string[] attachments)
        {
            IntPtr filePointer = IntPtr.Zero;
            int initializedFiles = 0;
            try
            {
                if (attachments.Length > 0)
                {
                    int descriptorSize = Marshal.SizeOf(typeof(MapiFile));
                    filePointer = Marshal.AllocHGlobal(descriptorSize * attachments.Length);
                    for (int index = 0; index < attachments.Length; index++)
                    {
                        var descriptor = new MapiFile
                        {
                            Position = -1,
                            Path = attachments[index],
                            Name = System.IO.Path.GetFileName(attachments[index])
                        };
                        Marshal.StructureToPtr(
                            descriptor,
                            IntPtr.Add(filePointer, descriptorSize * index),
                            false);
                        initializedFiles++;
                    }
                }

                var message = new MapiMessage
                {
                    Subject = subject,
                    NoteText = body,
                    RecipientCount = 0,
                    Recipients = IntPtr.Zero,
                    FileCount = attachments.Length,
                    Files = filePointer
                };

                return MAPISendMail(
                    IntPtr.Zero,
                    IntPtr.Zero,
                    ref message,
                    MapiLogonUi | MapiDialog,
                    0);
            }
            finally
            {
                DestroyStructures<MapiFile>(filePointer, initializedFiles);
            }
        }

        private static void DestroyStructures<T>(IntPtr pointer, int count)
        {
            if (pointer == IntPtr.Zero) return;

            int size = Marshal.SizeOf(typeof(T));
            for (int index = 0; index < count; index++)
                Marshal.DestroyStructure(IntPtr.Add(pointer, size * index), typeof(T));

            Marshal.FreeHGlobal(pointer);
        }
    }
}
