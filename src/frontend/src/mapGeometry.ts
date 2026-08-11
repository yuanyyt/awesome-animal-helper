import type { MapLocation } from "./types";

export interface MapBounds {
  southWest: MapLocation;
  northEast: MapLocation;
}

export function polygonBounds(points: MapLocation[]): MapBounds | null {
  if (!points.length) return null;
  const longitudes = points.map((point) => point.longitude);
  const latitudes = points.map((point) => point.latitude);
  return {
    southWest: {
      longitude: Math.min(...longitudes),
      latitude: Math.min(...latitudes),
    },
    northEast: {
      longitude: Math.max(...longitudes),
      latitude: Math.max(...latitudes),
    },
  };
}

export function isPointInPolygon(
  point: MapLocation,
  polygon: MapLocation[],
): boolean {
  if (polygon.length < 3) return false;

  let inside = false;
  for (let index = 0; index < polygon.length; index += 1) {
    const current = polygon[index];
    const previous = polygon[(index + polygon.length - 1) % polygon.length];
    if (isPointOnSegment(point, previous, current)) return true;

    const crossesLatitude =
      (current.latitude > point.latitude) !== (previous.latitude > point.latitude);
    if (!crossesLatitude) continue;
    const crossingLongitude =
      ((previous.longitude - current.longitude) *
        (point.latitude - current.latitude)) /
        (previous.latitude - current.latitude) +
      current.longitude;
    if (point.longitude < crossingLongitude) inside = !inside;
  }
  return inside;
}

function cross(origin: MapLocation, left: MapLocation, right: MapLocation): number {
  return (
    (left.longitude - origin.longitude) * (right.latitude - origin.latitude) -
    (left.latitude - origin.latitude) * (right.longitude - origin.longitude)
  );
}

function isPointOnSegment(
  point: MapLocation,
  start: MapLocation,
  end: MapLocation,
): boolean {
  const epsilon = 1e-10;
  if (Math.abs(cross(start, end, point)) > epsilon) return false;
  return (
    point.longitude >= Math.min(start.longitude, end.longitude) - epsilon &&
    point.longitude <= Math.max(start.longitude, end.longitude) + epsilon &&
    point.latitude >= Math.min(start.latitude, end.latitude) - epsilon &&
    point.latitude <= Math.max(start.latitude, end.latitude) + epsilon
  );
}
