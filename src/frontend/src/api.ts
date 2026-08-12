import type {
  AnimalListResponse,
  GuideChatResponse,
  GuideCapability,
  MapGuide,
  MapNamedLocation,
  WikiIndexResponse,
  WikiPage,
} from "./types";

export const API_BALANCE_ERROR_CODE = "API_BALANCE_EXHAUSTED";

interface ApiErrorDetail {
  code?: string;
  message?: string;
}

export class ApiRequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
    super(message);
    this.name = "ApiRequestError";
  }
}

export function isApiBalanceError(reason: unknown): boolean {
  return reason instanceof ApiRequestError && reason.code === API_BALANCE_ERROR_CODE;
}

export interface AnimalQuery {
  q?: string;
  site?: string;
  name?: string;
}

export async function fetchAnimals(
  query: AnimalQuery = {},
  signal?: AbortSignal,
): Promise<AnimalListResponse> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value?.trim()) params.set(key, value.trim());
  }

  const suffix = params.size ? `?${params.toString()}` : "";
  const response = await fetch(`/api/animals${suffix}`, { signal });
  if (!response.ok) {
    throw new Error(`动物资料请求失败（${response.status}）`);
  }
  return response.json() as Promise<AnimalListResponse>;
}

export async function fetchMapGuide(signal?: AbortSignal): Promise<MapGuide> {
  const response = await fetch("/api/map", { signal });
  if (!response.ok) {
    throw new Error(`地图配置请求失败（${response.status}）`);
  }
  return response.json() as Promise<MapGuide>;
}

export async function fetchWikiIndex(
  query: { q?: string; site?: string } = {},
  signal?: AbortSignal,
): Promise<WikiIndexResponse> {
  const params = new URLSearchParams();
  if (query.q?.trim()) params.set("q", query.q.trim());
  if (query.site?.trim()) params.set("site", query.site.trim());
  const suffix = params.size ? `?${params.toString()}` : "";
  const response = await fetch(`/api/wiki${suffix}`, { signal });
  if (!response.ok) throw new Error(`园内趣事请求失败（${response.status}）`);
  return response.json() as Promise<WikiIndexResponse>;
}

export async function fetchWikiPage(
  site: string,
  scientificName: string,
  animal: string,
  signal?: AbortSignal,
): Promise<WikiPage> {
  const params = new URLSearchParams({ site, scientific_name: scientificName, animal });
  const response = await fetch(`/api/wiki/page?${params.toString()}`, { signal });
  if (!response.ok) throw new Error(`动物故事请求失败（${response.status}）`);
  return response.json() as Promise<WikiPage>;
}

export async function sendGuideMessage(
  message: string,
  sessionId: string | null,
  selectedSites: string[],
  selectedAnimals: string[],
  origin: MapNamedLocation | null,
  enabledCapabilities: GuideCapability[],
): Promise<GuideChatResponse> {
  return guideRequest("/api/guide/chat", {
    session_id: sessionId,
    message,
    enabled_capabilities: enabledCapabilities,
    map_context: {
      selected_sites: selectedSites,
      selected_animals: selectedAnimals,
      origin,
    },
  });
}

export async function continueGuideRun(
  runId: string,
  sessionId: string,
  values: Record<string, string | number | boolean>,
): Promise<GuideChatResponse> {
  return guideRequest(`/api/guide/chat/${encodeURIComponent(runId)}/continue`, {
    session_id: sessionId,
    values,
  });
}

async function guideRequest(path: string, body: object): Promise<GuideChatResponse> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as {
      detail?: string | ApiErrorDetail;
    };
    const detail = payload.detail;
    const message =
      typeof detail === "string"
        ? detail
        : detail?.message || `导览请求失败（${response.status}）`;
    throw new ApiRequestError(message, response.status, typeof detail === "object" ? detail.code : undefined);
  }
  return response.json() as Promise<GuideChatResponse>;
}
