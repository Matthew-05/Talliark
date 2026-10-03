using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Talliark.Addin.Modules.CustomXml.Models;
using Talliark.Addin.Modules.Infrastructure;
using Excel = Microsoft.Office.Interop.Excel;

namespace Talliark.Addin.Modules.Services
{
    /// <summary>
    /// Runs OCR on one or more PDFs stored in the workbook by delegating to the
    /// bundled Python worker process via a stdin/stdout JSON-line protocol.
    ///
    /// Threading contract:
    ///   • <see cref="RunOcrAsync"/> must be called on the UI thread.
    ///   • Phase 1 (Excel COM reads) executes synchronously on the UI thread.
    ///   • Phase 2 (process I/O) executes on a <see cref="Task.Run"/> background thread.
    ///   • Phase 3 (storage writes + callbacks) marshals back to the UI thread
    ///     via the <see cref="Control"/> handle supplied to the constructor.
    /// </summary>
    public sealed class OcrService
    {
        private readonly Control _uiControl;
        private readonly ManageFilesService _manageService = new ManageFilesService();

        // Cancellation state — _runningSession is volatile so Cancel() (any thread) always
        // reads the latest value written by the background worker thread.
        private volatile PythonWorkerSession _runningSession;
        private CancellationTokenSource _cts;

        /// <summary>True while a RunOcrAsync call is in flight. UI-thread only.</summary>
        public bool IsRunning { get; private set; }

        /// <param name="uiControl">
        /// Any WinForms control that lives on the UI thread (e.g. the hosting
        /// <see cref="Form"/>). Used to marshal callbacks back to the UI thread.
        /// </param>
        public OcrService(Control uiControl)
        {
            _uiControl = uiControl ?? throw new ArgumentNullException(nameof(uiControl));
        }

        /// <summary>
        /// Cancels any in-progress OCR run. The worker process is killed immediately;
        /// the in-flight job and all remaining queued jobs are reverted to their
        /// original status (none / text) rather than marked as errors.
        /// Safe to call from any thread.
        /// </summary>
        public void Cancel()
        {
            _cts?.Cancel();
            _runningSession?.Kill();
        }

        /// <summary>
        /// Queues geometry-first OCR for every requested PDF. Recognized text is
        /// stored beside the sanitized visual PDF, so rerunning OCR replaces the
        /// sidecar geometry without creating another PDF text layer.
        /// </summary>
        public Task RunOcrAsync(
            IList<string> pdfIds,
            Excel.Workbook workbook,
            Action<string, string, OcrStatusDetail> onStatusUpdate)
        {
            return RunJobsAsync(pdfIds, workbook, onStatusUpdate, analysis: false,
                detectTables: Infrastructure.ExperimentalSettings.TableDetection);
        }

        /// <summary>
        /// Runs the complete OCR pipeline for one document and appends the on-demand
        /// Reconcile analysis under the same job id, progress stream and cancellation.
        /// </summary>
        public Task RunReconcileScanAsync(
            string pdfId,
            Excel.Workbook workbook,
            Action<string, string, OcrStatusDetail> onStatusUpdate)
        {
            if (string.IsNullOrWhiteSpace(pdfId))
                throw new ArgumentException("PDF id must be non-empty.", nameof(pdfId));
            return RunJobsAsync(new[] { pdfId }, workbook, onStatusUpdate, analysis: true,
                detectTables: true);
        }

        private async Task RunJobsAsync(
            IList<string> pdfIds,
            Excel.Workbook workbook,
            Action<string, string, OcrStatusDetail> onStatusUpdate,
            bool analysis,
            bool detectTables)
        {
            if (pdfIds == null || pdfIds.Count == 0) return;
            if (workbook == null) throw new ArgumentNullException(nameof(workbook));
            WorkbookProtectionGuard.ThrowIfStructureProtected(workbook);

            string runId = Guid.NewGuid().ToString("N");
            var batchClock = Stopwatch.StartNew();
            LogPerf(new Dictionary<string, object>
            {
                ["event"] = "batch_requested",
                ["run_id"] = runId,
                ["requested_files"] = pdfIds.Count,
            });

            if (!PythonWorkerSession.IsAvailable)
            {
                LogPerf(new Dictionary<string, object>
                {
                    ["event"] = "batch_end",
                    ["run_id"] = runId,
                    ["outcome"] = "worker-unavailable",
                    ["requested_files"] = pdfIds.Count,
                    ["total_ms"] = batchClock.ElapsedMilliseconds,
                });
                foreach (string id in pdfIds)
                    onStatusUpdate(id, "error", PythonWorkerSession.NotBuiltMessage);
                return;
            }

            var loadClock = Stopwatch.StartNew();
            var jobs = LoadJobData(pdfIds, workbook, analysis, detectTables);
            loadClock.Stop();
            if (jobs.Count == 0)
            {
                LogPerf(new Dictionary<string, object>
                {
                    ["event"] = "batch_end",
                    ["run_id"] = runId,
                    ["outcome"] = "no-files-loaded",
                    ["requested_files"] = pdfIds.Count,
                    ["loaded_files"] = 0,
                    ["workbook_load_ms"] = loadClock.ElapsedMilliseconds,
                    ["total_ms"] = batchClock.ElapsedMilliseconds,
                });
                return;
            }

            var metrics = new OcrBatchMetrics(runId, pdfIds.Count, jobs.Count);
            LogPerf(new Dictionary<string, object>
            {
                ["event"] = "batch_start",
                ["run_id"] = runId,
                ["requested_files"] = pdfIds.Count,
                ["loaded_files"] = jobs.Count,
                ["input_bytes"] = jobs.Sum(job => job.InputBytes),
                ["workbook_load_ms"] = loadClock.ElapsedMilliseconds,
            });

            for (int jobIndex = 0; jobIndex < jobs.Count; jobIndex++)
            {
                onStatusUpdate(
                    jobs[jobIndex].PdfId,
                    "queued",
                    OcrStatusDetail.ForStage(
                        $"Waiting — file {jobIndex + 1} of {jobs.Count}",
                        ProgressStages.Queue, jobIndex + 1, jobs.Count));
            }

            _cts = new CancellationTokenSource();
            IsRunning = true;
            try
            {
                await Task.Run(() => RunWorker(
                    jobs, workbook, onStatusUpdate, _cts.Token, metrics));
            }
            catch (Exception ex)
            {
                metrics.UnhandledError = ex.GetType().Name + ": " + ex.Message;
                int terminalJobs = metrics.Succeeded
                    + metrics.Failed
                    + metrics.Cancelled
                    + metrics.Skipped;
                metrics.Failed += Math.Max(0, metrics.Loaded - terminalJobs);
                throw;
            }
            finally
            {
                batchClock.Stop();
                var batchEnd = new Dictionary<string, object>
                {
                    ["event"] = "batch_end",
                    ["run_id"] = runId,
                    ["outcome"] = string.IsNullOrEmpty(metrics.UnhandledError)
                        ? (metrics.Cancelled > 0 ? "cancelled" : "complete")
                        : "error",
                    ["requested_files"] = metrics.Requested,
                    ["loaded_files"] = metrics.Loaded,
                    ["succeeded"] = metrics.Succeeded,
                    ["failed"] = metrics.Failed,
                    ["cancelled"] = metrics.Cancelled,
                    ["skipped"] = metrics.Skipped,
                    ["input_bytes"] = jobs.Sum(job => job.InputBytes),
                    ["workbook_load_ms"] = loadClock.ElapsedMilliseconds,
                    ["worker_start_ms"] = metrics.WorkerStartMs,
                    ["total_ms"] = batchClock.ElapsedMilliseconds,
                };
                if (!string.IsNullOrEmpty(metrics.UnhandledError))
                    batchEnd["error"] = metrics.UnhandledError;
                LogPerf(batchEnd);

                IsRunning = false;
                _cts.Dispose();
                _cts = null;
            }
        }

        private void RunWorker(
            IList<OcrJobEntry> jobs,
            Excel.Workbook workbook,
            Action<string, string, OcrStatusDetail> onStatusUpdate,
            CancellationToken token,
            OcrBatchMetrics metrics)
        {
            using (var session = new PythonWorkerSession())
            {
                var startClock = Stopwatch.StartNew();
                session.Start();
                startClock.Stop();
                metrics.WorkerStartMs = startClock.ElapsedMilliseconds;
                LogPerf(new Dictionary<string, object>
                {
                    ["event"] = "worker_started",
                    ["run_id"] = metrics.RunId,
                    ["startup_ms"] = metrics.WorkerStartMs,
                });
                _runningSession = session;

                try
                {
                    for (int jobIndex = 0; jobIndex < jobs.Count; jobIndex++)
                    {
                        OcrJobEntry job = jobs[jobIndex];
                        var jobClock = Stopwatch.StartNew();

                        // Cancelled or worker already gone — revert remaining to original status
                        if (token.IsCancellationRequested || session.IsDead)
                        {
                            bool cancelled = token.IsCancellationRequested;
                            var callbackClock = Stopwatch.StartNew();
                            Invoke(() => onStatusUpdate(
                                job.PdfId,
                                job.Analysis && cancelled ? "cancelled" : job.OriginalStatus,
                                null));
                            callbackClock.Stop();
                            if (cancelled) metrics.Cancelled++;
                            else metrics.Skipped++;
                            LogJobOutcome(
                                metrics.RunId, jobIndex, jobs.Count, job,
                                cancelled ? "cancelled" : "skipped-worker-dead",
                                cancelled ? null : "Worker was unavailable after a prior job.",
                                null, 0, 0, 0, 0, 0,
                                callbackClock.ElapsedMilliseconds,
                                jobClock.ElapsedMilliseconds);
                            continue;
                        }

                        var processingCallbackClock = Stopwatch.StartNew();
                        Invoke(() => onStatusUpdate(
                            job.PdfId,
                            "processing",
                            OcrStatusDetail.ForStage(
                                $"Preparing file {jobIndex + 1} of {jobs.Count}…",
                                ProgressStages.Prepare, jobIndex + 1, jobs.Count)));
                        processingCallbackClock.Stop();

                        var buildClock = Stopwatch.StartNew();
                        string jobJson = BuildJobJson(job);
                        buildClock.Stop();

                        var sendClock = Stopwatch.StartNew();
                        session.SendJob(
                            jobJson,
                            (current, total) => Invoke(() => onStatusUpdate(
                                job.PdfId,
                                "processing",
                                OcrStatusDetail.ForStage(
                                    $"Sending PDF chunk {current} of {total}…",
                                    ProgressStages.Transfer, jobIndex + 1, jobs.Count,
                                    current, total, "chunk"))));
                        sendClock.Stop();

                        // Read lines until we get a terminal result for this job_id
                        var waitClock = Stopwatch.StartNew();
                        string lastProgressStage = null;
                        var progressLogClock = Stopwatch.StartNew();
                        string resultLine = session.ReadResultLine(
                            job.PdfId,
                            progress =>
                            {
                                OcrStatusDetail presentedProgress = PresentWorkerProgress(
                                    job, progress, jobIndex + 1, jobs.Count);
                                if (presentedProgress != null)
                                {
                                    Invoke(() => onStatusUpdate(
                                        job.PdfId,
                                        "processing",
                                        presentedProgress));
                                }

                                string stage = progress.Stage;
                                if (string.Equals(stage, lastProgressStage, StringComparison.Ordinal)
                                    && progressLogClock.ElapsedMilliseconds < 5000)
                                    return;

                                lastProgressStage = stage;
                                progressLogClock.Restart();
                                LogPerf(new Dictionary<string, object>
                                {
                                    ["event"] = "progress",
                                    ["run_id"] = metrics.RunId,
                                    ["job_index"] = jobIndex + 1,
                                    ["job_count"] = jobs.Count,
                                    ["pdf_id"] = job.PdfId,
                                    ["name"] = job.Name,
                                    ["stage"] = stage,
                                    ["message"] = progress.Message,
                                    ["elapsed_ms"] = jobClock.ElapsedMilliseconds,
                                });
                            });
                        waitClock.Stop();

                        if (resultLine == null)
                        {
                            // Stream closed — either we killed the process (cancellation) or it crashed
                            bool cancelled = token.IsCancellationRequested;
                            var callbackClock = Stopwatch.StartNew();
                            Invoke(() => onStatusUpdate(
                                job.PdfId,
                                cancelled ? (job.Analysis ? "cancelled" : job.OriginalStatus) : "error",
                                cancelled ? null : "Worker closed unexpectedly."));
                            callbackClock.Stop();
                            if (cancelled) metrics.Cancelled++;
                            else metrics.Failed++;
                            LogJobOutcome(
                                metrics.RunId, jobIndex, jobs.Count, job,
                                cancelled ? "cancelled" : "worker-died",
                                cancelled ? null : "Worker closed unexpectedly.",
                                null,
                                buildClock.ElapsedMilliseconds,
                                sendClock.ElapsedMilliseconds,
                                waitClock.ElapsedMilliseconds,
                                0, 0,
                                processingCallbackClock.ElapsedMilliseconds
                                    + callbackClock.ElapsedMilliseconds,
                                jobClock.ElapsedMilliseconds);
                            continue;
                        }

                        var parseClock = Stopwatch.StartNew();
                        var parsed = ParseResultLine(resultLine);
                        parseClock.Stop();

                        // The host is the storage boundary for the experimental
                        // feature. Even if an older or mismatched worker were to
                        // return table structure, an ordinary gated-off OCR run
                        // must neither expose nor persist it.
                        if (!job.Analysis && !job.DetectTables)
                            parsed.TableStructureBase64 = string.Empty;

                        if (job.Analysis
                            && string.Equals(parsed.Status, "success", StringComparison.Ordinal)
                            && string.IsNullOrEmpty(parsed.ReconcileBase64))
                        {
                            parsed.Status = "error";
                            parsed.Error = GetDiagnosticString(parsed.Diagnostics, "reconcile_error");
                            if (string.IsNullOrEmpty(parsed.Error))
                                parsed.Error = "The Reconcile scan did not return a result.";
                        }

                        long storageMs = 0;
                        long callbackMs = processingCallbackClock.ElapsedMilliseconds;
                        string outcome = parsed.Status;
                        string error = parsed.Error;

                        if (parsed.Status == "success")
                        {
                            Invoke(() =>
                            {
                                // Writing the result into the workbook is real work
                                // on a large document, and it is the last thing that
                                // happens to this file. Reporting it is what carries
                                // the bar to the end of the run rather than leaving it
                                // stalled on the worker's last message.
                                onStatusUpdate(
                                    job.PdfId,
                                    "processing",
                                    OcrStatusDetail.ForStage(
                                        "Saving to workbook…",
                                        ProgressStages.Finalizing, jobIndex + 1, jobs.Count));

                                var storageClock = Stopwatch.StartNew();
                                try
                                {
                                    bool pdfOrientationChanged = false;
                                    if (job.Analysis)
                                    {
                                        SaveReconcileResult(workbook, job, parsed);
                                    }
                                    else if (string.Equals(job.Mode, "geometry-only", StringComparison.Ordinal))
                                    {
                                        _manageService.UpdatePdfGeometry(
                                            workbook, job.PdfId,
                                            parsed.GeometryBase64 ?? string.Empty,
                                            parsed.TableStructureBase64 ?? string.Empty,
                                            parsed.DocumentValuesBase64 ?? string.Empty,
                                            parsed.FinancialStructureBase64 ?? string.Empty);
                                    }
                                    else
                                    {
                                        _manageService.UpdatePdfAfterOcr(
                                            workbook,
                                            job.PdfId,
                                            parsed.PdfBase64 ?? string.Empty,
                                            parsed.GeometryBase64 ?? string.Empty,
                                            parsed.TableStructureBase64 ?? string.Empty,
                                            parsed.DocumentValuesBase64 ?? string.Empty,
                                            parsed.FinancialStructureBase64 ?? string.Empty);
                                    }
                                    if (!job.Analysis)
                                    {
                                        pdfOrientationChanged = new RotatePageService().ApplyOcrRotationCorrections(
                                            job.PdfId,
                                            parsed.PageRotations,
                                            workbook);
                                    }
                                    storageClock.Stop();
                                    storageMs = storageClock.ElapsedMilliseconds;

                                    var callbackClock = Stopwatch.StartNew();
                                    onStatusUpdate(
                                        job.PdfId,
                                        job.Analysis ? "complete" : PdfStatus.Ocr,
                                        pdfOrientationChanged
                                            ? new OcrStatusDetail { PdfOrientationChanged = true }
                                            : null);
                                    callbackClock.Stop();
                                    callbackMs += callbackClock.ElapsedMilliseconds;
                                }
                                catch (Exception ex)
                                {
                                    storageClock.Stop();
                                    storageMs = storageClock.ElapsedMilliseconds;
                                    outcome = "storage-error";
                                    error = ex.Message;
                                    var callbackClock = Stopwatch.StartNew();
                                    onStatusUpdate(job.PdfId, "error", ex.Message);
                                    callbackClock.Stop();
                                    callbackMs += callbackClock.ElapsedMilliseconds;
                                }
                            });
                        }
                        else
                        {
                            var callbackClock = Stopwatch.StartNew();
                            Invoke(() => onStatusUpdate(job.PdfId, "error", parsed.Error));
                            callbackClock.Stop();
                            callbackMs += callbackClock.ElapsedMilliseconds;
                        }

                        jobClock.Stop();
                        if (string.Equals(outcome, "success", StringComparison.Ordinal))
                            metrics.Succeeded++;
                        else
                            metrics.Failed++;

                        LogJobOutcome(
                            metrics.RunId, jobIndex, jobs.Count, job, outcome, error,
                            parsed.Diagnostics,
                            buildClock.ElapsedMilliseconds,
                            sendClock.ElapsedMilliseconds,
                            waitClock.ElapsedMilliseconds,
                            parseClock.ElapsedMilliseconds,
                            storageMs,
                            callbackMs,
                            jobClock.ElapsedMilliseconds,
                            parsed.PdfBase64,
                            parsed.GeometryBase64,
                            parsed.TableStructureBase64,
                            parsed.DocumentValuesBase64,
                            parsed.FinancialStructureBase64);
                    }
                }
                finally
                {
                    _runningSession = null;
                }
            }
        }

        /// <summary>
        /// Writes a machine-readable, single-line performance record. The OCR_PERF
        /// prefix makes a large mixed debug log easy to filter before analysis.
        /// </summary>
        private static void LogJobOutcome(
            string runId,
            int jobIndex,
            int jobCount,
            OcrJobEntry job,
            string outcome,
            string error,
            Dictionary<string, object> diagnostics,
            long buildJsonMs,
            long sendMs,
            long waitMs,
            long parseMs,
            long storageMs,
            long callbackMs,
            long totalMs,
            string outputPdfBase64 = null,
            string geometryBase64 = null,
            string tableStructureBase64 = null,
            string documentValuesBase64 = null,
            string financialStructureBase64 = null)
        {
            var record = new Dictionary<string, object>
            {
                ["event"] = "job_end",
                ["run_id"] = runId,
                ["job_index"] = jobIndex + 1,
                ["job_count"] = jobCount,
                ["pdf_id"] = job.PdfId,
                ["name"] = job.Name,
                ["mode"] = job.Mode,
                ["outcome"] = outcome,
                ["input_bytes"] = job.InputBytes,
                ["output_pdf_bytes"] = Base64DecodedLength(outputPdfBase64),
                ["geometry_bytes"] = Base64DecodedLength(geometryBase64),
                ["table_structure_bytes"] = Base64DecodedLength(tableStructureBase64),
                ["document_values_bytes"] = Base64DecodedLength(documentValuesBase64),
                ["financial_structure_bytes"] = Base64DecodedLength(financialStructureBase64),
                ["workbook_binary_load_ms"] = job.LoadMs,
                ["host"] = new Dictionary<string, object>
                {
                    ["build_json_ms"] = buildJsonMs,
                    ["send_ms"] = sendMs,
                    ["wait_ms"] = waitMs,
                    ["parse_ms"] = parseMs,
                    ["workbook_storage_ms"] = storageMs,
                    ["ui_callback_ms"] = callbackMs,
                    ["total_ms"] = totalMs,
                },
            };
            if (diagnostics != null)
                record["worker"] = diagnostics;
            if (!string.IsNullOrEmpty(error))
                record["error"] = error;
            LogPerf(record);
        }

        private static void LogPerf(Dictionary<string, object> record)
        {
            try
            {
                string json = new JavaScriptSerializer
                {
                    MaxJsonLength = int.MaxValue,
                }.Serialize(record);
                TalliarkLog.Trace("OCR_PERF " + json);
            }
            catch (Exception ex)
            {
                TalliarkLog.Trace("OCR_PERF serialization-error=" + ex.Message);
            }
        }

        private static long Base64DecodedLength(string value)
        {
            if (string.IsNullOrEmpty(value)) return 0;
            long padding = value.EndsWith("==", StringComparison.Ordinal) ? 2
                : value.EndsWith("=", StringComparison.Ordinal) ? 1 : 0;
            return (value.Length / 4L) * 3L - padding;
        }

        private static void SaveReconcileResult(
            Excel.Workbook workbook, OcrJobEntry job, OcrWorkerResult result)
        {
var store = new CustomXml.TalliarkCustomXmlPartStore(workbook);
ReconcileWorkspace workspace = store.LoadReconcileWorkspace();
            ReconcileDocument document = workspace.Primary != null
                && string.Equals(workspace.Primary.Id, job.DocumentId, StringComparison.Ordinal)
                ? workspace.Primary : null;
            if (document?.Version != null
                && string.Equals(document.Version.Id, job.VersionId, StringComparison.Ordinal))
            {
                // Build the complete replacement in memory and publish it with the
                // store's add-before-delete atomic replacement. The original PDF and
                // import metadata remain the independent snapshot imported by the user.
                document.Version.GeometryBase64 = result.GeometryBase64 ?? string.Empty;
                document.Version.TableStructureBase64 = result.TableStructureBase64 ?? string.Empty;
                document.Version.DocumentValuesBase64 = result.DocumentValuesBase64 ?? string.Empty;
                document.Version.FinancialStructureBase64 = result.FinancialStructureBase64 ?? string.Empty;
                document.Version.ReconcileBase64 = result.ReconcileBase64 ?? string.Empty;
                store.SaveReconcileWorkspace(workspace);
                return;
            }

            // A comparison slot is unversioned: the scan belongs to the slot
            // itself, and re-adding the statement replaces the whole slot.
            ReconcileComparison comparison = workspace.Comparisons
                .FirstOrDefault(c => string.Equals(c.Id, job.DocumentId, StringComparison.Ordinal));
            if (comparison == null)
                throw new InvalidOperationException("The Reconcile statement was replaced while its scan was running.");

            comparison.GeometryBase64 = result.GeometryBase64 ?? string.Empty;
            comparison.TableStructureBase64 = result.TableStructureBase64 ?? string.Empty;
            comparison.DocumentValuesBase64 = result.DocumentValuesBase64 ?? string.Empty;
            comparison.FinancialStructureBase64 = result.FinancialStructureBase64 ?? string.Empty;
            comparison.ReconcileBase64 = result.ReconcileBase64 ?? string.Empty;
            store.SaveReconcileWorkspace(workspace);
        }

        private static OcrWorkerResult ParseResultLine(string line)
        {
            try
            {
                var obj = PythonWorkerSession.Deserialize(line);
                string status = PythonWorkerSession.GetString(obj, "status");

                if (status == "success")
                {
                    return new OcrWorkerResult
                    {
                        Status = "success",
                        PdfBase64 = PythonWorkerSession.GetString(obj, "pdf_base64"),
                        GeometryBase64 = PythonWorkerSession.GetString(obj, "geometry_base64"),
                        TableStructureBase64 = PythonWorkerSession.GetString(obj, "table_structure_base64"),
                        DocumentValuesBase64 = PythonWorkerSession.GetString(obj, "document_values_base64"),
                        FinancialStructureBase64 = PythonWorkerSession.GetString(obj, "financial_structure_base64"),
                        ReconcileBase64 = PythonWorkerSession.GetString(obj, "reconcile_base64"),
                        PageRotations = ParsePageRotations(
                            PythonWorkerSession.GetDictionary(obj, "page_rotations")),
                        Diagnostics = PythonWorkerSession.GetDictionary(obj, "diagnostics"),
                    };
                }

                string err = PythonWorkerSession.GetString(obj, "error");
                return new OcrWorkerResult
                {
                    Status = "error",
                    Error = string.IsNullOrEmpty(err) ? "Unknown error" : err,
                    Diagnostics = PythonWorkerSession.GetDictionary(obj, "diagnostics"),
                };
            }
            catch (Exception ex)
            {
                return new OcrWorkerResult
                {
                    Status = "error",
                    Error = "Failed to parse worker response: " + ex.Message,
                };
            }
        }

        private static string GetDiagnosticString(
            Dictionary<string, object> diagnostics, string key)
        {
            return diagnostics != null && diagnostics.TryGetValue(key, out object value)
                ? value?.ToString() ?? string.Empty
                : string.Empty;
        }

        private static string BuildJobJson(OcrJobEntry job)
        {
            var sb = new StringBuilder();
            sb.Append("{\"job_id\":");
            PythonWorkerSession.AppendJsonString(sb, job.PdfId);
            sb.Append(",\"command\":\"ocr\",\"pdf_base64\":");
            PythonWorkerSession.AppendJsonString(sb, job.Base64);
            sb.Append(",\"mode\":");
            PythonWorkerSession.AppendJsonString(sb, job.Mode ?? "full");
            if (job.Analysis)
            {
                sb.Append(",\"analysis\":true,\"document_id\":");
                PythonWorkerSession.AppendJsonString(sb, job.DocumentId);
                sb.Append(",\"version_id\":");
                PythonWorkerSession.AppendJsonString(sb, job.VersionId);
            }
            sb.Append(",\"detect_tables\":");
            sb.Append(job.DetectTables ? "true" : "false");
            sb.Append('}');
            return sb.ToString();
        }

        private static Dictionary<int, int> ParsePageRotations(
            Dictionary<string, object> raw)
        {
            var rotations = new Dictionary<int, int>();
            if (raw == null) return rotations;

            foreach (KeyValuePair<string, object> entry in raw)
            {
                if (!int.TryParse(entry.Key, out int pageIndex) || pageIndex < 0)
                    throw new InvalidOperationException(
                        "The OCR worker returned an invalid page rotation index.");

                int rotation = Convert.ToInt32(entry.Value);
                if (rotation != 90 && rotation != 180 && rotation != 270)
                    throw new InvalidOperationException(
                        "The OCR worker returned an invalid page rotation correction.");
                rotations[pageIndex] = rotation;
            }
            return rotations;
        }

        /// <summary>
        /// Keeps the ordinary OCR presentation aligned with the experimental
        /// table-detection gate. Ruled-cell recovery remains unconditional OCR
        /// repair, but when table detection is off it is presented as generic
        /// text refinement. A table-structure update in that mode is unexpected
        /// detector work and is not exposed to the pane.
        /// </summary>
        private static OcrStatusDetail PresentWorkerProgress(
            OcrJobEntry job,
            WorkerProgress progress,
            int fileIndex,
            int fileCount)
        {
            bool exposeTableDetection = job.Analysis || job.DetectTables;
            if (!exposeTableDetection
                && string.Equals(progress.Stage, ProgressStages.TableRecovery, StringComparison.Ordinal))
            {
                return OcrStatusDetail.ForStage(
                    "Refining text recognition…",
                    ProgressStages.AdaptiveOcr,
                    fileIndex,
                    fileCount,
                    progress.Current,
                    progress.Total,
                    progress.Unit);
            }

            if (!exposeTableDetection
                && string.Equals(progress.Stage, ProgressStages.TableStructure, StringComparison.Ordinal))
                return null;

            return OcrStatusDetail.FromWorker(progress, fileIndex, fileCount);
        }

        /// <summary>
        /// Reads the base64 bytes for the requested PDFs from the workbook and assigns
        /// full OCR mode. Must be called on the UI thread.
        /// </summary>
        private static IList<OcrJobEntry> LoadJobData(
            IList<string> pdfIds, Excel.Workbook workbook, bool analysis, bool detectTables)
        {
            var store = new CustomXml.TalliarkCustomXmlPartStore(workbook);
            if (analysis)
            {
                ReconcileWorkspace workspace = store.LoadReconcileWorkspace();
                var reconcileJobs = new List<OcrJobEntry>();
foreach (string id in pdfIds)
                {
                    ReconcileDocument document = workspace.Primary != null
                        && string.Equals(workspace.Primary.Id, id, StringComparison.Ordinal)
                        ? workspace.Primary : null;
                    if (document?.Version == null)
                    {
                        // A comparison slot has no version identity: the job's
                        // version id is its own slot id, and the scan result
                        // lands back on the slot.
                        ReconcileComparison comparison = workspace.Comparisons
                            .FirstOrDefault(c => string.Equals(c.Id, id, StringComparison.Ordinal));
                        if (comparison == null) continue;
                        reconcileJobs.Add(new OcrJobEntry
                        {
                            PdfId = comparison.Id,
                            DocumentId = comparison.Id,
                            VersionId = comparison.Id,
                            Name = comparison.DisplayName ?? string.Empty,
                            Base64 = comparison.PdfBase64 ?? string.Empty,
                            InputBytes = Base64DecodedLength(comparison.PdfBase64),
                            Mode = "full",
                            OriginalStatus = PdfStatus.None,
                            Analysis = true,
                            DetectTables = true,
                        });
                        continue;
                    }
                    reconcileJobs.Add(new OcrJobEntry
                    {
                        PdfId = document.Id,
                        DocumentId = document.Id,
                        VersionId = document.Version.Id,
                        Name = document.DisplayName ?? string.Empty,
                        Base64 = document.Version.Base64 ?? string.Empty,
                        InputBytes = Base64DecodedLength(document.Version.Base64),
                        Mode = "full",
                        OriginalStatus = PdfStatus.None,
                        Analysis = true,
                        DetectTables = true,
                    });
                }
                return reconcileJobs;
            }
            TalliarkContent content = store.LoadContent(); // metadata only — fast

            var result = new List<OcrJobEntry>();
            foreach (string id in pdfIds)
            {
                var metadata = content.Pdfs.FirstOrDefault(
                    p => string.Equals(p.Id, id, StringComparison.Ordinal));
                if (metadata == null) continue;

                string status = metadata.OcrStatus ?? PdfStatus.None;
                var loadClock = Stopwatch.StartNew();
                store.TryLoadPdfBinary(id, out PdfBinaryParts parts);
                loadClock.Stop();
                result.Add(new OcrJobEntry
                {
                    PdfId = id,
                    Name = metadata.Name ?? string.Empty,
                    Base64 = parts.Base64 ?? string.Empty,
                    InputBytes = Base64DecodedLength(parts.Base64),
                    LoadMs = loadClock.ElapsedMilliseconds,
                    Mode = "full",
                    OriginalStatus = status,
                    Analysis = analysis,
                    DetectTables = detectTables,
                });
            }
            return result;
        }

        private void Invoke(Action action)
        {
            if (_uiControl.InvokeRequired)
                _uiControl.Invoke(action);
            else
                action();
        }

        private sealed class OcrJobEntry
        {
            public string PdfId { get; set; }
            public string Name { get; set; }
            public string Base64 { get; set; }
            public long InputBytes { get; set; }
            public long LoadMs { get; set; }
            public string Mode { get; set; }
            public string OriginalStatus { get; set; }
            public bool Analysis { get; set; }
            public bool DetectTables { get; set; }
            public string DocumentId { get; set; }
            public string VersionId { get; set; }
        }

        private sealed class OcrBatchMetrics
        {
            public OcrBatchMetrics(string runId, int requested, int loaded)
            {
                RunId = runId;
                Requested = requested;
                Loaded = loaded;
            }

            public string RunId { get; }
            public int Requested { get; }
            public int Loaded { get; }
            public int Succeeded { get; set; }
            public int Failed { get; set; }
            public int Cancelled { get; set; }
            public int Skipped { get; set; }
            public long WorkerStartMs { get; set; }
            public string UnhandledError { get; set; }
        }

        private sealed class OcrWorkerResult
        {
            public string Status { get; set; }
            public string PdfBase64 { get; set; }
            public string GeometryBase64 { get; set; }
            public string TableStructureBase64 { get; set; }
            public string DocumentValuesBase64 { get; set; }

            public string FinancialStructureBase64 { get; set; }
            public string ReconcileBase64 { get; set; }
            public Dictionary<int, int> PageRotations { get; set; }
            public string Error { get; set; }

            /// <summary>
            /// Optional OcrDiagnostics object from the worker. Null when the worker omitted
            /// it or the line failed to parse — debug logging only, never behavioural.
            /// </summary>
            public Dictionary<string, object> Diagnostics { get; set; }
        }
    }
}
