using System;
using System.Collections.Generic;
using System.Linq;
using Talliark.Addin.Modules.CustomXml;
using Talliark.Addin.Modules.CustomXml.Models;
using Excel = Microsoft.Office.Interop.Excel;

namespace Talliark.Addin.Modules.Services
{
    internal sealed class RotatePageService
    {
        /// <summary>
        /// Rotates one page of a PDF 90 degrees, transforms any link rectangles on that
        /// page so they remain visually aligned, and persists both changes to storage.
        /// </summary>
        /// <returns>
        /// A tuple of the updated rotation for that page (always one entry) and the full
        /// updated link list after transforming affected rectangles.
        /// </returns>
        internal (Dictionary<int, int> newRotations, IList<LinkedRectangle> allRects) RotatePage(
            string pdfId, int pageIndex, string direction, Excel.Workbook workbook)
        {
            if (string.IsNullOrWhiteSpace(pdfId))
                throw new ArgumentException("pdfId must be non-empty.", nameof(pdfId));
            if (workbook == null)
                throw new ArgumentNullException(nameof(workbook));

            WorkbookProtectionGuard.ThrowIfStructureProtected(workbook);

            // ── 1. Update page rotation in content XML ────────────────────────
            var store = new TalliarkCustomXmlPartStore(workbook);
            TalliarkContent content = store.LoadContent();

            PdfMetadata pdfMeta = content.Pdfs.FirstOrDefault(
                p => string.Equals(p.Id, pdfId, StringComparison.Ordinal));

            if (pdfMeta == null)
                throw new InvalidOperationException($"PDF '{pdfId}' not found in workbook.");

            int currentRotation = 0;
            if (pdfMeta.PageRotations != null && pdfMeta.PageRotations.TryGetValue(pageIndex, out int stored))
                currentRotation = stored;

            int delta = string.Equals(direction, "cw", StringComparison.OrdinalIgnoreCase) ? 90 : -90;
            int newRotation = ((currentRotation + delta) % 360 + 360) % 360;

            if (pdfMeta.PageRotations == null)
                pdfMeta.PageRotations = new Dictionary<int, int>();

            if (newRotation == 0)
                pdfMeta.PageRotations.Remove(pageIndex);
            else
                pdfMeta.PageRotations[pageIndex] = newRotation;

            store.SaveContent(new TalliarkContent(content.Version, content.Folders, content.Pdfs));

            // ── 2. Transform link rectangles on the rotated page ─────────────
            IList<LinkedRectangle> allLinks = TransformLinks(
                pdfId,
                new Dictionary<int, int> { [pageIndex] = delta / 90 },
                workbook);

            var resultRotations = new Dictionary<int, int> { [pageIndex] = newRotation };
            return (resultRotations, allLinks);
        }

        /// <summary>
        /// Applies the same clockwise delta that the worker already persisted into the
        /// embedded PDF's intrinsic page rotation to workbook-owned link rectangles.
        /// Manual viewer rotations remain a separate metadata layer.
        /// </summary>
        internal bool ApplyOcrRotationCorrections(
            string pdfId,
            IDictionary<int, int> appliedCorrections,
            Excel.Workbook workbook)
        {
            if (appliedCorrections == null || appliedCorrections.Count == 0) return false;
            if (string.IsNullOrWhiteSpace(pdfId))
                throw new ArgumentException("pdfId must be non-empty.", nameof(pdfId));
            if (workbook == null)
                throw new ArgumentNullException(nameof(workbook));

            WorkbookProtectionGuard.ThrowIfStructureProtected(workbook);

            var store = new TalliarkCustomXmlPartStore(workbook);
            if (!store.TryGetMetadata(pdfId, out PdfMetadata _))
                throw new InvalidOperationException($"PDF '{pdfId}' not found in workbook.");

            var deltaTurnsByPage = new Dictionary<int, int>();
            foreach (KeyValuePair<int, int> correction in appliedCorrections)
            {
                if (correction.Key < 0 ||
                    (correction.Value != 90 && correction.Value != 180 && correction.Value != 270))
                    throw new InvalidOperationException(
                        "OCR returned an invalid page rotation correction.");

                deltaTurnsByPage[correction.Key] = correction.Value / 90;
            }

            TransformLinks(pdfId, deltaTurnsByPage, workbook);
            return true;
        }

        private static IList<LinkedRectangle> TransformLinks(
            string pdfId,
            IDictionary<int, int> deltaTurnsByPage,
            Excel.Workbook workbook)
        {
            var session = Globals.ThisAddIn.GetStorageSession(workbook);
            List<LinkedRectangle> allLinks = session.GetLinks().ToList();

            bool anyChanged = false;
            for (int i = 0; i < allLinks.Count; i++)
            {
                LinkedRectangle link = allLinks[i];
                if (!string.Equals(link.PdfId, pdfId, StringComparison.Ordinal)
                    || !deltaTurnsByPage.TryGetValue(
                        link.Rectangle.PageIndex, out int deltaTurns))
                    continue;

                PdfRectangle transformed = TransformRect(link.Rectangle, deltaTurns);
                allLinks[i] = new LinkedRectangle(link.Id, link.PdfId, link.LinkedCell, transformed)
                {
                    LinkType = link.LinkType,
                    SourceText = link.SourceText,
                    TableGrid = link.TableGrid,
                };
                anyChanged = true;
            }

            if (anyChanged)
                session.SetLinks(allLinks);

            return allLinks;
        }

        /// <summary>
        /// Transforms a normalized rectangle by <paramref name="steps"/> 90° clockwise turns.
        /// Steps: +1 = 90° CW, -1 = 90° CCW (= 270° CW), +2 = 180°.
        /// </summary>
        private static PdfRectangle TransformRect(PdfRectangle rect, int steps)
        {
            double x = rect.X, y = rect.Y, w = rect.Width, h = rect.Height;

            // Normalise steps to 0–3
            int turns = ((steps % 4) + 4) % 4;

            for (int i = 0; i < turns; i++)
            {
                // 90° CW: (x, y, w, h) → (1-y-h, x, h, w)
                double nx = 1.0 - y - h;
                double ny = x;
                double nw = h;
                double nh = w;
                x = nx; y = ny; w = nw; h = nh;
            }

            return new PdfRectangle(rect.PageIndex, x, y, w, h, rect.CoordinateSpace);
        }
    }
}
