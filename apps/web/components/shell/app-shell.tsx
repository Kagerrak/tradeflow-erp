"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { navSections } from "./navigation";

export function AppShell({
  children,
  environmentLabel,
}: {
  children: React.ReactNode;
  environmentLabel: string;
}) {
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to main content
      </a>

      <header className="app-topbar">
        <div className="app-topbar-start">
          <button
            aria-controls="mobile-nav"
            aria-expanded={mobileOpen}
            className="app-menu-button"
            onClick={() => setMobileOpen((open) => !open)}
            type="button"
          >
            <span aria-hidden="true" className="app-menu-icon" />
            <span className="visually-hidden">
              {mobileOpen ? "Close" : "Open"} navigation
            </span>
          </button>
          <Link className="app-brand" href="/demo">
            <span className="app-brand-mark" aria-hidden="true">
              TF
            </span>
            <span className="app-brand-name">TradeFlow</span>
          </Link>
        </div>
        <span className="app-environment">{environmentLabel}</span>
      </header>

      {mobileOpen && (
        <div
          className="app-mobile-overlay"
          onClick={() => setMobileOpen(false)}
          role="presentation"
        />
      )}

      <aside
        className={`app-rail ${mobileOpen ? "app-rail-open" : ""}`}
        id="mobile-nav"
        aria-label="Primary navigation"
      >
        <div className="app-rail-brand">
          <Link className="app-brand" href="/demo">
            <span className="app-brand-mark" aria-hidden="true">
              TF
            </span>
            <span className="app-brand-name">TradeFlow</span>
          </Link>
        </div>
        <nav className="app-rail-nav">
          {navSections.map((section) => (
            <div className="app-rail-section" key={section.label}>
              <h3 className="app-rail-section-label">{section.label}</h3>
              <ul className="app-rail-list">
                {section.items.map((item) => {
                  const active = item.href === pathname;
                  return (
                    <li key={item.href}>
                      <Link
                        aria-current={active ? "page" : undefined}
                        className={`app-rail-item ${active ? "app-rail-item-active" : ""}`}
                        href={item.href}
                        onClick={() => setMobileOpen(false)}
                      >
                        <span className="app-rail-marker" aria-hidden="true">
                          {item.marker ?? item.label.charAt(0)}
                        </span>
                        <span className="app-rail-label">{item.label}</span>
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </nav>
        <div className="app-rail-foot">
          <span className="app-rail-status" aria-hidden="true" />
          <span>Server-authoritative</span>
        </div>
      </aside>

      <main className="app-main" id="main-content">
        {children}
      </main>
    </div>
  );
}
