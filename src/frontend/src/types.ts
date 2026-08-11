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

export interface MapBoundary {
  points: MapLocation[];
  source: string;
  source_url: string;
  attribution: string;
  object_type: "way";
  object_id: number;
}

export interface MapGuide {
  center: MapLocation;
  zoom: number;
  image_url: string;
  points: MapPoint[];
  boundary: MapBoundary;
  provider: string;
  js_api: MapJsConfig | null;
  default_origin: MapNamedLocation | null;
}

export interface MapNamedLocation extends MapLocation {
  name: string;
}

export interface RouteStep {
  instruction: string;
  distance_meters: number;
  duration_seconds: number;
  walk_type: string | null;
}

export interface RouteLeg {
  from_name: string;
  to_name: string;
  distance_meters: number;
  duration_seconds: number;
  steps: RouteStep[];
  polyline: MapLocation[];
}

export interface RouteOption {
  id: string;
  name: string;
  description: string;
  sites: string[];
  distance_meters: number;
  walking_minutes: number;
  visiting_minutes: number;
  total_minutes: number;
  calories_kcal: number | null;
  calories_range_kcal: [number, number] | null;
  has_stairs: boolean;
  warnings: string[];
  legs: RouteLeg[];
  polyline: MapLocation[];
}

export interface GuideInputField {
  name: string;
  field_type: string;
  description: string;
  value: string | number | boolean | string[] | null;
}

export interface GuideChatResponse {
  session_id: string;
  run_id: string;
  status: "completed" | "input_required";
  assistant_message: string;
  intent: "route" | "animal_knowledge" | "mixed" | "unknown";
  resolved_sites: string[];
  unresolved_terms: string[];
  knowledge_items: AnimalDetail[];
  required_inputs: GuideInputField[];
  route_options: RouteOption[];
}

export interface GuideAutoRequest {
  id: number;
  message: string;
}
