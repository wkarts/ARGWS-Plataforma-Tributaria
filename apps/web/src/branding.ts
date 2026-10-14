import type { InstallationBrand } from "./types.ts";

declare global {
  interface Window {
    __INSTALLATION_BRAND__?: InstallationBrand;
  }
}

export const DEFAULT_BRAND: InstallationBrand = {
  display_name: "Gestão Fiscal",
  short_name: "Fiscal",
  primary_color: "#255896",
  secondary_color: "#102743",
  logo_url: null,
  version: 0,
};

export function applyBrand(value: InstallationBrand): void {
  const primary = /^#[0-9a-fA-F]{6}$/.test(value.primary_color)
    ? value.primary_color
    : DEFAULT_BRAND.primary_color;
  const secondary = /^#[0-9a-fA-F]{6}$/.test(value.secondary_color)
    ? value.secondary_color
    : DEFAULT_BRAND.secondary_color;
  document.title = value.display_name || DEFAULT_BRAND.display_name;
  document.documentElement.style.setProperty("--brand-primary", primary);
  document.documentElement.style.setProperty("--brand-secondary", secondary);
  document.querySelector<HTMLMetaElement>('meta[name="theme-color"]')?.setAttribute("content", primary);
  const version = Number.isSafeInteger(value.version) && value.version >= 0 ? value.version : 0;
  document.querySelector<HTMLLinkElement>('link[rel="icon"]')?.setAttribute(
    "href", `/api/v1/installation/branding/icon.png?size=32&v=${version}`,
  );
  document.querySelector<HTMLLinkElement>('link[rel="apple-touch-icon"]')?.setAttribute(
    "href", `/api/v1/installation/branding/icon.png?size=180&v=${version}`,
  );
  document.querySelector<HTMLLinkElement>('link[rel="manifest"]')?.setAttribute(
    "href", `/api/v1/installation/branding/manifest.webmanifest?v=${version}`,
  );
  document.querySelector<HTMLLinkElement>('link[data-installation-theme]')?.setAttribute(
    "href", `/api/v1/installation/branding/theme.css?v=${version}`,
  );
}
