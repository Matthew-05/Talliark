using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Web.Script.Serialization;
using System.Xml;
using System.Xml.Schema;
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
        try
        {
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
            book.SaveAs(path, 51); book.Close(false); Marshal.FinalReleaseComObject((object)book); book = null;
            book = books.Open(path, ReadOnly: false);
            store = Activator.CreateInstance(storeType, new object[] { (object)book });
            Check((string)load.Invoke(store, new object[] { "doc" }) == payload, "Review did not survive Excel save/reopen");
            Check((string)load.Invoke(store, new object[] { "other" }) == payload, "Second document did not survive Excel save/reopen");
            object legacyAfter = storeType.GetMethod("LoadReconcileWorkspace").Invoke(store, null);
            Check((string)legacyAfter.GetType().GetProperty("ProjectName").GetValue(legacyAfter, null) == "Review storage test", "Review overwrote legacy workspace");
            Console.WriteLine("Excel save/reopen verified: " + path);
        }
        finally
        {
            if (book != null) { book.Close(false); Marshal.FinalReleaseComObject((object)book); }
            if (books != null) Marshal.FinalReleaseComObject((object)books);
            if (app != null) { app.Quit(); Marshal.FinalReleaseComObject((object)app); }
        }
    }
}
