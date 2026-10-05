using System;
using System.Collections.Generic;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using Talliark.Addin.Modules.CustomXml;
using Talliark.Addin.Modules.CustomXml.Models;
using Talliark.Addin.Modules.CustomXml.Serialization;
using Talliark.Addin.Modules.Infrastructure;
using Excel = Microsoft.Office.Interop.Excel;

namespace Talliark.Addin.Modules.Services
{
    /// <summary>
    /// Coordinates review against immutable workbook evidence. Excel reads/writes
    /// stay on the caller's UI thread; arithmetic runs only in the worker.
    /// </summary>
    internal sealed class ReconcileReviewService : IDisposable
    {
        private readonly SemaphoreSlim _gate = new SemaphoreSlim(1, 1);
        private PythonWorkerSession _session;
        private string _sourceXml;
        private readonly Dictionary<string, ReconcileStoredResult> _sources = new Dictionary<string, ReconcileStoredResult>(StringComparer.Ordinal);
        private bool _disposed;
        private static JavaScriptSerializer Serializer() => new JavaScriptSerializer { MaxJsonLength = int.MaxValue, RecursionLimit = 256 };

        public async Task<string> ProcessAsync(Excel.Workbook workbook, string json, bool scanning)
        {
            // Generated binding identifies intent; forward the original object
            // so optional fields remain absent, rather than becoming JSON nulls.
            string requestId = string.Empty;
            string pdfId = string.Empty;
            try
            {
                ReviewRequest request = Serializer().Deserialize<ReviewRequest>(json);
                requestId = request?.requestId ?? string.Empty;
                pdfId = request?.pdfId ?? string.Empty;
                await _gate.WaitAsync();
                try
                {
                    if (_disposed) throw new ObjectDisposedException(nameof(ReconcileReviewService));
                    if (request == null || request.version != 1 || request.type != "reconcile-review-request")
                        throw new InvalidOperationException("Unsupported review request.");
                    if (scanning) throw new InvalidOperationException("Wait for the scan to finish before reviewing this document.");
                    if (request.mode == "save-workbook")
                    {
                        if (request.operations == null || request.operations.Count != 0)
                            throw new InvalidOperationException("Saving the workbook cannot apply review operations.");
                        // The gate has drained pending XML writes. Excel owns
                        // Save As, BeforeSave handlers and cancellation.
                        workbook.Save();
                        if (!workbook.Saved)
                            throw new InvalidOperationException("Workbook save did not complete. Your review remains in the open workbook.");
                        return Serializer().Serialize(new Dictionary<string, object>
                        {
                            ["type"] = "reconcile-review-response", ["version"] = 1,
                            ["requestId"] = requestId, ["pdfId"] = pdfId, ["status"] = "workbook-saved",
                        });
                    }
                    if (request.mode == "commit") WorkbookProtectionGuard.ThrowIfStructureProtected(workbook);
                    var store = new TalliarkCustomXmlPartStore(workbook);
                    string sourceXml = store.LoadReconcileWorkspaceXml();
                    if (!string.Equals(sourceXml, _sourceXml, StringComparison.Ordinal))
                    {
                        _sources.Clear();
                        _sourceXml = sourceXml;
                    }
                    if (!_sources.TryGetValue(pdfId, out ReconcileStoredResult source))
                    {
                        source = new ReconcileResultService().LoadResult(TalliarkReconcileSerializer.FromXml(sourceXml), pdfId);
                        _sources[pdfId] = source;
                    }
                    if (string.IsNullOrEmpty(source.ReconcileBase64)) throw new InvalidOperationException("Scan this document before reviewing its relationships.");
                    if (source.Staleness != "current" && request.mode != "load")
                        throw new InvalidOperationException("Re-scan stale evidence before applying review decisions.");
                    string prior = store.LoadReconcileReview(pdfId);
                    string jobId = Guid.NewGuid().ToString();
                    var job = new Dictionary<string, object>
                    {
                        ["job_id"] = jobId, ["command"] = "reconcile-review",
                        ["request"] = Serializer().DeserializeObject(json),
                        ["model_base64"] = source.ReconcileBase64,
                        ["values_base64"] = source.DocumentValuesBase64 ?? string.Empty,
                        ["review_base64"] = prior ?? string.Empty,
                    };
                    string line = await Task.Run(() => RunWorker(jobId, Serializer().Serialize(job)));
                    var terminal = Serializer().Deserialize<Dictionary<string, object>>(line);
                    if (PythonWorkerSession.GetString(terminal, "status") != "success")
                        throw new InvalidOperationException(PythonWorkerSession.GetString(terminal, "error"));
                    ReviewWorkerResult typed = Serializer().Deserialize<ReviewWorkerResult>(line);
                    if (typed.response == null || typed.response.requestId != requestId || typed.response.pdfId != pdfId)
                        throw new InvalidOperationException("Worker response does not match this review request.");
                    if (!string.IsNullOrEmpty(typed.review_base64) && !string.Equals(typed.review_base64, prior, StringComparison.Ordinal))
                    {
                        // Re-read after background work: a scan, second window,
                        // document replacement or save must not be overwritten.
                        if (!string.Equals(store.LoadReconcileWorkspaceXml(), sourceXml, StringComparison.Ordinal)
                            || !string.Equals(store.LoadReconcileReview(pdfId), prior, StringComparison.Ordinal))
                            throw new InvalidOperationException("The document or review changed. Reload before applying changes.");
                        if (request.mode != "load" || !workbook.ProtectStructure)
                        {
                            WorkbookProtectionGuard.ThrowIfStructureProtected(workbook);
                            store.SaveReconcileReview(pdfId, typed.review_base64);
                            workbook.Saved = false;
                        }
                    }
                    return Serializer().Serialize(terminal["response"]);
                }
                finally { _gate.Release(); }
            }
            catch (Exception ex)
            {
                TalliarkLog.Trace("Reconcile review failed: " + ex.Message);
                return Serializer().Serialize(new Dictionary<string, object>
                {
                    ["type"] = "reconcile-review-response", ["version"] = 1,
                    ["requestId"] = requestId, ["pdfId"] = pdfId,
                    ["status"] = "error", ["error"] = ex.Message,
                });
            }
        }

        private string RunWorker(string jobId, string json)
        {
            if (_disposed) throw new ObjectDisposedException(nameof(ReconcileReviewService));
            try
            {
                if (_session == null || _session.IsDead)
                {
                    _session?.Dispose();
                    _session = new PythonWorkerSession();
                    _session.Start();
                }
                _session.SendJob(json);
                return _session.ReadResultLine(jobId) ?? throw new IOException("The review worker stopped before returning its result.");
            }
            catch
            {
                _session?.Dispose();
                _session = null;
                throw;
            }
        }

        public void Dispose()
        {
            _disposed = true;
            _sources.Clear();
            _sourceXml = null;
            // Kill is safe from the UI thread and wakes a blocked result read.
            _session?.Kill();
            if (_gate.CurrentCount == 1)
            {
                _session?.Dispose();
                _session = null;
            }
        }
    }
}
