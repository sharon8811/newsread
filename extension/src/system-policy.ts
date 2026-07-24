import type { SystemRule } from "./types.js";

type SystemRuleDefinition = Omit<SystemRule, "enabled">;

export const SYSTEM_POLICY_REVISION = 1;

export const DEFAULT_SYSTEM_RULES: SystemRuleDefinition[] = [
  {
    id: "google-home",
    label: "Google homepage",
    description: "Skip the Google search landing page.",
    hosts: ["google.com", "www.google.com"],
    path_match: "exact",
    path: "/",
  },
  {
    id: "google-search",
    label: "Google search results",
    description:
      "Skip Google result pages while retaining other Google-hosted content.",
    hosts: ["google.com", "www.google.com"],
    path_match: "exact",
    path: "/search",
  },
  {
    id: "bing-home",
    label: "Bing homepage",
    description: "Skip the Bing search landing page.",
    hosts: ["bing.com", "www.bing.com"],
    path_match: "exact",
    path: "/",
  },
  {
    id: "bing-search",
    label: "Bing search results",
    description: "Skip Bing result pages.",
    hosts: ["bing.com", "www.bing.com"],
    path_match: "exact",
    path: "/search",
  },
  {
    id: "duckduckgo-search",
    label: "DuckDuckGo search",
    description: "Skip DuckDuckGo landing and result pages.",
    hosts: ["duckduckgo.com", "www.duckduckgo.com"],
    path_match: "exact",
    path: "/",
  },
  {
    id: "google-account",
    label: "Google account screens",
    description: "Skip Google sign-in and account chooser pages.",
    hosts: ["accounts.google.com"],
    path_match: "prefix",
    path: "/",
  },
  {
    id: "microsoft-account",
    label: "Microsoft account screens",
    description: "Skip Microsoft sign-in and account chooser pages.",
    hosts: ["login.microsoftonline.com"],
    path_match: "prefix",
    path: "/",
  },
  {
    id: "apple-account",
    label: "Apple account screens",
    description: "Skip Apple sign-in and account management pages.",
    hosts: ["appleid.apple.com"],
    path_match: "prefix",
    path: "/",
  },
  {
    id: "github-login",
    label: "GitHub sign-in",
    description: "Skip the GitHub sign-in page without excluding GitHub content.",
    hosts: ["github.com"],
    path_match: "exact",
    path: "/login",
  },
];

export function effectiveSystemRules(rules: SystemRule[]): SystemRuleDefinition[] {
  if (!rules.length) return DEFAULT_SYSTEM_RULES;
  const builtIns = new Map(DEFAULT_SYSTEM_RULES.map((rule) => [rule.id, rule]));
  return rules.flatMap((rule) => {
    if (!rule.enabled) return [];
    const fallback = builtIns.get(rule.id);
    const hosts = Array.isArray(rule.hosts) ? rule.hosts : fallback?.hosts;
    const pathMatch = rule.path_match ?? fallback?.path_match;
    const path = rule.path ?? fallback?.path;
    if (
      !hosts?.length ||
      (pathMatch !== "exact" && pathMatch !== "prefix") ||
      typeof path !== "string"
    ) {
      return [];
    }
    return [
      {
        id: rule.id,
        label: rule.label,
        description: rule.description,
        hosts,
        path_match: pathMatch,
        path,
      },
    ];
  });
}

export function matchingSystemRule(
  url: URL,
  rules: SystemRule[],
): SystemRuleDefinition | null {
  return (
    effectiveSystemRules(rules).find((rule) => {
      if (!rule.hosts.includes(url.hostname)) return false;
      if (rule.path_match === "exact") return url.pathname === rule.path;
      const prefix = rule.path.replace(/\/+$/, "");
      return url.pathname === rule.path || url.pathname.startsWith(`${prefix}/`);
    }) ?? null
  );
}
