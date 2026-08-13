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
  // Every caller passes a fresh arrow, so `onClose` has a new identity on each parent
  // render. With it in the dependency list the whole effect tore down and re-ran once
  // per streamed token — re-locking scroll and yanking focus back to the close button,
  // off whatever the reader was actually using inside the dialog. The ref keeps the
  // latest callback without making the effect depend on its identity.
  const closeHandler = useRef(onClose);
  closeHandler.current = onClose;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeHandler.current();
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
    // Mounts once: see closeHandler above.
  }, []);

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
