import type { CapturedImage } from "./types.js";

const LEAD_MAX_DIMENSION = 640;
const LEAD_MAX_BYTES = 200 * 1024;
const FAVICON_MAX_DIMENSION = 64;
const FAVICON_MAX_BYTES = 32 * 1024;

export async function capturePageImages(
  root: HTMLElement,
  source: Document = document,
): Promise<{ leadImage: CapturedImage | null; favicon: CapturedImage | null }> {
  const [leadImage, favicon] = await Promise.all([
    captureLeadImage(root, source),
    captureFavicon(source),
  ]);
  return { leadImage, favicon };
}

async function captureLeadImage(
  root: HTMLElement,
  source: Document,
): Promise<CapturedImage | null> {
  const socialUrl =
    source.querySelector<HTMLMetaElement>('meta[property="og:image"]')?.content ??
    source.querySelector<HTMLMetaElement>('meta[name="twitter:image"]')?.content;
  const contentImages = [...root.querySelectorAll<HTMLImageElement>("img")].filter(
    (image) =>
      image.complete &&
      image.naturalWidth >= 200 &&
      image.naturalHeight >= 120 &&
      isVisible(image),
  );
  const social = socialUrl
    ? [...source.querySelectorAll<HTMLImageElement>("img")].find((image) => {
        if (!image.complete || !image.naturalWidth || !image.naturalHeight) {
          return false;
        }
        try {
          return new URL(image.currentSrc || image.src, source.baseURI).href ===
            new URL(socialUrl, source.baseURI).href;
        } catch {
          return false;
        }
      })
    : undefined;
  return renderImage(
    social ?? contentImages[0] ?? null,
    LEAD_MAX_DIMENSION,
    LEAD_MAX_BYTES,
    "image/webp",
  );
}

async function captureFavicon(source: Document): Promise<CapturedImage | null> {
  let href = source.querySelector<HTMLLinkElement>(
    'link[rel~="icon"]:not([type="image/svg+xml"])',
  )?.href;
  if (!href) {
    try {
      href = new URL("/favicon.ico", source.baseURI).href;
    } catch {
      return null;
    }
  }
  const image = new Image();
  image.crossOrigin = "anonymous";
  image.src = href;
  try {
    await Promise.race([
      image.decode(),
      new Promise((_, reject) =>
        setTimeout(() => reject(new Error("favicon timed out")), 2_000),
      ),
    ]);
  } catch {
    return null;
  }
  return renderImage(
    image,
    FAVICON_MAX_DIMENSION,
    FAVICON_MAX_BYTES,
    "image/png",
  );
}

async function renderImage(
  image: HTMLImageElement | null,
  maxDimension: number,
  maxBytes: number,
  contentType: "image/png" | "image/webp",
): Promise<CapturedImage | null> {
  if (!image?.naturalWidth || !image.naturalHeight) return null;
  const scale = Math.min(
    1,
    maxDimension / Math.max(image.naturalWidth, image.naturalHeight),
  );
  const width = Math.max(1, Math.round(image.naturalWidth * scale));
  const height = Math.max(1, Math.round(image.naturalHeight * scale));
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d", { alpha: contentType === "image/png" });
  if (!context) return null;
  try {
    context.drawImage(image, 0, 0, width, height);
    const qualities = contentType === "image/webp" ? [0.82, 0.7, 0.58] : [undefined];
    for (const quality of qualities) {
      const blob = await canvasBlob(canvas, contentType, quality);
      if (blob && blob.size <= maxBytes) {
        return {
          bytesBase64: await blobBase64(blob),
          contentType,
          width,
          height,
        };
      }
    }
  } catch {
    // Cross-origin images without CORS taint the canvas. Missing images are a
    // normal capture outcome and never trigger a backend fetch.
  }
  return null;
}

function canvasBlob(
  canvas: HTMLCanvasElement,
  contentType: string,
  quality: number | undefined,
): Promise<Blob | null> {
  return new Promise((resolve) => canvas.toBlob(resolve, contentType, quality));
}

function blobBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error);
    reader.onload = () => {
      const value = String(reader.result);
      resolve(value.slice(value.indexOf(",") + 1));
    };
    reader.readAsDataURL(blob);
  });
}

function isVisible(image: HTMLImageElement): boolean {
  const style = getComputedStyle(image);
  const rect = image.getBoundingClientRect();
  return (
    style.display !== "none" &&
    style.visibility !== "hidden" &&
    rect.width > 0 &&
    rect.height > 0
  );
}
