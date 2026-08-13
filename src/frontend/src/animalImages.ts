import type { AnimalDetail } from "./types";

const MANIFEST_URL = "/animals/manifest.csv";
const RESPONSIVE_WIDTHS = [320, 640, 1024] as const;

export interface AnimalImageSet {
  fallback: string;
  src: string;
  srcset: string;
}

let manifestPromise: Promise<Map<string, string>> | undefined;

function normalize(value: string): string {
  return value.trim().toLocaleLowerCase().replace(/\s+/g, " ");
}

function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let value = "";
  let quoted = false;

  for (let index = 0; index < text.length; index += 1) {
    const character = text[index];
    const next = text[index + 1];

    if (character === '"' && quoted && next === '"') {
      value += '"';
      index += 1;
    } else if (character === '"') {
      quoted = !quoted;
    } else if (character === "," && !quoted) {
      row.push(value);
      value = "";
    } else if ((character === "\n" || character === "\r") && !quoted) {
      if (character === "\r" && next === "\n") index += 1;
      row.push(value);
      if (row.some(Boolean)) rows.push(row);
      row = [];
      value = "";
    } else {
      value += character;
    }
  }

  if (value || row.length) {
    row.push(value);
    rows.push(row);
  }

  return rows;
}

export function parseAnimalImageManifest(csv: string): Map<string, string> {
  const [rawHeaders = [], ...rows] = parseCsv(csv);
  const headers = rawHeaders.map((header) => header.replace(/^\uFEFF/, "").trim());
  const animalColumn = headers.indexOf("animal");
  const scientificNameColumn = headers.indexOf("scientific_name");
  const pathColumn = headers.indexOf("public_path");
  const statusColumn = headers.indexOf("status");
  const images = new Map<string, string>();

  for (const row of rows) {
    const path = row[pathColumn]?.trim();
    const status = row[statusColumn]?.trim();
    if (!path?.startsWith("/animals/") || !["success", "cached"].includes(status)) continue;

    const animal = row[animalColumn]?.trim();
    const scientificName = row[scientificNameColumn]?.trim();
    if (animal) images.set(`name:${normalize(animal)}`, path);
    if (scientificName && !images.has(`scientific:${normalize(scientificName)}`)) {
      images.set(`scientific:${normalize(scientificName)}`, path);
    }
  }

  return images;
}

async function loadManifest(): Promise<Map<string, string>> {
  const response = await fetch(MANIFEST_URL);
  if (!response.ok) throw new Error(`动物图册索引加载失败（${response.status}）`);
  return parseAnimalImageManifest(await response.text());
}

function responsivePath(path: string, width: (typeof RESPONSIVE_WIDTHS)[number]): string {
  return path.replace(/\.png$/i, `-${width}.webp`);
}

export async function resolveAnimalImageSet(
  animal: Pick<AnimalDetail, "name" | "scientific_name">,
): Promise<AnimalImageSet | null> {
  manifestPromise ??= loadManifest();

  try {
    const images = await manifestPromise;
    const fallback =
      images.get(`name:${normalize(animal.name)}`) ??
      (animal.scientific_name
        ? images.get(`scientific:${normalize(animal.scientific_name)}`)
        : undefined) ??
      null;
    if (!fallback) return null;
    return {
      fallback,
      src: responsivePath(fallback, 640),
      srcset: RESPONSIVE_WIDTHS.map(
        (width) => `${responsivePath(fallback, width)} ${width}w`,
      ).join(", "),
    };
  } catch {
    manifestPromise = undefined;
    return null;
  }
}

export async function resolveAnimalImage(
  animal: Pick<AnimalDetail, "name" | "scientific_name">,
): Promise<string | null> {
  const image = await resolveAnimalImageSet(animal);
  return image ? responsivePath(image.fallback, 320) : null;
}

export const animalImageManifestUrl = MANIFEST_URL;
