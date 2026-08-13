import type { Citation } from "./types";

/**
 * Copying, in one place.
 *
 * The fallback matters more than it looks. `navigator.clipboard` is refused outside a
 * secure context, and this demo is routinely opened over a plain-http tunnel — so on the
 * machine it is most often shown on, the copy button would silently do nothing. The
 * selection fallback is what makes the control honest about having worked.
 */
export async function copyText(value: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(value);
    return true;
  } catch {
    const area = document.createElement("textarea");
    area.value = value;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    let copied = false;
    try {
      copied = document.execCommand("copy");
    } catch {
      copied = false;
    }
    document.body.removeChild(area);
    return copied;
  }
}

/**
 * A citation on the clipboard, as something that can be pasted into a message and still
 * be checked: what it is called, where in the document it sits, and the passage itself.
 *
 * The passage is included verbatim and nothing is summarised — a quotation that has been
 * tightened is no longer the source it names. When the passage has not loaded (or was
 * refused), the reference is still worth copying and says plainly that the text is not
 * part of it, rather than pasting a heading that looks like it came with evidence.
 */
export function citationCopyText(citation: Citation, body: string): string {
  const head = `[${citation.index}] ${citation.title}`;
  const crumb = citation.breadcrumb.trim();
  const passage = body.trim();
  const lines = [crumb ? `${head} — ${crumb}` : head, ""];
  lines.push(passage === "" ? "(passage not loaded)" : passage);
  return lines.join("\n");
}
