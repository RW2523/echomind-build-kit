/**
 * Line icons, drawn inline.
 *
 * No icon font and no emoji: an emoji is a picture of a feeling and this tool reports
 * instrument state. Everything here is a 16px stroke drawing on a 24-unit grid that
 * inherits `currentColor`, so an icon can never disagree with the text beside it.
 */

type Props = { className?: string };

function svg(children: React.ReactNode, { className }: Props) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  );
}

export const PanelIcon = (p: Props) =>
  svg(
    <>
      <rect x="3" y="4.5" width="18" height="15" rx="2.5" />
      <path d="M9.5 4.5v15" />
    </>,
    p,
  );

export const PlusIcon = (p: Props) => svg(<path d="M12 5v14M5 12h14" />, p);

export const UploadIcon = (p: Props) =>
  svg(
    <>
      <path d="M12 16V4.5" />
      <path d="M7.5 9 12 4.5 16.5 9" />
      <path d="M4.5 15.5v2.5a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2v-2.5" />
    </>,
    p,
  );

export const CopyIcon = (p: Props) =>
  svg(
    <>
      <rect x="9" y="9" width="11" height="11" rx="2" />
      <path d="M15 6.5A2.5 2.5 0 0 0 12.5 4H6a2 2 0 0 0-2 2v6.5A2.5 2.5 0 0 0 6.5 15" />
    </>,
    p,
  );

export const CheckIcon = (p: Props) => svg(<path d="m5 12.5 4.5 4.5L19 7" />, p);

export const SendIcon = (p: Props) =>
  svg(
    <>
      <path d="M12 19V5.5" />
      <path d="m6 11.5 6-6 6 6" />
    </>,
    p,
  );

export const CloseIcon = (p: Props) => svg(<path d="M6 6l12 12M18 6 6 18" />, p);

export const TrashIcon = (p: Props) =>
  svg(
    <>
      <path d="M4.5 7h15" />
      <path d="M9.5 7V5.5A1.5 1.5 0 0 1 11 4h2a1.5 1.5 0 0 1 1.5 1.5V7" />
      <path d="M6.5 7l.8 11a2 2 0 0 0 2 1.9h5.4a2 2 0 0 0 2-1.9L17.5 7" />
    </>,
    p,
  );

export const ShieldIcon = (p: Props) =>
  svg(
    <>
      <path d="M12 3.5 5 6v6c0 4 3 7 7 8.5 4-1.5 7-4.5 7-8.5V6l-7-2.5Z" />
      <path d="m9 12 2 2 4-4" />
    </>,
    p,
  );

/**
 * The assistant's mark: an aperture, not a spark.
 *
 * The same six-blade figure the brand mark uses, so the thing signing each reply is
 * visibly the same instrument named in the corner.
 */
export function AssistantMark({ className }: Props) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
      focusable="false"
    >
      <circle cx="12" cy="12" r="8.25" stroke="currentColor" strokeWidth="1.5" />
      <circle cx="12" cy="12" r="3" fill="currentColor" />
      <path
        d="M12 3.75v3M12 17.25v3M3.75 12h3M17.25 12h3"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
  );
}

/** Determinate-looking but indeterminate: it says "in flight", never "83% done". */
export const SpinnerIcon = (p: Props) => (
  <svg className={`spinner ${p.className ?? ""}`} viewBox="0 0 24 24" aria-hidden="true">
    <circle cx="12" cy="12" r="8" fill="none" stroke="currentColor" strokeWidth="2" opacity="0.28" />
    <path
      d="M20 12a8 8 0 0 0-8-8"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
    />
  </svg>
);
