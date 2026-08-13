import { useEffect, useRef } from "react";
import { CloseIcon } from "./icons";

/**
 * The preview popup. Everything a reader might want to check lives behind one of these:
 * the rows an answer came from, or the passage a citation points at.
 */
export function Preview({
  title,
  subtitle,
  onClose,
  children,
}: {
  title: string;
  subtitle?: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    // The page behind must not scroll while a preview is over it.
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    // aria-modal="true" tells a screen reader nothing outside this dialog exists, so focus
    // has to actually be inside it — otherwise the announcement and the caret disagree and
    // the first Tab walks the page behind. Focus returns to whatever opened it on close.
    const opener = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = previous;
      opener?.focus?.();
    };
  }, [onClose]);

  return (
    <div className="preview-backdrop" onClick={onClose} role="presentation">
      <div
        className="preview"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="preview-head">
          <div>
            <h3>{title}</h3>
            {subtitle && <div className="crumb">{subtitle}</div>}
          </div>
          <button
            ref={closeRef}
            className="preview-close"
            onClick={onClose}
            aria-label="Close preview"
          >
            <CloseIcon />
          </button>
        </header>
        <div className="preview-body">{children}</div>
      </div>
    </div>
  );
}
