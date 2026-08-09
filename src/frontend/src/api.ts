import type { AnimalListResponse } from "./types";

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

