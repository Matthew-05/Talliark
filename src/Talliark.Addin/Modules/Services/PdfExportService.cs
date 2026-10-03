using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Threading.Tasks;
using Talliark.Addin.Modules.CustomXml;
using Talliark.Addin.Modules.CustomXml.Models;
using Talliark.Addin.Modules.Infrastructure;
using Excel = Microsoft.Office.Interop.Excel;

namespace Talliark.Addin.Modules.Services
{
    /// <summary>
    /// Exports an embedded PDF, materializing sidecar OCR geometry as an invisible
    /// searchable text layer without changing the workbook's stored PDF.
    /// </summary>
    internal sealed class PdfExportService
    {
        /// <summary>
        /// Loads the workbook-owned data while the caller is still on Excel's UI thread.
        /// The returned snapshot is safe to hand to <see cref="ExportAsync"/>.
        /// </summary>
        public PdfExportSource LoadSource(Excel.Workbook workbook, string id)
        {
            if (workbook == null) throw new ArgumentNullException(nameof(workbook));
            if (string.IsNullOrWhiteSpace(id))
                throw new ArgumentException("PDF id must be non-empty.", nameof(id));

            var store = new TalliarkCustomXmlPartStore(workbook);
            if (!store.TryGetPdf(id, out PdfDocument pdf) || pdf == null)
                throw new InvalidOperationException("PDF not found: " + id);
            if (string.IsNullOrWhiteSpace(pdf.Base64))
                throw new InvalidOperationException("The embedded PDF has no stored bytes.");

            return new PdfExportSource(
                SuggestedFileName(pdf.Name),
                pdf.Base64,
                pdf.GeometryBase64 ?? string.Empty);
        }

        public Task<PdfExportResult> ExportAsync(PdfExportSource source, string outputPath)
        {
            if (source == null) throw new ArgumentNullException(nameof(source));
            if (string.IsNullOrWhiteSpace(outputPath))
                throw new ArgumentException("Output path must be non-empty.", nameof(outputPath));

            return Task.Run(() =>
            {
                bool ocrTextLayerAdded = !string.IsNullOrWhiteSpace(source.GeometryBase64);
                byte[] output = !ocrTextLayerAdded
                    ? Convert.FromBase64String(source.PdfBase64)
                    : BuildSearchablePdf(source.PdfBase64, source.GeometryBase64);
                File.WriteAllBytes(outputPath, output);
                return new PdfExportResult(outputPath, output.LongLength, ocrTextLayerAdded);
            });
        }

        private static byte[] BuildSearchablePdf(string pdfBase64, string geometryBase64)
        {
            if (!PythonWorkerSession.IsAvailable)
                throw new FileNotFoundException(
                    PythonWorkerSession.NotBuiltMessage,
                    PythonWorkerSession.WorkerExePath);

            string jobId = Guid.NewGuid().ToString("N");
            var json = new StringBuilder(
                pdfBase64.Length + geometryBase64.Length + 128);
            json.Append("{\"job_id\":");
            PythonWorkerSession.AppendJsonString(json, jobId);
            json.Append(",\"command\":\"export-pdf\",\"pdf_base64\":");
            PythonWorkerSession.AppendJsonString(json, pdfBase64);
            json.Append(",\"geometry_base64\":");
            PythonWorkerSession.AppendJsonString(json, geometryBase64);
            json.Append('}');

            using (var session = new PythonWorkerSession())
            {
                session.Start();
                session.SendJob(json.ToString());
                string line = session.ReadResultLine(jobId);
                if (line == null)
                    throw new InvalidOperationException(
                        "The PDF export worker closed unexpectedly.");

                Dictionary<string, object> result = PythonWorkerSession.Deserialize(line);
                string status = PythonWorkerSession.GetString(result, "status");
                if (!string.Equals(status, "success", StringComparison.Ordinal))
                {
                    string error = PythonWorkerSession.GetString(result, "error");
                    throw new InvalidOperationException(
                        string.IsNullOrWhiteSpace(error)
                            ? "The PDF export worker returned an unknown error."
                            : error);
                }

                string outputBase64 = PythonWorkerSession.GetString(result, "pdf_base64");
                if (string.IsNullOrWhiteSpace(outputBase64))
                    throw new InvalidOperationException(
                        "The PDF export worker returned no PDF bytes.");
                return Convert.FromBase64String(outputBase64);
            }
        }

        private static string SuggestedFileName(string name)
        {
            string fileName = Path.GetFileName(name ?? string.Empty);
            foreach (char invalid in Path.GetInvalidFileNameChars())
                fileName = fileName.Replace(invalid, '_');
            if (string.IsNullOrWhiteSpace(fileName))
                fileName = "document.pdf";
            else if (!fileName.EndsWith(".pdf", StringComparison.OrdinalIgnoreCase))
                fileName += ".pdf";
            return fileName;
        }
    }

    internal sealed class PdfExportSource
    {
        public PdfExportSource(
            string suggestedFileName,
            string pdfBase64,
            string geometryBase64)
        {
            SuggestedFileName = suggestedFileName;
            PdfBase64 = pdfBase64;
            GeometryBase64 = geometryBase64;
        }

        public string SuggestedFileName { get; }
        public string PdfBase64 { get; }
        public string GeometryBase64 { get; }
    }

    internal sealed class PdfExportResult
    {
        public PdfExportResult(string outputPath, long outputBytes, bool ocrTextLayerAdded)
        {
            OutputPath = outputPath;
            OutputBytes = outputBytes;
            OcrTextLayerAdded = ocrTextLayerAdded;
        }

        public string OutputPath { get; }
        public long OutputBytes { get; }
        public bool OcrTextLayerAdded { get; }
    }
}
