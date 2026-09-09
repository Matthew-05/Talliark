import { buildWebApp } from "../../build-app.ts";

await buildWebApp({
  appUrl: import.meta.url,
  title: "Talliark — Reconcile",
  format: "iife",
  copyPdfWorker: true,
  inlineSvg: true,
  completionMessage: "[Talliark] reconcile build complete",
  production: process.argv.includes("--prod"),
});
