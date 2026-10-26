/**
 * Architecture — from "what is this?" down to the lines that implement it.
 *
 * What the repository is, how it works in a handful of concepts, its major
 * parts, a path to follow and where to start; drilling into a part shows its
 * runtime components, and the inspector shows what was read from source, what
 * was inferred and what a real run measured.
 *
 * Spec: docs/design/03-architecture.md
 */

"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { ArchitectureExperience } from "@/components/architecture-experience";
import { Panel, Skeleton } from "@/components/ui";
import { api, optional } from "@/lib/api";
import type { RuntimeOverlay } from "@/lib/types";

import { useWorkspace } from "../workspace";

export default function ArchitecturePage() {
  // useSearchParams needs a Suspense boundary for static rendering.
  return (
    <Suspense fallback={<Skeleton rows={4} />}>
      <Architecture />
    </Suspense>
  );
}

function Architecture() {
  const { jobId, architecture, manifest } = useWorkspace();
  const searchParams = useSearchParams();
  const [overlay, setOverlay] = useState<RuntimeOverlay | null>(null);

  useEffect(() => {
    if (!architecture) return;
    let cancelled = false;
    void (async () => {
      try {
        const next = await optional(api.architectureRuntime(jobId));
        if (!cancelled) setOverlay(next);
      } catch {
        // The overlay is optional: without it, nothing is shown as measured.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [jobId, architecture]);

  if (!architecture || !manifest) {
    return (
      <Panel title="Architecture">
        <Skeleton rows={4} />
        <p className="mt-3 text-sm text-fg-secondary">
          The architecture appears here once RepoLab has finished reading the repository.
        </p>
      </Panel>
    );
  }

  return (
    <ArchitectureExperience
      jobId={jobId}
      view={architecture}
      manifest={manifest}
      overlay={overlay}
      initialSubsystem={searchParams.get("subsystem")}
      initialSelection={searchParams.get("node")}
      initialPerspective={searchParams.get("view")}
    />
  );
}
