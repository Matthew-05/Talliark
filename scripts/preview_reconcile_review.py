"""Local browser harness with synthetic PDF evidence and the real review engine.

Run using the bundled Python host after building the Reconcile web app. It binds
only to localhost and writes review state under output/, never into a workbook.
This is a developer preview, not an alternative production host.
"""
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import base64
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src/python"), str(ROOT / "src/python/tests")]
import pymupdf as fitz
from engines.binary_codec import json_to_base64
from engines.reconcile.review import handle_job
from test_reconcile_review import sample_scan

MODEL = sample_scan()
MODEL["source"].update(pageCount=3, geometryFingerprint="synthetic-review-queue-v1")
for i, value in enumerate(["3", "4", "7"]):
    MODEL["tables"][0]["cells"].append({"id": f"segment-{i}", "bounds": {"x": .45 + i * .15, "y": .65, "width": .08, "height": .025},
        "text": value, "normalizedValue": value, "rowLabel": "Segments", "columnIndex": i})
MODEL["tables"][0]["totals"].append({"id": "segments", "cellId": "segment-2", "axis": "cross", "resolution": {"addendCellIds": ["segment-0", "segment-1"]}})
MODEL["tables"][0]["headerLabels"] = [{"columnIndex": i, "text": label} for i, label in enumerate(["Domestic", "Overseas", "Total"])]
cells, totals = [], []
for row, (label, values) in enumerate([("Revenue", ["3", "4", "8"]), ("Expenses", ["1", "2", "3"]), ("Net result", ["2", "2", "5"])]):
    for column, value in enumerate(values):
        cells.append({"id": f"r{row}-c{column}", "bounds": {"x": .45 + column * .15, "y": .14 + row * .1, "width": .08, "height": .025},
            "text": value, "normalizedValue": value, "rowLabel": label, "columnIndex": column})
    totals.append({"id": f"row-{row}", "cellId": f"r{row}-c2", "axis": "cross", "resolution": {"addendCellIds": [f"r{row}-c0", f"r{row}-c1"]}})
totals.append({"id": "net-foot", "cellId": "r2-c2", "axis": "vertical", "resolution": {"addendCellIds": ["r0-c2", "r1-c2"], "negatedAddendCellIds": ["r1-c2"]}})
cells.append({"id": "unresolved", "bounds": {"x": .75, "y": .44, "width": .08, "height": .025}, "text": "?", "rowLabel": "Unreadable total", "columnIndex": 2})
totals.append({"id": "unresolved", "cellId": "unresolved", "axis": "vertical", "resolution": {"addendCellIds": []}})
MODEL["tables"].append({"id": "page-two", "pageIndex": 1, "bounds": {"x": .1, "y": .1, "width": .75, "height": .4},
    "rowCount": 4, "cells": cells, "headerLabels": MODEL["tables"][0]["headerLabels"], "totals": totals})
MODEL.update(summary={"tablesExamined": 2, "totalsNominated": 8, "confirmed": 5, "breaks": 2, "unresolved": 1}, findings=[])
PDF = fitz.open()
for page_index in range(3):
    page = PDF.new_page(width=600, height=800)
    page.insert_text((60, 50), "Synthetic statement - review workflow preview", fontsize=14)
    if page_index < 2:
        for cell in MODEL["tables"][page_index]["cells"]:
            bounds = cell["bounds"]
            page.insert_text((60, bounds["y"] * 800 + 13), cell["rowLabel"], fontsize=11)
            page.insert_text((bounds["x"] * 600, bounds["y"] * 800 + 13), cell["text"], fontsize=11)
    else:
        page.insert_text((60, 100), "Page without detected totals. Add a manual sum here.")
        page.insert_text((300, 160), "3     4     7")
PDF_BASE64 = base64.b64encode(PDF.tobytes()).decode()
MODEL_BASE64 = json_to_base64(MODEL)
STATE = ROOT / "output/reconcile-review-queue-preview.txt"
STATE.parent.mkdir(exist_ok=True)

BRIDGE = """<script>
const bridge = new EventTarget();
window.chrome = { webview: bridge };
const deliver = data => bridge.dispatchEvent(new MessageEvent('message', { data }));
bridge.postMessage = async raw => {
  const msg = JSON.parse(raw);
  if (msg.type === 'reconcile-ready') {
    deliver(await (await fetch('/data')).json());
  } else if (msg.type === 'request-reconcile-result') {
    deliver(await (await fetch('/result')).json());
  } else if (msg.type === 'reconcile-review-request') {
    try { deliver(await (await fetch('/review', { method: 'POST', body: raw })).json()); }
    catch (error) { deliver({ type: 'reconcile-review-response', version: 1, requestId: msg.requestId, pdfId: msg.pdfId, status: 'error', error: String(error) }); }
  }
};
</script>"""


class Handler(BaseHTTPRequestHandler):
    def reply(self, data, content_type="application/json"):
        payload = json.dumps(data).encode() if content_type == "application/json" else data
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", len(payload))
        self.end_headers(); self.wfile.write(payload)

    def do_GET(self):
        if self.path == "/data":
            return self.reply({"type": "reconcile-data-loaded", "projectName": "Review preview", "documents": [
                {"id": "doc", "role": "primary", "name": "Synthetic statement.pdf", "staleness": "current", "summary": MODEL["summary"]}]})
        if self.path == "/result":
            return self.reply({"type": "reconcile-result-loaded", "pdfId": "doc", "pdfBase64": PDF_BASE64,
                               "reconcileBase64": MODEL_BASE64, "staleness": "current", "pageRotations": {}})
        if self.path == "/":
            html = (ROOT / "src/web/apps/reconcile/dist/index.html").read_text(encoding="utf-8")
            return self.reply(html.replace('<script src="index.js">', BRIDGE + '<script src="index.js">').encode(), "text/html")
        allowed = {"/index.js": "text/javascript", "/index.css": "text/css", "/pdf.worker.min.mjs": "text/javascript"}
        if self.path in allowed:
            return self.reply((ROOT / "src/web/apps/reconcile/dist" / self.path[1:]).read_bytes(), allowed[self.path])
        self.send_error(404)

    def do_POST(self):
        if self.path != "/review": return self.send_error(404)
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        result = handle_job({"job_id": "preview", "command": "reconcile-review", "request": request,
                             "model_base64": MODEL_BASE64, "values_base64": "", "review_base64": STATE.read_text() if STATE.exists() else ""})
        if result["review_base64"]: STATE.write_text(result["review_base64"])
        self.reply(result["response"])


if __name__ == "__main__":
    print("Reconcile review preview: http://127.0.0.1:8765", flush=True)
    HTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
