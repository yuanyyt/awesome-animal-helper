export interface SiteSummary {
  name: string;
  animal_count: number;
}

export interface AnimalDetail {
  name: string;
  scientific_name: string | null;
  taxonomy: string | null;
  habitat: string | null;
  distribution: string | null;
  diet: string | null;
  behavior: string | null;
  reproduction: string | null;
  conservation_status: string | null;
  fun_facts: string[];
  source_url: string | null;
  language: string | null;
  data_status: "success" | "partial" | string;
  sites: string[];
}

export interface AnimalListResponse {
  items: AnimalDetail[];
  sites: SiteSummary[];
  total: number;
  filtered_count: number;
}

export interface MapLocation {
  longitude: number;
  latitude: number;
}

export interface MapPoint extends MapLocation {
  site: string;
  poi_name: string;
  address: string;
  animal_count: number;
}

export interface MapJsConfig {
  api_key: string;
  service_host: string;
}

export interface MapGuide {
  center: MapLocation;
  zoom: number;
  image_url: string;
  points: MapPoint[];
  provider: string;
  js_api: MapJsConfig | null;
}
