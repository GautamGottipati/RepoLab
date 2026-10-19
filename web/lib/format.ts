/** Formatting helpers shared across screens. */

export function bytes(value: number): string {
  const units = ["B", "KiB", "MiB", "GiB"];
  let scaled = value;
  for (const unit of units) {
    if (Math.abs(scaled) < 1024 || unit === "GiB") {
      return unit === "B" ? `${scaled.toLocaleString()} B` : `${scaled.toFixed(1)} ${unit}`;
    }
    scaled /= 1024;
  }
  return `${scaled.toFixed(1)} GiB`;
}

export function ms(value: number | null | undefined): string {
  if (value == null) return "—";
  if (value < 1000) return `${Math.round(value)} ms`;
  return `${(value / 1000).toFixed(1)} s`;
}

export function seconds(value: number | null | undefined): string {
  if (value == null) return "—";
  if (value < 60) return `${value.toFixed(1)}s`;
  const minutes = Math.floor(value / 60);
  return `${minutes}m ${Math.round(value % 60)}s`;
}

export function count(value: number | null | undefined): string {
  return value == null ? "—" : value.toLocaleString();
}

export function percent(value: number | null | undefined): string {
  if (value == null) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(1)}%`;
}

export function shortHash(value: string | null | undefined, length = 12): string {
  return value ? value.slice(0, length) : "—";
}

/** `owner/name` from a repository URL, for a header that is not a wall of URL. */
export function slug(repoUrl: string): string {
  try {
    const parts = new URL(repoUrl).pathname.replace(/^\/+|\/+$|\.git$/g, "").split("/");
    return parts.slice(-2).join("/") || repoUrl;
  } catch {
    return repoUrl;
  }
}
