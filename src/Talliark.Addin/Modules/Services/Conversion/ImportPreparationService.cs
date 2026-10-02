using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using System.Windows.Forms;
using Talliark.Addin.Modules.UI;

namespace Talliark.Addin.Modules.Services.Conversion
{
    /// <summary>One file the user asked to import, before Talliark knows what it is.</summary>
    internal sealed class ImportCandidate
    {
        /// <summary>Path on disk. Null when the file arrived as bytes from the web layer.</summary>
        public string Path { get; set; }

        /// <summary>File name including extension. Required.</summary>
        public string Name { get; set; }

        /// <summary>File bytes. Null when <see cref="Path"/> is set.</summary>
        public byte[] Bytes { get; set; }

        /// <summary>Base-64 bytes as delivered by the web layer, kept to avoid a re-encode.</summary>
        public string Base64 { get; set; }

        /// <summary>Target folder id, carried through to the import request unchanged.</summary>
        public string FolderId { get; set; }
    }

    /// <summary>What the user agreed to import, decided before any file is touched.</summary>
    internal sealed class ImportSelectionPlan
    {
        /// <summary>True when the user dismissed the confirmation dialog; import nothing.</summary>
        public bool Cancelled { get; set; }

        /// <summary>Files that are already PDFs.</summary>
        public IList<ImportCandidate> PdfCandidates { get; } = new List<ImportCandidate>();

        /// <summary>Non-PDF files of a type the user chose to convert.</summary>
        public IList<ImportCandidate> ConvertCandidates { get; } = new List<ImportCandidate>();

        /// <summary>"report.zip: .zip files cannot be converted to PDF." style messages.</summary>
        public IList<string> SkippedMessages { get; } = new List<string>();

        /// <summary>True when there is nothing at all to do.</summary>
        public bool IsEmpty => PdfCandidates.Count == 0 && ConvertCandidates.Count == 0;
    }

    /// <summary>
    /// The import requests to hand to <see cref="PdfImportService"/>, plus the temp
    /// storage backing any converted PDFs.
    ///
    /// Disposing this deletes the converted files, so callers must keep it alive
    /// until the PDFs have been embedded in the workbook.
    /// </summary>
    internal sealed class PreparedImport : IDisposable
    {
        private readonly ConversionTempStore _tempStore;
        private bool _disposed;

        internal PreparedImport(ConversionTempStore tempStore)
        {
            _tempStore = tempStore;
        }

        /// <summary>PDFs to read from disk — originals plus freshly converted temp files.</summary>
        public IList<PdfPathImportRequest> PathRequests { get; } = new List<PdfPathImportRequest>();

        /// <summary>PDFs the web layer already handed over as base-64.</summary>
        public IList<PdfBase64ImportRequest> Base64Requests { get; } = new List<PdfBase64ImportRequest>();

        /// <summary>Files that could not be converted, and files skipped as unsupported.</summary>
        public IList<string> Errors { get; } = new List<string>();

        public bool HasWork => PathRequests.Count > 0 || Base64Requests.Count > 0;

        public void Dispose()
        {
            if (_disposed) return;
            _disposed = true;
            _tempStore?.Dispose();
        }
    }

    /// <summary>
    /// Front door for every import path in Talliark.
    ///
    /// Import happens in two phases so the user is asked before anything is read,
    /// converted or written:
    ///   1. <see cref="Plan"/> — classify the selection and, when it contains
    ///      non-PDFs, show <see cref="NonPdfImportDialog"/> to confirm which types
    ///      to convert. Nothing is touched on disk.
    ///   2. <see cref="PrepareAsync"/> — convert the agreed types into temp PDFs and
    ///      return ready-to-embed import requests.
    ///
    /// Keeping both phases here means the ribbon — its file picker, its folder
    /// picker and its clipboard, which delivers a picture as bytes rather than a
    /// path — OS drag-drop and the web dropzone all get identical behaviour.
    /// </summary>
    internal static class ImportPreparationService
    {
        /// <summary>
        /// Classifies the selection and confirms non-PDF conversions with the user.
        /// Returns immediately, with no dialog, when every file is already a PDF.
        /// Must be called on the UI thread.
        /// </summary>
        public static ImportSelectionPlan Plan(IWin32Window owner, IList<ImportCandidate> candidates)
        {
            var plan = new ImportSelectionPlan();
            if (candidates == null || candidates.Count == 0)
            {
                TalliarkLog.Trace("Import planning: nothing selected.");
                return plan;
            }

            TalliarkLog.Trace($"Import planning: {candidates.Count} candidate(s).");

            var convertible = new List<ImportCandidate>();
            var unsupported = new List<ImportCandidate>();

            foreach (ImportCandidate candidate in candidates)
            {
                string name = ResolveName(candidate);
                if (string.IsNullOrWhiteSpace(name))
                    continue;

                if (ConversionFormatCatalog.IsPdf(name))
                    plan.PdfCandidates.Add(candidate);
                else if (ConversionFormatCatalog.TryGetFormat(name, out _))
                    convertible.Add(candidate);
                else
                    unsupported.Add(candidate);
            }

            TalliarkLog.Trace(
                $"Import planning: {plan.PdfCandidates.Count} PDF, {convertible.Count} convertible, " +
                $"{unsupported.Count} unsupported.");

            // Nothing to ask about — every file is already a PDF.
            if (convertible.Count == 0 && unsupported.Count == 0)
                return plan;

            IList<ImportFormatSummary> convertibleSummaries = Summarise(convertible, describeKnownFormats: true);
            IList<ImportFormatSummary> unsupportedSummaries = Summarise(unsupported, describeKnownFormats: false);

            if (!NonPdfImportDialog.TryConfirm(
                    owner,
                    plan.PdfCandidates.Count,
                    convertibleSummaries,
                    unsupportedSummaries,
                    out ISet<string> selectedExtensions))
            {
                TalliarkLog.Trace("Import planning: the user cancelled the non-PDF confirmation.");
                plan.Cancelled = true;
                return plan;
            }

            TalliarkLog.Trace(
                $"Import planning: the user selected [{string.Join(", ", selectedExtensions)}] for conversion.");

            foreach (ImportCandidate candidate in convertible)
            {
                string extension = ConversionFormatCatalog.GetExtension(ResolveName(candidate));
                if (selectedExtensions.Contains(extension))
                    plan.ConvertCandidates.Add(candidate);
                else
                    plan.SkippedMessages.Add($"{ResolveName(candidate)}: skipped — {extension} was not selected for conversion.");
            }

            foreach (ImportCandidate candidate in unsupported)
            {
                string extension = ConversionFormatCatalog.GetExtension(ResolveName(candidate));
                plan.SkippedMessages.Add(string.IsNullOrEmpty(extension)
                    ? $"{ResolveName(candidate)}: skipped — files without an extension cannot be converted to PDF."
                    : $"{ResolveName(candidate)}: skipped — {extension} files cannot be converted to PDF.");
            }

            return plan;
        }

        /// <summary>
        /// Converts the agreed non-PDF files and assembles the import requests.
        /// Must be awaited on the UI thread — HTML rendering needs a message pump.
        /// </summary>
        public static async Task<PreparedImport> PrepareAsync(
            ImportSelectionPlan plan,
            IProgressReporter progress)
        {
            var tempStore = new ConversionTempStore();
            var prepared = new PreparedImport(tempStore);

            try
            {
                if (plan == null || plan.Cancelled)
                {
                    TalliarkLog.Trace("Import preparation: nothing to do (no plan, or cancelled).");
                    return prepared;
                }

                foreach (string message in plan.SkippedMessages)
                    prepared.Errors.Add(message);

                // PDFs need no work — queue them straight through.
                foreach (ImportCandidate candidate in plan.PdfCandidates)
                {
                    if (!string.IsNullOrWhiteSpace(candidate.Path))
                        prepared.PathRequests.Add(new PdfPathImportRequest(candidate.Path, candidate.FolderId));
                    else if (!string.IsNullOrEmpty(candidate.Base64))
                        prepared.Base64Requests.Add(new PdfBase64ImportRequest(
                            ResolveName(candidate), candidate.Base64, candidate.FolderId));
                    else if (candidate.Bytes != null)
                        prepared.Base64Requests.Add(new PdfBase64ImportRequest(
                            ResolveName(candidate), Convert.ToBase64String(candidate.Bytes), candidate.FolderId));
                }

                if (plan.ConvertCandidates.Count == 0)
                {
                    TalliarkLog.Trace(
                        $"Import preparation: {prepared.PathRequests.Count} path and " +
                        $"{prepared.Base64Requests.Count} base-64 PDF request(s), nothing to convert.");
                    return prepared;
                }

                var conversionRequests = plan.ConvertCandidates
                    .Select(candidate => new DocumentConversionRequest
                    {
                        SourcePath = candidate.Path,
                        SourceBytes = ResolveBytes(candidate),
                        FileName = ResolveName(candidate),
                        FolderId = candidate.FolderId,
                    })
                    .ToList();

                var service = new DocumentConversionService(tempStore);

                DocumentConversionResult converted;
                using (TalliarkLog.Time($"Converting {conversionRequests.Count} document(s)"))
                    converted = await service.ConvertAsync(conversionRequests, progress);

                TalliarkLog.Trace(
                    $"Import preparation: {converted.Converted.Count} converted, " +
                    $"{converted.Errors.Count} failed.");

                // The scratch file is a .pdf, but the workbook keeps the name the user
                // picked — 'terms.docx' stays 'terms.docx'.
                foreach (DocumentConversionSuccess success in converted.Converted)
                    prepared.PathRequests.Add(new PdfPathImportRequest(
                        success.PdfPath, success.FolderId, success.DisplayName));

                foreach (string error in converted.Errors)
                    prepared.Errors.Add(error);

                return prepared;
            }
            catch
            {
                prepared.Dispose();
                throw;
            }
        }

        /// <summary>Groups candidates by extension into dialog rows, ordered by descending count.</summary>
        private static IList<ImportFormatSummary> Summarise(
            IList<ImportCandidate> candidates,
            bool describeKnownFormats)
        {
            return candidates
                .GroupBy(
                    c => ConversionFormatCatalog.GetExtension(ResolveName(c)),
                    StringComparer.OrdinalIgnoreCase)
                .Select(group => new ImportFormatSummary(
                    group.Key,
                    describeKnownFormats && ConversionFormatCatalog.TryGetFormat(group.Key, out ConversionFormat format)
                        ? format.Description
                        : string.Empty,
                    group.Count()))
                .OrderByDescending(summary => summary.Count)
                .ThenBy(summary => summary.Extension, StringComparer.OrdinalIgnoreCase)
                .ToList();
        }

        private static string ResolveName(ImportCandidate candidate)
        {
            if (candidate == null)
                return string.Empty;

            if (!string.IsNullOrWhiteSpace(candidate.Name))
                return candidate.Name;

            if (string.IsNullOrWhiteSpace(candidate.Path))
                return string.Empty;

            try
            {
                return Path.GetFileName(candidate.Path);
            }
            catch
            {
                return candidate.Path;
            }
        }

        private static byte[] ResolveBytes(ImportCandidate candidate)
        {
            if (candidate.Bytes != null)
                return candidate.Bytes;

            if (string.IsNullOrEmpty(candidate.Base64))
                return null;   // DocumentConversionService reads from Path instead

            try
            {
                return Convert.FromBase64String(candidate.Base64);
            }
            catch (FormatException)
            {
                return null;
            }
        }
    }
}
