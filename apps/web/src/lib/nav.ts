export interface NavItem {
  href: string;
  label: string;
  short: string;
  icon: "dashboard" | "signals" | "catalysts" | "lab" | "models" | "positions" | "alerts" | "operations" | "settings" | "setup";
  primary?: boolean;
}

export const NAV: NavItem[] = [
  { href: "/", label: "Command Center", short: "Home", icon: "dashboard", primary: true },
  { href: "/signals", label: "Signals Explorer", short: "Signals", icon: "signals", primary: true },
  { href: "/positions", label: "My Positions", short: "Positions", icon: "positions", primary: true },
  { href: "/catalysts", label: "Catalyst Intelligence", short: "Catalysts", icon: "catalysts" },
  { href: "/lab", label: "Strategy & Model Lab", short: "Lab", icon: "lab" },
  { href: "/models", label: "Models & Automation", short: "Models", icon: "models", primary: true },
  { href: "/alerts", label: "Alerts & Delivery", short: "Alerts", icon: "alerts" },
  { href: "/operations", label: "Operations & Audit", short: "Ops", icon: "operations" },
  { href: "/settings", label: "Settings & Secrets", short: "Settings", icon: "settings" },
  { href: "/onboarding", label: "Guided Setup", short: "Setup", icon: "setup" },
];
