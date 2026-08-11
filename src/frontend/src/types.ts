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
  wiki_fact_count: number;
}

export type AnimalDetailSection = "profile" | "stories";

export interface AnimalListResponse {
  items: AnimalDetail[];
  sites: SiteSummary[];
  total: number;
  filtered_count: number;
}

export interface WikiSource {
  title: string;
  url: string;
  published_at: string;
}

export interface WikiFact {
  text: string;
  evidence: string;
  source: WikiSource;
}

export interface WikiAnimalSummary {
  site: string;
  scientific_name: string;
  animal_name: string;
  aliases: string[];
  fact_count: number;
  source_count: number;
}

export interface WikiScientificGroup {
  scientific_name: string;
  animals: WikiAnimalSummary[];
}

export interface WikiSiteGroup {
  name: string;
  fact_count: number;
  scientific_groups: WikiScientificGroup[];
}

export interface WikiIndexResponse {
  sites: WikiSiteGroup[];
  total_animals: number;
  total_facts: number;
  filtered_count: number;
  generated_at: string;
}

export interface WikiPage extends WikiAnimalSummary {
  facts: WikiFact[];
  markdown: string;
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

export type FacilityCategory =
  | "metro"
  | "bus_terminal"
  | "train_station"
  | "visitor_center"
  | "entrance"
  | "bag_storage"
  | "ticket_office"
  | "parking"
  | "smoking_area"
  | "drinking_water"
  | "tour_bus_station"
  | "mobility_rental"
  | "police"
  | "shopping"
  | "restaurant"
  | "coffee"
  | "toilet"
  | "nursing_room"
  | "family_toilet";

export interface FacilityPoint extends MapLocation {
  id: string;
  name: string;
  category: FacilityCategory;
  address: string;
}

export interface ShuttleStation extends MapLocation {
  id: string;
  name: string;
  order: number;
}

export interface ShuttleSchedule {
  day_type: "weekday" | "statutory_holiday";
  label: string;
  fare_yuan: number;
  ticket_sales_start: string;
  ticket_sales_end: string;
  service_start: string;
  service_end: string;
}

export interface ShuttleService {
  name: string;
  loop: boolean;
  stations: ShuttleStation[];
  polyline: MapLocation[];
  schedules: ShuttleSchedule[];
  average_speed_kmh: number;
  average_wait_minutes: number;
  notes: string[];
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
  facilities: FacilityPoint[];
  shuttle: ShuttleService | null;
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
  mode: "walking" | "shuttle";
  estimated: boolean;
}

export interface RouteOption {
  id: string;
  name: string;
  description: string;
  sites: string[];
  distance_meters: number;
  walking_distance_meters: number | null;
  walking_minutes: number;
  shuttle_minutes: number;
  visiting_minutes: number;
  total_minutes: number;
  calories_kcal: number | null;
  calories_range_kcal: [number, number] | null;
  has_stairs: boolean;
  warnings: string[];
  legs: RouteLeg[];
  polyline: MapLocation[];
  transport_preference: "walking" | "mixed";
  uses_shuttle: boolean;
  shuttle_fare_yuan: number | null;
  estimated_wait_minutes: number;
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
  intent: "route" | "animal_knowledge" | "mixed" | "facility" | "unknown";
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
