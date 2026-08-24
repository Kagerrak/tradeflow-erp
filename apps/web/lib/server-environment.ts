import "server-only";

const labels: Record<string, string> = {
  demo: "Live demo",
  development: "Development",
  preview: "Preview",
  production: "Production",
  testing: "Test",
};

export function consoleEnvironmentLabel(): string {
  const environment =
    process.env.TRADEFLOW_ENVIRONMENT ??
    (process.env.NODE_ENV === "production" ? "production" : "development");

  return labels[environment] ?? environment;
}
