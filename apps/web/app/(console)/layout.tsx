import type { ReactNode } from "react";
import { DemoPreparationGate } from "@/components/demo-preparation-gate";
import { AppShell } from "@/components/shell/app-shell";
import { consoleEnvironmentLabel } from "@/lib/server-environment";

export default function ConsoleLayout({ children }: { children: ReactNode }) {
  return (
    <AppShell environmentLabel={consoleEnvironmentLabel()}>
      <DemoPreparationGate />
      {children}
    </AppShell>
  );
}
