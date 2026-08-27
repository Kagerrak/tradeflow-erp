import type { ReactNode } from "react";
import { AppShell } from "@/components/shell/app-shell";
import { consoleEnvironmentLabel } from "@/lib/server-environment";

export default function ConsoleLayout({ children }: { children: ReactNode }) {
  return (
    <AppShell environmentLabel={consoleEnvironmentLabel()}>{children}</AppShell>
  );
}
