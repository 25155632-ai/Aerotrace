import { createFileRoute } from "@tanstack/react-router";
import { Dashboard } from "@/components/Dashboard";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "Aerotrace — Aerospace Cyber-Intelligence" },
      {
        name: "description",
        content: "Real-time aviation intelligence: GNSS spoofing and trajectory anomaly detection.",
      },
      { property: "og:title", content: "Aerotrace — Aerospace Cyber-Intelligence" },
      {
        property: "og:description",
        content: "Real-time aviation intelligence: GNSS spoofing and trajectory anomaly detection.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: Dashboard,
});
