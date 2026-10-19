/**
 * The workspace layout.
 *
 * A server component that resolves the route params — `params` is a Promise in
 * Next 16 — and hands the job id to the client shell, which owns the SSE
 * connection and the state every screen reads.
 */

import { WorkspaceShell } from "./workspace";

export default async function WorkspaceLayout({
  children,
  params,
}: LayoutProps<"/r/[jobId]">) {
  const { jobId } = await params;
  return <WorkspaceShell jobId={jobId}>{children}</WorkspaceShell>;
}
