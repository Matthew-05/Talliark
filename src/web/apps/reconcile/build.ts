import { buildWebApp } from "../../build-app.ts";

await buildWebApp({
  appUrl: import.meta.url,
  title: "Talliark — Reconcile",
  format: "iife",
  completionMessage: "[Talliark] reconcile build complete",
  production: process.argv.includes("--prod"),
});
