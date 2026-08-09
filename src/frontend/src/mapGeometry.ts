import type { MapLocation } from "./types";

export interface MapBounds {
  southWest: MapLocation;
  northEast: MapLocation;
}

export function expandedConvexHull(
  points: MapLocation[],
  scale = 1.5,
): MapLocation[] {
  const hull = convexHull(points);
  if (hull.length < 3) return hull;

  const center = hull.reduce(
    (total, point) => ({
      longitude: total.longitude + point.longitude / hull.length,
      latitude: total.latitude + point.latitude / hull.length,
    }),
    { longitude: 0, latitude: 0 },
  );

  return hull.map((point) => ({
    longitude: center.longitude + (point.longitude - center.longitude) * scale,
    latitude: center.latitude + (point.latitude - center.latitude) * scale,
  }));
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

function convexHull(points: MapLocation[]): MapLocation[] {
  const sorted = [...points]
    .filter(
      (point, index, all) =>
        all.findIndex(
          (candidate) =>
            candidate.longitude === point.longitude && candidate.latitude === point.latitude,
        ) === index,
    )
    .sort((left, right) =>
      left.longitude === right.longitude
        ? left.latitude - right.latitude
        : left.longitude - right.longitude,
    );
  if (sorted.length <= 2) return sorted;

  const lower: MapLocation[] = [];
  for (const point of sorted) {
    while (lower.length >= 2 && cross(lower.at(-2)!, lower.at(-1)!, point) <= 0) {
      lower.pop();
    }
    lower.push(point);
  }

  const upper: MapLocation[] = [];
  for (const point of [...sorted].reverse()) {
    while (upper.length >= 2 && cross(upper.at(-2)!, upper.at(-1)!, point) <= 0) {
      upper.pop();
    }
    upper.push(point);
  }

  return [...lower.slice(0, -1), ...upper.slice(0, -1)];
}

function cross(origin: MapLocation, left: MapLocation, right: MapLocation): number {
  return (
    (left.longitude - origin.longitude) * (right.latitude - origin.latitude) -
    (left.latitude - origin.latitude) * (right.longitude - origin.longitude)
  );
}
