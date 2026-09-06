namespace Talliark.Addin.Modules.Infrastructure
{
    /// <summary>
    /// The steps a job moves through, mirroring the <c>ProgressStage</c> enum in
    /// contracts/python-worker-v1.json and contracts/webview-messages-v1.json.
    /// The contract is the source of the vocabulary; this is the host's copy of it,
    /// and the three must be changed together.
    ///
    /// <see cref="Order"/> is pipeline order, which is what lets the file manager
    /// place a step on a bar. An adaptive run skips steps, so it is a ranking
    /// rather than a sequence to expect in full.
    ///
    /// Queue, Prepare, Transfer and Finalizing are the host's own; the worker
    /// states the rest.
    /// </summary>
    internal static class ProgressStages
    {
        public const string Queue = "queue";
        public const string Prepare = "prepare";
        public const string Convert = "convert";
        public const string Transfer = "transfer";
        public const string Security = "security";
        public const string SourceCheck = "source-check";
        public const string Source = "source";
        public const string Geometry = "geometry";
        public const string Ocr = "ocr";
        public const string Orientation = "orientation";
        public const string AdaptiveOcr = "adaptive-ocr";
        public const string TableRecovery = "table-recovery";
        public const string TableStructure = "table-structure";
        public const string FinancialStructure = "financial-structure";
        public const string Values = "values";

        // The Reconcile scan's two stages. They are emitted only by a job whose
        // analysis flag is set, so an ordinary cache build jumps over them --
        // which is exactly the gap the contract tells every consumer to tolerate.
        public const string ReconcileTables = "reconcile-tables";
        public const string Reconcile = "reconcile";

        public const string ResultTransfer = "result-transfer";
        public const string Finalizing = "finalizing";

        public static readonly string[] Order =
        {
            Queue,
            Prepare,
            Convert,
            Transfer,
            Security,
            SourceCheck,
            Source,
            Geometry,
            Ocr,
            Orientation,
            AdaptiveOcr,
            TableRecovery,
            TableStructure,
            FinancialStructure,
            Values,
            ReconcileTables,
            Reconcile,
            ResultTransfer,
            Finalizing,
        };
    }
}
