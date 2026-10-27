/**
 * One way to cite source, everywhere: a chip that opens the exact lines.
 *
 * `SourceRef` shows `path:L120–184` (and the symbol, when there is one).
 * Clicking it opens the `SourceViewer` drawer, which fetches the file from
 * RepoLab's own copy *at the commit the claim was made against* and highlights
 * the range. A reference RepoLab could not verify says so instead of linking.
 *
 * The workspace provides the viewer (`SourceViewerProvider`); outside it, a chip
 * falls back to the same file at the same commit on its host.
 */

"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { CheckIcon, Drawer, SourceIcon } from "./ui";
import { api, ApiError } from "@/lib/api";
import { shortHash } from "@/lib/format";
import type { CodeReference, EvidenceStrength, SourceFile } from "@/lib/types";

export type SourceTarget = Pick<CodeReference, "path" | "start_line" | "end_line"> &
  Partial<Pick<CodeReference, "commit_sha" | "symbol" | "verified" | "verification" | "strength">> & {
    /** Why this source is being shown, in a sentence. */
    reason?: string | null;
  };

type Viewer = {
  jobId: string;
  repoUrl: string;
  commitSha: string;
  open: (target: SourceTarget) => void;
};

const ViewerContext = createContext<Viewer | null>(null);

export function useSourceViewer(): Viewer | null {
  return useContext(ViewerContext);
}

export const STRENGTH_LABEL: Record<EvidenceStrength, string> = {
  direct_source: "source code",
  build_metadata: "build file",
  configuration: "configuration",
  test: "test",
  in_repository_documentation: "repository docs",
  inferred_from_multiple_files: "inferred from several files",
};

export function hostUrl(repoUrl: string, commit: string, target: SourceTarget): string | null {
  const match = repoUrl.match(/^https:\/\/github\.com\/([^/]+)\/([^/]+?)(?:\.git)?\/?$/);
  if (!match || !commit) return null;
  const range = target.start_line
    ? `#L${target.start_line}${target.end_line && target.end_line !== target.start_line ? `-L${target.end_line}` : ""}`
    : "";
  return `https://github.com/${match[1]}/${match[2]}/blob/${commit}/${target.path}${range}`;
}

export function rangeLabel(target: SourceTarget): string {
  if (!target.start_line) return "";
  if (target.end_line && target.end_line !== target.start_line) return `L${target.start_line}–${target.end_line}`;
  return `L${target.start_line}`;
}

export function SourceViewerProvider({
  jobId,
  repoUrl,
  commitSha,
  children,
}: {
  jobId: string;
  repoUrl: string;
  commitSha: string;
  children: ReactNode;
}) {
  const [target, setTarget] = useState<SourceTarget | null>(null);
  const open = useCallback((next: SourceTarget) => setTarget(next), []);
  const value = useMemo(() => ({ jobId, repoUrl, commitSha, open }), [jobId, repoUrl, commitSha, open]);
  return (
    <ViewerContext.Provider value={value}>
      {children}
      {target && (
        <Drawer
          open
          onClose={() => setTarget(null)}
          title={target.path.split("/").pop() ?? target.path}
          description={target.reason ?? undefined}
          width="lg"
          data-testid="source-viewer"
        >
          <SourceViewer jobId={jobId} repoUrl={repoUrl} commitSha={commitSha} target={target} />
        </Drawer>
      )}
    </ViewerContext.Provider>
  );
}

/** The cited file at its commit, with the cited range highlighted. */
export function SourceViewer({
  jobId,
  repoUrl,
  commitSha,
  target,
}: {
  jobId: string;
  repoUrl: string;
  commitSha: string;
  target: SourceTarget;
}) {
  const commit = target.commit_sha || commitSha;
  const [file, setFile] = useState<SourceFile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const highlight = useRef<HTMLTableRowElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    const around = target.start_line ?? undefined;
    const request =
      commit === commitSha
        ? api.source(jobId, target.path, around)
        : api.sourceAt(repoUrl, commit, target.path, around);
    request.then(
      (next) => !cancelled && setFile(next),
      (cause) => !cancelled && setError(cause instanceof ApiError ? cause.message : String(cause)),
    );
    return () => {
      cancelled = true;
    };
  }, [jobId, repoUrl, commit, commitSha, target.path, target.start_line]);

  useEffect(() => {
    highlight.current?.scrollIntoView?.({ block: "center" });
  }, [file]);

  const start = target.start_line ?? 0;
  const end = target.end_line ?? target.start_line ?? 0;
  const link = hostUrl(repoUrl, commit, target);

  return (
    <div className="flex flex-col gap-4" data-testid="source-viewer-body">
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 text-meta">
        <dt className="text-fg-muted">Repository</dt>
        <dd className="truncate font-mono text-fg">{repoUrl.replace(/^https:\/\/github\.com\//, "")}</dd>
        <dt className="text-fg-muted">Commit</dt>
        <dd className="font-mono text-fg" data-testid="viewer-commit" title={commit}>
          {shortHash(commit)}
          {commit !== commitSha && (
            <span className="ml-2 text-exp-fg">different from the investigated {shortHash(commitSha)}</span>
          )}
        </dd>
        <dt className="text-fg-muted">File</dt>
        <dd className="break-all font-mono text-fg">{target.path}</dd>
        {target.start_line && (
          <>
            <dt className="text-fg-muted">Lines</dt>
            <dd className="font-mono text-fg" data-testid="viewer-range">
              {rangeLabel(target)}
              {target.symbol && <span className="ml-2 text-arch-fg">{target.symbol}</span>}
            </dd>
          </>
        )}
      </dl>
      {target.verified ? (
        <p className="flex items-center gap-1.5 text-meta text-run-fg" data-testid="viewer-verified">
          <CheckIcon size={14} /> RepoLab re-read these lines at this commit
          {target.verification ? ` (${target.verification})` : ""}.
        </p>
      ) : target.verified === false && target.verification ? (
        <p className="text-meta text-exp-fg">Not verified: {target.verification}.</p>
      ) : null}

      {error && <p className="text-sm text-danger-fg">{error}</p>}
      {!file && !error && <p className="text-sm text-fg-secondary">Loading the file…</p>}
      {file && !file.captured && (
        <div className="rounded-lg border border-line bg-surface-subtle p-4 text-sm text-fg-secondary" data-testid="viewer-not-captured">
          RepoLab did not keep a copy of this file at this commit, so it cannot show it here.
          {file.host_url && (
            <>
              {" "}
              <a href={link ?? file.host_url} target="_blank" rel="noreferrer" className="font-medium text-brand-fg hover:underline">
                Open it on GitHub at {shortHash(commit)}
              </a>
              .
            </>
          )}
        </div>
      )}
      {file && file.captured && (
        <div className="overflow-hidden rounded-lg border border-code-line bg-code">
          <div className="max-h-[60vh] overflow-auto">
            <table className="w-full border-collapse font-mono text-xs leading-5 text-code-fg" data-testid="viewer-code">
              <tbody>
                {file.lines.map((line, index) => {
                  const number = file.first_line + index;
                  const inside = start > 0 && number >= start && number <= end;
                  return (
                    <tr
                      key={number}
                      ref={number === start ? highlight : undefined}
                      data-highlighted={inside || undefined}
                      className={inside ? "bg-arch/15" : ""}
                    >
                      <td
                        className={`select-none border-r px-3 text-right align-top ${
                          inside ? "border-arch text-arch" : "border-code-line text-code-muted"
                        }`}
                      >
                        {number}
                      </td>
                      <td className="whitespace-pre px-3">{line || " "}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="flex items-center justify-between gap-3 border-t border-code-line px-3 py-2 text-xs text-code-muted">
            <span className="truncate">sha256 {file.sha256?.slice(0, 16)} · {file.line_count} lines</span>
            {link && (
              <a href={link} target="_blank" rel="noreferrer" className="shrink-0 hover:text-code-fg">
                Open on GitHub at this commit
              </a>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * A citation. Opens the viewer at its lines, or -- when RepoLab could not
 * verify it -- says so instead of pretending.
 */
export function SourceRef({
  reference,
  repoUrl,
  commitSha,
  reason,
  compact = false,
}: {
  reference: SourceTarget;
  /** For the host fallback when no viewer is available. */
  repoUrl?: string;
  commitSha?: string;
  reason?: string;
  compact?: boolean;
}) {
  const viewer = useSourceViewer();
  if (reference.verified === false && reference.verification) {
    return (
      <span
        className="inline-flex items-center gap-1.5 rounded-md border border-dashed border-line-strong px-2 py-0.5 text-xs text-fg-muted"
        title={reference.verification}
        data-testid="source-unverified"
      >
        <span className="font-mono">{reference.path.split("/").pop()}</span> · Source location could not be verified
      </span>
    );
  }
  const name = compact ? reference.path.split("/").pop() : reference.path;
  const range = rangeLabel(reference);
  const label = (
    <>
      <SourceIcon size={12} className="shrink-0 text-fg-muted" />
      <span className="truncate font-mono">{name}</span>
      {range && <span className="shrink-0 font-mono text-fg-muted">{range}</span>}
      {reference.symbol && !compact && <span className="shrink-0 text-arch-fg">{reference.symbol}</span>}
      {reference.verified && <CheckIcon size={12} className="shrink-0 text-run-fg" aria-label="verified" />}
    </>
  );
  const className =
    "inline-flex max-w-full items-center gap-1.5 rounded-md border border-line bg-surface px-2 py-0.5 text-xs text-fg transition-colors hover:border-line-strong hover:bg-surface-hover";
  const title = `${reference.path}${range ? ` ${range}` : ""}${reference.verification ? ` — ${reference.verification}` : ""}`;
  if (viewer) {
    return (
      <button
        type="button"
        onClick={() => viewer.open({ ...reference, reason: reason ?? null })}
        className={className}
        title={title}
        data-testid="source-ref"
      >
        {label}
      </button>
    );
  }
  const href = repoUrl && (reference.commit_sha || commitSha) ? hostUrl(repoUrl, reference.commit_sha || commitSha!, reference) : null;
  return href ? (
    <a href={href} target="_blank" rel="noreferrer" className={className} title={title} data-testid="source-ref">
      {label}
    </a>
  ) : (
    <span className={className} title={title} data-testid="source-ref">
      {label}
    </span>
  );
}
