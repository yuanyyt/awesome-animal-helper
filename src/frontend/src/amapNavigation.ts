import type { MapLocation, RouteLeg, RouteOption } from "./types";

const SOURCE_APPLICATION = "awesome-animal-helper";

export interface AmapNavigationTarget {
  androidUri: string;
  h5Uri: string;
  fallbackH5Uri: string;
  label: string;
}

interface NamedLocation extends MapLocation {
  name: string;
}

export function buildAmapNavigationTarget(
  route: RouteOption,
): AmapNavigationTarget | null {
  const shuttleIndex = route.legs.findIndex((leg) => leg.mode === "shuttle");
  if (shuttleIndex >= 0) {
    const firstLeg = route.legs[0];
    if (!firstLeg || firstLeg.mode !== "walking") return null;
    return buildTarget([firstLeg], "高德导航到上车站");
  }

  return buildTarget(
    route.legs.filter((leg) => leg.mode === "walking"),
    "在高德打开",
  );
}

export function isAndroidBrowser(userAgent = window.navigator.userAgent): boolean {
  return /Android/i.test(userAgent);
}

function buildTarget(
  legs: RouteLeg[],
  label: string,
): AmapNavigationTarget | null {
  const firstLeg = legs[0];
  const lastLeg = legs.at(-1);
  const originPoint = firstLeg?.polyline[0];
  const destinationPoint = lastLeg?.polyline.at(-1);
  if (!firstLeg || !lastLeg || !originPoint || !destinationPoint) return null;

  const origin = { ...originPoint, name: firstLeg.from_name };
  const destination = { ...destinationPoint, name: lastLeg.to_name };
  const waypoints = legs
    .slice(0, -1)
    .flatMap((leg) => {
      const point = leg.polyline.at(-1);
      return point ? [{ ...point, name: leg.to_name }] : [];
    })
    .filter(
      (point, index, points) =>
        !sameLocation(point, origin)
        && !sameLocation(point, destination)
        && points.findIndex((candidate) => sameLocation(candidate, point)) === index,
    );

  return {
    androidUri: buildAndroidUri(origin, destination, waypoints),
    h5Uri: buildH5Uri(origin, destination, true),
    fallbackH5Uri: buildH5Uri(origin, destination, false),
    label,
  };
}

function buildAndroidUri(
  origin: NamedLocation,
  destination: NamedLocation,
  waypoints: NamedLocation[],
): string {
  const params = new URLSearchParams({
    sourceApplication: SOURCE_APPLICATION,
    sname: origin.name,
    slat: coordinate(origin.latitude),
    slon: coordinate(origin.longitude),
    dname: destination.name,
    dlat: coordinate(destination.latitude),
    dlon: coordinate(destination.longitude),
    dev: "0",
    t: "2",
  });
  if (waypoints.length) {
    params.set("vian", String(waypoints.length));
    params.set("vialons", waypoints.map((point) => coordinate(point.longitude)).join("|"));
    params.set("vialats", waypoints.map((point) => coordinate(point.latitude)).join("|"));
    params.set("vianames", waypoints.map((point) => point.name).join("|"));
  }
  return `amapuri://route/plan/?${params.toString()}`;
}

function buildH5Uri(
  origin: NamedLocation,
  destination: NamedLocation,
  callNative: boolean,
): string {
  const params = new URLSearchParams({
    from: `${coordinate(origin.longitude)},${coordinate(origin.latitude)},${origin.name}`,
    to: `${coordinate(destination.longitude)},${coordinate(destination.latitude)},${destination.name}`,
    mode: "walk",
    src: SOURCE_APPLICATION,
    callnative: callNative ? "1" : "0",
  });
  return `https://uri.amap.com/navigation?${params.toString()}`;
}

function coordinate(value: number): string {
  return value.toFixed(6);
}

function sameLocation(left: MapLocation, right: MapLocation): boolean {
  return coordinate(left.longitude) === coordinate(right.longitude)
    && coordinate(left.latitude) === coordinate(right.latitude);
}
