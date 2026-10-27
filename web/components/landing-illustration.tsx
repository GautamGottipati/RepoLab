/**
 * The landing page's hero illustration: a repository becoming a running,
 * explorable system.
 *
 *   GitHub repo → $ spark-submit → Live Playground → Driver → Executors
 *
 * Purely decorative (`aria-hidden`): it shows the idea of RepoLab, not data
 * from any investigation, so it carries no numbers or results. Built from SVG
 * and HTML so it stays sharp at any size and needs no image assets. Colours
 * follow the product's meanings: emerald for something running, cyan for
 * architecture, violet for the AI layer.
 */

export function LandingIllustration({ className = "" }: { className?: string }) {
  return (
    <div aria-hidden className={`relative h-[420px] w-[480px] select-none ${className}`}>
      {/* Soft glows behind the cards. */}
      <div className="absolute left-[200px] top-[90px] size-64 rounded-full bg-indigo-500/20 blur-3xl" />
      <div className="absolute left-[60px] top-[250px] size-48 rounded-full bg-cyan-500/10 blur-3xl" />

      {/* Connectors, under the cards. */}
      <svg className="absolute inset-0" width="480" height="420" viewBox="0 0 480 420" fill="none">
        <defs>
          <linearGradient id="landing-line" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#818cf8" stopOpacity="0.9" />
            <stop offset="1" stopColor="#22d3ee" stopOpacity="0.7" />
          </linearGradient>
        </defs>
        <g stroke="url(#landing-line)" strokeWidth="1.5" strokeLinecap="round">
          {/* repo → terminal */}
          <path d="M132 92 C 175 92, 190 52, 228 52" />
          {/* repo → playground */}
          <path d="M120 132 C 140 200, 200 190, 252 180" />
          {/* playground → driver */}
          <path d="M330 248 L 330 300 L 236 300 L 236 312" />
          {/* driver → executors */}
          <path d="M282 334 L 330 334" />
          <path d="M330 334 L 330 300" strokeDasharray="0" />
          <path d="M330 334 L 388 334 L 388 320" />
          <path d="M330 334 L 330 362 L 352 362" />
        </g>
        {/* Data flowing along the lines; still when motion is reduced. */}
        <g className="motion-safe:animate-pulse">
          <circle cx="228" cy="52" r="3" fill="#a5b4fc" />
          <circle cx="252" cy="180" r="3" fill="#a5b4fc" />
          <circle cx="330" cy="334" r="3.5" fill="#67e8f9" />
        </g>
      </svg>

      {/* The repository. */}
      <div className="absolute left-[40px] top-[44px] flex size-[92px] -rotate-6 items-center justify-center rounded-2xl border border-slate-600/70 bg-slate-900/90 shadow-[0_0_40px_-10px_rgba(129,140,248,0.6)]">
        <GitHubMark />
      </div>

      {/* A command, run for real. */}
      <div className="absolute left-[228px] top-[22px] w-[224px] rounded-xl border border-slate-600/70 bg-slate-950/90 px-4 py-3 shadow-lg">
        <div className="mb-2 flex gap-1.5">
          <span className="size-2 rounded-full bg-slate-600" />
          <span className="size-2 rounded-full bg-slate-600" />
          <span className="size-2 rounded-full bg-slate-600" />
        </div>
        <p className="font-mono text-[13px] text-slate-200">
          <span className="text-emerald-400">$</span> spark-submit job.py
          <span className="ml-0.5 inline-block h-3.5 w-1.5 translate-y-0.5 bg-slate-300 motion-safe:animate-pulse" />
        </p>
      </div>

      {/* A live, interactive experience. */}
      <div className="absolute left-[252px] top-[110px] w-[212px] rounded-2xl border border-indigo-400/50 bg-gradient-to-b from-indigo-500/25 to-slate-900/95 p-4 shadow-[0_0_50px_-12px_rgba(99,102,241,0.8)]">
        <div className="flex items-center justify-between">
          <p className="text-sm font-semibold text-white">Live Playground</p>
          <span className="flex items-center gap-1 text-[11px] font-medium text-emerald-300">
            <span className="size-1.5 rounded-full bg-emerald-400" />
            live
          </span>
        </div>
        <div className="mt-4 flex justify-center">
          <span className="flex size-14 items-center justify-center rounded-full bg-emerald-500/20 ring-1 ring-emerald-400/50">
            <svg width="20" height="20" viewBox="0 0 16 16">
              <path d="M5 3v10l8.5-5Z" fill="#6ee7b7" />
            </svg>
          </span>
        </div>
        <div className="mt-4 flex gap-1.5">
          <span className="h-1.5 flex-1 rounded-full bg-slate-700" />
          <span className="h-1.5 w-8 rounded-full bg-indigo-400/70" />
        </div>
      </div>

      {/* Its architecture, as it runs. */}
      <Node className="left-[190px] top-[312px] border-cyan-400/60 bg-cyan-500/20 text-cyan-100">Driver</Node>
      <Node className="left-[352px] top-[300px] border-violet-400/60 bg-violet-500/25 text-violet-100">Executor</Node>
      <Node className="left-[352px] top-[348px] border-indigo-400/60 bg-indigo-500/25 text-indigo-100">Executor</Node>
    </div>
  );
}

function Node({ className, children }: { className: string; children: React.ReactNode }) {
  return (
    <div
      className={`absolute flex h-[44px] w-[92px] items-center justify-center rounded-xl border text-sm font-medium shadow-lg backdrop-blur ${className}`}
    >
      {children}
    </div>
  );
}

function GitHubMark() {
  return (
    <svg width="44" height="44" viewBox="0 0 16 16" fill="#e2e8f0">
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8Z" />
    </svg>
  );
}
