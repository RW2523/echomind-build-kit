import { useEffect, useState } from "react";
import { copyText } from "../clipboard";
import { CheckIcon, CopyIcon } from "./icons";

/**
 * Copy, with the one thing that makes it usable: it says whether it worked.
 *
 * A copy button that looks the same before and after is a button the reader has to test
 * by pasting somewhere else. The tick is the receipt — and because the clipboard can be
 * refused outright, a failure keeps the button in its resting state rather than claiming
 * a copy that did not happen.
 */
export function CopyButton({
  value,
  what,
  className = "copy-btn",
}: {
  value: string;
  /** Named in the button's label: "Copy the query", "Copy source 2". */
  what: string;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 1600);
    return () => window.clearTimeout(timer);
  }, [copied]);

  return (
    <button
      type="button"
      className={className}
      onClick={() => void copyText(value).then(setCopied)}
      aria-label={copied ? `${what} copied` : `Copy ${what}`}
      title={copied ? "Copied" : `Copy ${what}`}
    >
      {copied ? <CheckIcon /> : <CopyIcon />}
      <span className="copy-btn-label">{copied ? "Copied" : "Copy"}</span>
    </button>
  );
}
