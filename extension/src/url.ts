const RESERVED_SUFFIXES = new Set([
  "example",
  "home",
  "home.arpa",
  "internal",
  "invalid",
  "lan",
  "local",
  "localhost",
  "onion",
  "test",
]);

const SENSITIVE_QUERY = /(^|[_-])(access_?token|token|auth|authorization|session|session_?id|sid|code|key|api_?key|signature|sig|secret|password|passwd)([_-]|$)/i;
const TRACKING_QUERY = new Set([
  "dclid",
  "fbclid",
  "gclid",
  "igshid",
  "msclkid",
  "twclid",
]);

function isPrivateIpv4(hostname: string): boolean {
  const parts = hostname.split(".").map(Number);
  if (parts.length !== 4 || parts.some((part) => !Number.isInteger(part))) {
    return false;
  }
  const [a = 0, b = 0] = parts;
  return (
    a === 0 ||
    a === 10 ||
    a === 127 ||
    a >= 224 ||
    (a === 169 && b === 254) ||
    (a === 172 && b >= 16 && b <= 31) ||
    (a === 192 && b === 168)
  );
}

export function normalizeCaptureUrl(value: string): URL | null {
  try {
    const url = new URL(value);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) {
      return null;
    }
    const hostname = url.hostname.toLowerCase().replace(/\.$/, "");
    if (
      !hostname.includes(".") ||
      hostname === "::1" ||
      hostname.startsWith("[") ||
      isPrivateIpv4(hostname) ||
      [...RESERVED_SUFFIXES].some(
        (suffix) => hostname === suffix || hostname.endsWith(`.${suffix}`),
      )
    ) {
      return null;
    }
    url.hash = "";
    for (const name of [...url.searchParams.keys()]) {
      const lowered = name.toLowerCase();
      if (
        lowered.startsWith("utm_") ||
        lowered.startsWith("mc_") ||
        TRACKING_QUERY.has(lowered) ||
        SENSITIVE_QUERY.test(lowered)
      ) {
        url.searchParams.delete(name);
      }
    }
    return url;
  } catch {
    return null;
  }
}

export function normalizeHostname(value: string): string | null {
  const hostname = value.trim().toLowerCase().replace(/^\*\./, "").replace(/\.$/, "");
  return /^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$/.test(hostname) &&
    hostname.includes(".")
    ? hostname
    : null;
}

export function hostnameMatches(
  hostname: string,
  ruleHostname: string,
  matchSubdomains = true,
): boolean {
  return (
    hostname === ruleHostname ||
    (matchSubdomains && hostname.endsWith(`.${ruleHostname}`))
  );
}

export function permissionPattern(serverUrl: string): string {
  const url = new URL(serverUrl);
  if (!["http:", "https:"].includes(url.protocol)) {
    throw new Error("NewsRead server must use HTTP or HTTPS");
  }
  return `${url.origin}/*`;
}
