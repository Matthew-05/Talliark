using Talliark.Addin.Modules.Infrastructure;

namespace Talliark.Addin.Modules.Services
{
    /// <summary>
    /// What an OCR run reports about one PDF alongside its status.
    ///
    /// Terminal updates normally carry only a message — an error string, or null to
    /// clear — so a plain string converts implicitly and those call sites read as
    /// they always did. A successful OCR update may additionally carry the host-only
    /// <see cref="PdfOrientationChanged"/> signal. Progress updates carry the stage
    /// the work is in, which the worker or the host states outright; nothing
    /// downstream infers it from <see cref="Message"/>.
    ///
    /// <see cref="FileIndex"/> and <see cref="FileCount"/> describe the run rather
    /// than the file, and only the host knows them, so it attaches them here rather
    /// than writing them into a sentence for the pane to parse back out.
    ///
    /// Public because it appears in <see cref="OcrService"/>'s public callback
    /// signature. The factories stay internal: one names host-internal stage
    /// constants and the other takes the internal worker protocol type, and
    /// widening either would pull Infrastructure into the public surface.
    /// </summary>
    public sealed class OcrStatusDetail
    {
        public string Message { get; set; }
        public string Stage { get; set; }
        public int? Current { get; set; }
        public int? Total { get; set; }
        public string Unit { get; set; }
        public int? FileIndex { get; set; }
        public int? FileCount { get; set; }

        /// <summary>
        /// Host-only terminal signal that OCR changed persisted page orientation and
        /// therefore also transformed stored link rectangles. It is not part of the
        /// WebView progress contract.
        /// </summary>
        internal bool PdfOrientationChanged { get; set; }

        public static implicit operator OcrStatusDetail(string message)
        {
            return message == null ? null : new OcrStatusDetail { Message = message };
        }

        /// <summary>A host-side progress update, for the stages no worker reports.</summary>
        internal static OcrStatusDetail ForStage(
            string message, string stage, int fileIndex, int fileCount,
            int? current = null, int? total = null, string unit = null)
        {
            return new OcrStatusDetail
            {
                Message = message,
                Stage = stage,
                Current = current,
                Total = total,
                Unit = unit,
                FileIndex = fileIndex,
                FileCount = fileCount,
            };
        }

        /// <summary>Wraps a worker progress line, adding the run position only the host knows.</summary>
        internal static OcrStatusDetail FromWorker(WorkerProgress progress, int fileIndex, int fileCount)
        {
            return new OcrStatusDetail
            {
                Message = progress.Message,
                Stage = progress.Stage,
                Current = progress.Current,
                Total = progress.Total,
                Unit = progress.Unit,
                FileIndex = fileIndex,
                FileCount = fileCount,
            };
        }
    }
}
