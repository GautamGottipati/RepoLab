/**
 * The handful of icons RepoLab uses, inline so no icon library is needed.
 *
 * Every icon is decorative (`aria-hidden`): meaning is always carried by the
 * text next to it.
 */

import type { ReactNode, SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement> & { size?: number };

function Svg({ size = 16, children, ...rest }: IconProps & { children: ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      focusable={false}
      {...rest}
    >
      {children}
    </svg>
  );
}

/** Ask / AI explanation. */
export const AskIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M8 1.75 9.4 5.6 13.25 7 9.4 8.4 8 12.25 6.6 8.4 2.75 7 6.6 5.6Z" />
    <path d="M12.5 11.5 13 13l1.5.5-1.5.5-.5 1.5-.5-1.5-1.5-.5 1.5-.5Z" />
  </Svg>
);

/** Architecture: connected components. */
export const ArchIcon = (p: IconProps) => (
  <Svg {...p}>
    <rect x="1.75" y="1.75" width="4.5" height="4.5" rx="1" />
    <rect x="9.75" y="9.75" width="4.5" height="4.5" rx="1" />
    <path d="M6.25 4h3.5a2 2 0 0 1 2 2v3.75" />
  </Svg>
);

/** Run / experience. */
export const RunIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4.5 2.75v10.5L13 8Z" fill="currentColor" stroke="none" />
  </Svg>
);

/** Experiment. */
export const ExperimentIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M6 1.75h4M6.75 1.75v4.5L2.6 12.9a1 1 0 0 0 .86 1.35h9.08a1 1 0 0 0 .86-1.35L9.25 6.25v-4.5" />
    <path d="M4.5 10h7" />
  </Svg>
);

/** Source code. */
export const SourceIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="m5.5 4.5-3.5 3.5 3.5 3.5M10.5 4.5 14 8l-3.5 3.5" />
  </Svg>
);

export const CheckIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="m3 8.5 3 3 7-7" />
  </Svg>
);

export const CrossIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="m4 4 8 8M12 4l-8 8" />
  </Svg>
);

export const ArrowRightIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M3 8h10M9 4l4 4-4 4" />
  </Svg>
);

export const ChevronDownIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="m4 6 4 4 4-4" />
  </Svg>
);

export const StarIcon = (p: IconProps) => (
  <Svg {...p}>
    <path
      d="m8 1.75 1.9 3.85 4.25.62-3.08 3 .73 4.23L8 11.45l-3.8 2 .73-4.23-3.08-3 4.25-.62Z"
      fill="currentColor"
      stroke="none"
    />
  </Svg>
);

export const InfoIcon = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="8" cy="8" r="6.25" />
    <path d="M8 7.25v4M8 4.75v.01" />
  </Svg>
);

export const ActivityIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M1.75 8h2.5l2-4.5 3.5 9 2-4.5h2.5" />
  </Svg>
);

export const CopyIcon = (p: IconProps) => (
  <Svg {...p}>
    <rect x="5.25" y="5.25" width="8.5" height="8.5" rx="1.5" />
    <path d="M10.75 5.25V3.5a1.25 1.25 0 0 0-1.25-1.25h-6A1.25 1.25 0 0 0 2.25 3.5v6a1.25 1.25 0 0 0 1.25 1.25h1.75" />
  </Svg>
);
