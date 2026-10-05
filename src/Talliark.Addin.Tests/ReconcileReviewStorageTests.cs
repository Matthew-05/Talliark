using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Web.Script.Serialization;
using System.Xml;
using System.Xml.Schema;
using System.Diagnostics;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using Talliark.Addin.Modules.CustomXml.Models;
using Talliark.Addin.Modules.CustomXml.Serialization;

internal static class ReconcileReviewStorageTests
{
    private static int checks;
    private static void Check(bool condition, string message)
    {
        checks++;
        if (!condition) throw new Exception(message);
    }
    private static void Reject(string xml)
    {
        bool rejected = false;
        try { TalliarkReconcileReviewSerializer.FromXml(xml); }
        catch (InvalidOperationException) { rejected = true; }
        Check(rejected, "Unsupported or corrupt storage was not rejected");
    }
    [STAThread]
    public static void Main(string[] args)
    {
        string root = args[0];
        var reviews = new Dictionary<string, string> { ["doc-b"] = "base64-b", ["doc-a"] = "base64-a" };
        string xml = TalliarkReconcileReviewSerializer.ToXml(reviews);
        var restored = TalliarkReconcileReviewSerializer.FromXml(xml);
        Check(restored.Count == 2 && restored["doc-a"] == "base64-a", "Multi-document roundtrip failed");
        restored["doc-a"] = "updated";
        Check(TalliarkReconcileReviewSerializer.FromXml(TalliarkReconcileReviewSerializer.ToXml(restored))["doc-b"] == "base64-b", "Updating a document lost another review");
        Check(TalliarkReconcileReviewSerializer.FromXml(null).Count == 0, "Old workbook cannot initialize review");
        Reject(xml.Replace("version=\"1\"", "version=\"2\""));
        Reject(xml.Replace("urn:talliark:schemas:storage:1:reconcile-review", "urn:unknown"));
        Reject(xml.Replace("doc-b", "doc-a"));
        Reject(xml.Replace("base64-a", ""));
        var schema = new XmlSchemaSet();
        schema.Add(TalliarkReconcileReviewSerializer.NamespaceUri, Path.Combine(root, "contracts/talliark-storage-reconcile-review-v1.xsd"));
        var settings = new XmlReaderSettings { ValidationType = ValidationType.Schema, Schemas = schema };
        settings.ValidationEventHandler += (sender, e) => { throw new Exception(e.Message); };
        using (var reader = XmlReader.Create(Path.Combine(root, "contracts/talliark-storage-reconcile-review-v1.sample.xml"), settings))
            while (reader.Read()) { }
        Check(true, "XML sample validation");
        var serializer = new JavaScriptSerializer { MaxJsonLength = int.MaxValue };
        ReviewWorkspace sample = serializer.Deserialize<ReviewWorkspace>(File.ReadAllText(Path.Combine(root, "contracts/reconcile-review-v1.sample.json")));
        Check(sample.version == 1 && sample.equations.Count == 2 && sample.equations[0].terms.Count == 2, "Generated C# review binding cannot read sample");
        if (args.Length > 1 && args[1] == "--excel") ExcelRoundtrip(root);
        Console.WriteLine(checks + " C# storage and contract checks passed.");
    }

    private static void ExcelRoundtrip(string root)
    {
        // A separate hidden automation instance and a fresh synthetic workbook;
        // no running user workbook is selected or changed.
        Type excelType = Type.GetTypeFromProgID("Excel.Application", true);
        Assembly addin = Assembly.LoadFrom(Path.Combine(root, "src/Talliark.Addin/bin/Debug/Talliark.Addin.dll"));
        Type storeType = addin.GetType("Talliark.Addin.Modules.CustomXml.TalliarkCustomXmlPartStore", true);
        string path = Path.Combine(root, "output/reconcile-review-storage-" + Guid.NewGuid().ToString("N") + ".xlsx");
        string sampleXml = File.ReadAllText(Path.Combine(root, "contracts/talliark-storage-reconcile-review-v1.sample.xml"));
        string payload = TalliarkReconcileReviewSerializer.FromXml(sampleXml)["doc"];
        dynamic app = null;
        dynamic books = null;
        dynamic book = null;
        object reviewService = null;
        SynchronizationContext previousContext = SynchronizationContext.Current;
        try
        {
            SynchronizationContext.SetSynchronizationContext(new WindowsFormsSynchronizationContext());
            app = Activator.CreateInstance(excelType);
            app.Visible = false; app.DisplayAlerts = false; app.AutomationSecurity = 3;
            books = app.Workbooks; book = books.Add();
            object store = Activator.CreateInstance(storeType, new object[] { (object)book });
            MethodInfo load = storeType.GetMethod("LoadReconcileReview");
            MethodInfo save = storeType.GetMethod("SaveReconcileReview");
            object legacy = storeType.GetMethod("LoadReconcileWorkspace").Invoke(store, null);
            legacy.GetType().GetProperty("ProjectName").SetValue(legacy, "Review storage test", null);
            storeType.GetMethod("SaveReconcileWorkspace").Invoke(store, new[] { legacy });
            Check(load.Invoke(store, new object[] { "doc" }) == null, "Fresh workbook has unexpected review");
            save.Invoke(store, new object[] { "doc", payload });
            save.Invoke(store, new object[] { "other", payload });
            Check((string)load.Invoke(store, new object[] { "doc" }) == payload, "Workbook review write failed");
            book.SaveAs(path, 51);
            Type comparisonType = addin.GetType("Talliark.Addin.Modules.CustomXml.Models.ReconcileComparison", true);
            object comparison = Activator.CreateInstance(comparisonType);
            foreach (var item in new Dictionary<string, object>
            {
                ["Id"] = "service-doc", ["Role"] = "comparison-1", ["DisplayName"] = "Synthetic statement",
                ["PdfBase64"] = "c3ludGhldGlj", ["ImportedAt"] = DateTime.UtcNow,
                ["ReconcileBase64"] = File.ReadAllText(Path.Combine(root, "output/reconcile-service-scan.txt")),
            }) comparisonType.GetProperty(item.Key).SetValue(comparison, item.Value, null);
            ((System.Collections.IList)legacy.GetType().GetProperty("Comparisons").GetValue(legacy, null)).Add(comparison);
            storeType.GetMethod("SaveReconcileWorkspace").Invoke(store, new[] { legacy });
            Type serviceType = addin.GetType("Talliark.Addin.Modules.Services.ReconcileReviewService", true);
            reviewService = Activator.CreateInstance(serviceType, true);
            var request = new ReviewRequest { type = "reconcile-review-request", version = 1, requestId = "load-service", pdfId = "service-doc",
                mode = "load", scanId = "", expectedRevision = 0, operations = new List<ReviewOperation>() };
            ReviewResponse loaded = ProcessReview(reviewService, (object)book, request);
            Check(loaded.status == "loaded", "Service load failed: " + loaded.error);
            request.mode = "commit"; request.scanId = loaded.workspace.scanId; request.expectedRevision = loaded.workspace.revision;
            request.operations.Add(new ReviewOperation { kind = "decision", equationIds = new List<string> { loaded.workspace.equations[0].id }, decision = "accepted" });
            var timer = Stopwatch.StartNew();
            ReviewResponse committed = ProcessReview(reviewService, (object)book, request);
            Console.WriteLine("Warm synthetic review update: " + timer.ElapsedMilliseconds + " ms");
            Check(committed.status == "updated" && committed.workspace.equations[0].decision == "accepted", "Automatic XML commit failed: " + committed.error);
            string recorded = (string)load.Invoke(store, new object[] { "service-doc" });
            Check(!string.IsNullOrEmpty(recorded) && !book.Saved, "Review was not recorded in the dirty open workbook");
            comparisonType.GetProperty("ReconcileBase64").SetValue(comparison, File.ReadAllText(Path.Combine(root, "output/reconcile-service-changed-scan.txt")), null);
            storeType.GetMethod("SaveReconcileWorkspace").Invoke(store, new[] { legacy });
            request.expectedRevision = committed.workspace.revision;
            ReviewResponse conflict = ProcessReview(reviewService, (object)book, request);
            Check(conflict.status == "error" && (string)load.Invoke(store, new object[] { "service-doc" }) == recorded, "Cached source allowed a stale decision to overwrite review");
            request.mode = "load"; request.operations.Clear();
            ReviewResponse changed = ProcessReview(reviewService, (object)book, request);
            Check(changed.status == "loaded" && changed.workspace.scanId != loaded.workspace.scanId && changed.workspace.equations[0].decision == "unreviewed", "Source cache did not invalidate on XML change");
            recorded = (string)load.Invoke(store, new object[] { "service-doc" });
            book.Worksheets[1].Cells[1, 1].Value2 = "Unrelated workbook edit";
            request.mode = "save-workbook";
            request.operations.Add(new ReviewOperation { kind = "page", pageIndex = 0, reviewed = true });
            ReviewResponse invalidSave = ProcessReview(reviewService, (object)book, request);
            Check(invalidSave.status == "error" && !book.Saved, "Workbook Save incorrectly applied review operations");
            request.operations.Clear();
            ReviewResponse blockedSave = ProcessReview(reviewService, (object)book, request, true);
            Check(blockedSave.status == "error" && !book.Saved, "Workbook was saved during a scan");
            ReviewResponse workbookSaved = ProcessReview(reviewService, (object)book, request);
            Check(workbookSaved.status == "workbook-saved" && book.Saved, "Excel Save proxy failed: " + workbookSaved.error);
            Check((string)load.Invoke(store, new object[] { "service-doc" }) == recorded, "Workbook Save changed the review payload");
            ((IDisposable)reviewService).Dispose(); reviewService = null;
            book.Close(false); Marshal.FinalReleaseComObject((object)book); book = null;
            book = books.Open(path, ReadOnly: false);
            store = Activator.CreateInstance(storeType, new object[] { (object)book });
            Check((string)load.Invoke(store, new object[] { "doc" }) == payload, "Review did not survive Excel save/reopen");
            Check((string)load.Invoke(store, new object[] { "other" }) == payload, "Second document did not survive Excel save/reopen");
            Check((string)load.Invoke(store, new object[] { "service-doc" }) == recorded, "Automatically recorded review did not survive the Save workbook proxy");
            Check((string)book.Worksheets[1].Cells[1, 1].Value2 == "Unrelated workbook edit", "Save workbook did not save other Excel edits");
            object legacyAfter = storeType.GetMethod("LoadReconcileWorkspace").Invoke(store, null);
            Check((string)legacyAfter.GetType().GetProperty("ProjectName").GetValue(legacyAfter, null) == "Review storage test", "Review overwrote legacy workspace");
            Console.WriteLine("Excel save/reopen verified: " + path);
        }
        finally
        {
            if (reviewService != null) ((IDisposable)reviewService).Dispose();
            if (book != null) { book.Close(false); Marshal.FinalReleaseComObject((object)book); }
            if (books != null) Marshal.FinalReleaseComObject((object)books);
            if (app != null) { app.Quit(); Marshal.FinalReleaseComObject((object)app); }
            SynchronizationContext.SetSynchronizationContext(previousContext);
        }
    }

    private static ReviewResponse ProcessReview(object service, object book, ReviewRequest request, bool scanning = false)
    {
        request.requestId = Guid.NewGuid().ToString();
        var serializer = new JavaScriptSerializer { MaxJsonLength = int.MaxValue };
        object wire = serializer.DeserializeObject(serializer.Serialize(request));
        RemoveNulls(wire); // Match web JSON: absent optional fields are not null.
        var task = (Task<string>)service.GetType().GetMethod("ProcessAsync").Invoke(service, new object[] { book, serializer.Serialize(wire), scanning });
        var timer = Stopwatch.StartNew();
        while (!task.IsCompleted)
        {
            if (timer.Elapsed > TimeSpan.FromSeconds(30)) throw new TimeoutException("Review service test timed out");
            Application.DoEvents(); Thread.Sleep(1);
        }
        return serializer.Deserialize<ReviewResponse>(task.GetAwaiter().GetResult());
    }

    private static void RemoveNulls(object value)
    {
        if (value is Dictionary<string, object> fields)
            foreach (string key in new List<string>(fields.Keys))
                if (fields[key] == null) fields.Remove(key); else RemoveNulls(fields[key]);
        if (value is object[] items) foreach (object item in items) RemoveNulls(item);
    }
}
