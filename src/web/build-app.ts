import * as esbuild from "esbuild";
import { copyFileSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

export interface WebAppBuildOptions {
  appUrl: string;
  title: string;
  format: "esm" | "iife";
  completionMessage: string;
  cleanDist?: boolean;
  copyPdfWorker?: boolean;
  inlineSvg?: boolean;
  production?: boolean;
  splitting?: boolean;
}

function buildIndexHtml(title: string, moduleScript: boolean): string {
  const scriptType = moduleScript ? ' type="module"' : "";
  return `<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>${title}</title>
    <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='75' font-size='75' font-weight='bold' fill='%234B5563'>D</text></svg>" />
    <link rel="stylesheet" href="index.css" />
  </head>
  <body>
    <div id="app"></div>
    <script${scriptType} src="index.js"></script>
  </body>
</html>
`;
}

export async function buildWebApp(options: WebAppBuildOptions): Promise<void> {
  const appDirectory = fileURLToPath(new URL(".", options.appUrl));
  const webDirectory = resolve(appDirectory, "../..");
  const distDirectory = resolve(appDirectory, "dist");

  if (options.cleanDist) {
    rmSync(distDirectory, { recursive: true, force: true });
  }
  mkdirSync(distDirectory, { recursive: true });

  if (options.copyPdfWorker) {
    copyFileSync(
      resolve(webDirectory, "node_modules/pdfjs-dist/build/pdf.worker.min.mjs"),
      resolve(distDirectory, "pdf.worker.min.mjs"),
    );
  }

  writeFileSync(
    resolve(distDirectory, "index.html"),
    buildIndexHtml(options.title, options.format === "esm"),
  );

  const sharedOptions: esbuild.BuildOptions = {
    absWorkingDir: appDirectory,
    bundle: true,
    minify: options.production ?? false,
    sourcemap: options.production ? false : "inline",
  };

  await esbuild.build({
    ...sharedOptions,
    entryPoints: ["src/main.ts"],
    ...(options.splitting
      ? {
          outdir: "dist",
          entryNames: "index",
          chunkNames: "chunks/[name]-[hash]",
          splitting: true,
        }
      : { outfile: "dist/index.js" }),
    format: options.format,
    platform: "browser",
  });

  await esbuild.build({
    ...sharedOptions,
    entryPoints: ["src/styles/main.css"],
    outfile: "dist/index.css",
    ...(options.inlineSvg ? { loader: { ".svg": "dataurl" } } : {}),
  });

  console.log(options.completionMessage);
}
