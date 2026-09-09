using System.Collections.Generic;
using System.Web.Script.Serialization;
using Talliark.Addin.Modules.CustomXml.Models;
using Talliark.Addin.Modules.Services;

namespace Talliark.Addin.Modules.WebView
{
    internal static class ReconcileMessageSerializer
    {
        public static string BuildDataLoaded(
            IList<ReconcileDocumentInfo> documents,
            IList<PdfMetadata> sources,
            string scanningPdfId,
            string projectName)
        {
            var root = new Dictionary<string, object>
            {
                ["type"] = "reconcile-data-loaded",
                ["documents"] = BuildDocuments(documents),
            };
            if (!string.IsNullOrWhiteSpace(projectName)) root["projectName"] = projectName;
            if (sources != null) root["sources"] = BuildSources(sources);
            if (!string.IsNullOrEmpty(scanningPdfId)) root["scanning"] = scanningPdfId;
            return Serialize(root);
        }

        public static string BuildScanStatus(
            string pdfId, string status, OcrStatusDetail detail = null)
        {
            var root = new Dictionary<string, object>
            {
                ["type"] = "reconcile-scan-status",
                ["pdfId"] = pdfId ?? string.Empty,
                ["status"] = status ?? string.Empty,
            };
            if (detail != null)
            {
                if (!string.IsNullOrEmpty(detail.Message)) root["message"] = detail.Message;
                if (!string.IsNullOrEmpty(detail.Stage)) root["stage"] = detail.Stage;
                if (detail.Current.HasValue && detail.Total.HasValue && detail.Total.Value > 0)
                {
                    root["current"] = detail.Current.Value;
                    root["total"] = detail.Total.Value;
                    if (!string.IsNullOrEmpty(detail.Unit)) root["unit"] = detail.Unit;
                }
            }
            return Serialize(root);
        }

        public static string BuildResultLoaded(ReconcileStoredResult result)
        {
            var root = new Dictionary<string, object>
            {
                ["type"] = "reconcile-result-loaded",
                ["pdfId"] = result.PdfId ?? string.Empty,
                ["staleness"] = result.Staleness ?? "none",
            };
            if (!string.IsNullOrEmpty(result.PdfBase64))
                root["pdfBase64"] = result.PdfBase64;
            if (result.PageRotations != null && result.PageRotations.Count > 0)
                root["pageRotations"] = result.PageRotations;
            if (!string.IsNullOrEmpty(result.ReconcileBase64))
                root["reconcileBase64"] = result.ReconcileBase64;
            return Serialize(root);
        }

        private static object BuildDocuments(IList<ReconcileDocumentInfo> documents)
        {
            var output = new List<Dictionary<string, object>>();
            foreach (ReconcileDocumentInfo document in documents)
            {
                var item = new Dictionary<string, object>
                {
                    ["id"] = document.Id,
                    ["versionId"] = document.VersionId,
                    ["role"] = document.Role,
                    ["name"] = document.Name,
                    ["staleness"] = document.Staleness,
                };
                if (!string.IsNullOrEmpty(document.FolderId)) item["folderId"] = document.FolderId;
                if (document.PageCount.HasValue) item["pageCount"] = document.PageCount.Value;
                if (!string.IsNullOrEmpty(document.ScannedAt)) item["scannedAt"] = document.ScannedAt;
                if (document.Summary != null)
                {
                    item["summary"] = new Dictionary<string, object>
                    {
                        ["tablesExamined"] = document.Summary.TablesExamined,
                        ["totalsNominated"] = document.Summary.TotalsNominated,
                        ["confirmed"] = document.Summary.Confirmed,
                        ["breaks"] = document.Summary.Breaks,
                        ["unresolved"] = document.Summary.Unresolved,
                    };
                }
                output.Add(item);
            }
            return output;
        }

        private static object BuildSources(IList<PdfMetadata> sources)
        {
            var output = new List<Dictionary<string, object>>();
            foreach (PdfMetadata source in sources)
                output.Add(new Dictionary<string, object> { ["id"] = source.Id, ["name"] = source.Name });
            return output;
        }

        private static string Serialize(object value) =>
            new JavaScriptSerializer { MaxJsonLength = int.MaxValue }.Serialize(value);
    }
}
