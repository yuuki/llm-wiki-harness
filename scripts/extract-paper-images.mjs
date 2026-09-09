#!/usr/bin/env node
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

import { Canvas, DOMMatrix, ImageData, Path2D } from "@napi-rs/canvas";
import { OPS, getDocument } from "pdfjs-dist/legacy/build/pdf.mjs";

globalThis.DOMMatrix ||= DOMMatrix;
globalThis.ImageData ||= ImageData;
globalThis.Path2D ||= Path2D;

const require = createRequire(import.meta.url);
const pdfjsDistRoot = path.dirname(require.resolve("pdfjs-dist/package.json"));

function usage() {
  console.error("usage: extract-paper-images.mjs <pdf-path> <images-dir>");
  process.exit(1);
}

function ensurePngName(prefix, pageNum, seq) {
  return `${prefix}-${String(pageNum).padStart(3, "0")}-${String(seq).padStart(3, "0")}.png`;
}

function writeJson(file, value) {
  fs.writeFileSync(file, `${JSON.stringify(value, null, 2)}\n`);
}

function bytesFromImageData(data) {
  if (data instanceof Uint8ClampedArray) {
    return data;
  }
  if (ArrayBuffer.isView(data)) {
    return new Uint8ClampedArray(data.buffer, data.byteOffset, data.byteLength);
  }
  if (data instanceof ArrayBuffer) {
    return new Uint8ClampedArray(data);
  }
  return null;
}

function toRgba(image) {
  const source = bytesFromImageData(image.data);
  if (!source) {
    return null;
  }

  const pixelCount = image.width * image.height;
  if (source.length === pixelCount * 4) {
    return source;
  }

  const rgba = new Uint8ClampedArray(pixelCount * 4);
  if (source.length === pixelCount * 3) {
    for (let i = 0, j = 0; i < source.length; i += 3, j += 4) {
      rgba[j] = source[i];
      rgba[j + 1] = source[i + 1];
      rgba[j + 2] = source[i + 2];
      rgba[j + 3] = 255;
    }
    return rgba;
  }

  if (source.length === pixelCount) {
    for (let i = 0, j = 0; i < source.length; i += 1, j += 4) {
      rgba[j] = source[i];
      rgba[j + 1] = source[i];
      rgba[j + 2] = source[i];
      rgba[j + 3] = 255;
    }
    return rgba;
  }

  return null;
}

function imageDataToPngBuffer(image) {
  if (!image || typeof image.width !== "number" || typeof image.height !== "number") {
    return null;
  }
  if (!image.data) {
    return null;
  }

  const rgba = toRgba(image);
  if (!rgba) {
    return null;
  }

  const canvas = new Canvas(image.width, image.height);
  const context = canvas.getContext("2d");
  context.putImageData(new ImageData(rgba, image.width, image.height), 0, 0);
  return canvas.toBuffer("image/png");
}

async function getObjectValue(objectStore, objectId) {
  if (!objectStore || !objectId || typeof objectStore.get !== "function") {
    return null;
  }

  try {
    return objectStore.get(objectId);
  } catch {
    return await new Promise(resolve => {
      const timer = setTimeout(() => resolve(null), 3000);
      try {
        objectStore.get(objectId, value => {
          clearTimeout(timer);
          resolve(value);
        });
      } catch {
        clearTimeout(timer);
        resolve(null);
      }
    });
  }
}

async function extractEmbeddedImages(page, pageNum, workImagesDir, entries) {
  const operatorList = await page.getOperatorList();
  let seq = 1;

  for (let i = 0; i < operatorList.fnArray.length; i += 1) {
    const fn = operatorList.fnArray[i];
    const args = operatorList.argsArray[i] || [];
    const isImage =
      fn === OPS.paintImageXObject ||
      fn === OPS.paintImageXObjectRepeat ||
      fn === OPS.paintInlineImageXObject ||
      fn === OPS.paintInlineImageXObjectGroup ||
      fn === OPS.paintJpegXObject;

    if (!isImage) {
      continue;
    }

    const objectId = typeof args[0] === "string" ? args[0] : "";
    const image = objectId ? await getObjectValue(page.objs, objectId) : args[0];
    const buffer = imageDataToPngBuffer(image);
    if (!buffer) {
      console.error(`warn: skipped unsupported embedded image on page ${pageNum}${objectId ? ` (${objectId})` : ""}`);
      continue;
    }

    const file = ensurePngName("image", pageNum, seq);
    fs.writeFileSync(path.join(workImagesDir, file), buffer);
    entries.push({
      file,
      page: pageNum,
      kind: "embedded",
      width: image.width,
      height: image.height,
      object_id: objectId,
    });
    seq += 1;
  }
}

async function renderPage(page, pageNum, workImagesDir, entries, canvasFactory) {
  const viewport = page.getViewport({ scale: 2.0 });
  const canvasAndContext = canvasFactory
    ? canvasFactory.create(viewport.width, viewport.height)
    : null;
  const canvas = canvasAndContext?.canvas;
  const context = canvasAndContext?.context;

  if (!canvas || !context) {
    throw new Error("pdf.js canvasFactory is unavailable");
  }

  await page.render({ canvasContext: context, viewport }).promise;

  const file = `page-${String(pageNum).padStart(3, "0")}.png`;
  fs.writeFileSync(path.join(workImagesDir, file), canvas.toBuffer("image/png"));
  entries.push({
    file,
    page: pageNum,
    kind: "page-render",
    width: Math.floor(viewport.width),
    height: Math.floor(viewport.height),
    object_id: "",
  });

  canvasFactory.destroy(canvasAndContext);
}

async function main() {
  const [pdfPath, imagesDir] = process.argv.slice(2);
  if (!pdfPath || !imagesDir) {
    usage();
  }

  const parentDir = path.dirname(imagesDir);
  const baseName = path.basename(imagesDir);
  fs.mkdirSync(parentDir, { recursive: true });
  const workImagesDir = fs.mkdtempSync(path.join(parentDir, `.${baseName}.tmp-`));
  let replaced = false;

  const data = new Uint8Array(fs.readFileSync(pdfPath));
  const loadingTask = getDocument({
    data,
    disableWorker: true,
    cMapPacked: true,
    cMapUrl: path.join(pdfjsDistRoot, "cmaps/"),
    standardFontDataUrl: path.join(pdfjsDistRoot, "standard_fonts/"),
  });
  const pdfDocument = await loadingTask.promise;
  const entries = [];

  try {
    for (let pageNum = 1; pageNum <= pdfDocument.numPages; pageNum += 1) {
      const page = await pdfDocument.getPage(pageNum);
      await renderPage(page, pageNum, workImagesDir, entries, pdfDocument.canvasFactory);
      await extractEmbeddedImages(page, pageNum, workImagesDir, entries);
      page.cleanup();
    }

    const workManifest = path.join(workImagesDir, "images.json");
    writeJson(workManifest, {
      pdf: pdfPath,
      images_dir: imagesDir,
      pages: pdfDocument.numPages,
      images_count: entries.length,
      images: entries,
    });

    fs.rmSync(imagesDir, { recursive: true, force: true });
    fs.renameSync(workImagesDir, imagesDir);
    replaced = true;
  } finally {
    await pdfDocument.destroy();
    if (!replaced) {
      fs.rmSync(workImagesDir, { recursive: true, force: true });
    }
  }

  const manifest = path.join(imagesDir, "images.json");
  console.log(`images_dir=${imagesDir}`);
  console.log(`images_count=${entries.length}`);
  console.log(`image_manifest=${manifest}`);
}

main().catch(error => {
  console.error(`ERROR: ${error.message}`);
  process.exit(1);
});
