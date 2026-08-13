import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  deleteUpload,
  demoUsers,
  getThread,
  listUploads,
  loginAs,
  setToken,
  streamChat,
  upload,
} from "./api";
import { Admin } from "./components/Admin";
import { ApprovalCard } from "./components/ApprovalCard";
import {
  AssistantMark,
  CheckIcon,
  CopyIcon,
  PanelIcon,
  PlusIcon,
  SendIcon,
  SpinnerIcon,
  TrashIcon,
  UploadIcon,
} from "./components/icons";
import { Reply } from "./components/Reply";
import { StageTrail } from "./components/StageTrail";
import { useTheme, type ThemeChoice } from "./theme";
import type { AgentResponse, DemoUser, Turn, UploadRecord } from "./types";

const LAST_USER_KEY = "echomind.lastUser";
const RAIL_KEY = "echomind.rail";
const threadKey = (handle: string) => `echomind.thread.${handle}`;

/** Eight lines of the composer's own line-height, plus its padding. Kept in step with
 *  `--composer-max` in the stylesheet; the textarea scrolls past this rather than growing. */
const COMPOSER_MAX_PX = 208;

/** Below this the rail cannot sit beside the conversation, so it becomes a drawer. */
const WIDE = "(min-width: 900px)";

interface Suggestion {
  text: string;
  /** What the question demonstrates. Written by the UI, never sourced from an answer. */
  note: string;
}

/**
 * The four openers, in the order a first-time reader should meet them: find a place,
 * choose an instrument, see your own money, read the policy behind a charge.
 */
const BASE_SUGGESTIONS: Suggestion[] = [
  // No opener carries a location, so no answer to one can carry a distance. The note
  // said "with distance" and the card that came back never had one — a promise the
  // product broke in the first thing a new reader clicks.
  { text: "Where is the nearest core that can do cryo-EM?", note: "Facilities · where and what" },
  { text: "I want to image live cells — what should I use?", note: "Instruments · by technique" },
  { text: "What is on my March invoice?", note: "Invoice · your charges only" },
  {
    text: "What am I charged if I cancel a booking 12 hours before it starts?",
    note: "Policy · with the passage",
  },
];

/** Each identity keeps one opener that only makes sense as that person — the refusal for
 *  Bob, the lab-wide read for Asha, the approval for Cora. That is the demo's whole point. */
const SUGGESTIONS: Record<string, Suggestion[]> = {
  alice: BASE_SUGGESTIONS,
  bob: [
    ...BASE_SUGGESTIONS.slice(0, 3),
    { text: "Show me alice's bookings", note: "Permissions · refused, not answered" },
  ],
  asha: [
    ...BASE_SUGGESTIONS.slice(0, 2),
    { text: "Why was lab A charged $412 in March?", note: "Usage · traced to the bookings" },
    { text: "Show me lab A's usage this month", note: "Usage · your labs only" },
  ],
  cora: [
    ...BASE_SUGGESTIONS.slice(0, 2),
    {
      text: "Which instrument had the most downtime in March 2026?",
      note: "Usage · across all cores",
    },
    { text: "Generate the monthly summary for 2026-03", note: "Document · waits for approval" },
  ],
};

const THEME_LABELS: { value: ThemeChoice; label: string }[] = [
  { value: "system", label: "Auto" },
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
];

function ThemeToggle() {
  const [choice, setChoice] = useTheme();
  return (
    <div className="theme-toggle" role="group" aria-label="Colour theme">
      {THEME_LABELS.map((option) => (
        <button
          key={option.value}
          className={choice === option.value ? "is-on" : ""}
          aria-pressed={choice === option.value}
          onClick={() => setChoice(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

/**
 * The hover row under a finished reply.
 *
 * Copy is the only action here on purpose: everything else a reader might want — the
 * passage behind a claim, the rows behind a figure — already has a control of its own
 * inside the reply, and duplicating them out here would say they were afterthoughts.
 */
function TurnActions({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 1600);
    return () => window.clearTimeout(timer);
  }, [copied]);

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
    } catch {
      // Clipboard access is refused outside a secure context. Fall back to a selection
      // the reader can copy themselves rather than silently doing nothing.
      const area = document.createElement("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      try {
        setCopied(document.execCommand("copy"));
      } catch {
        setCopied(false);
      }
      document.body.removeChild(area);
    }
  }

  return (
    <div className="turn-actions">
      <button
        className="icon-btn icon-btn--sm"
        onClick={() => void copy()}
        aria-label={copied ? "Reply copied" : "Copy reply"}
        title={copied ? "Copied" : "Copy"}
      >
        {copied ? <CheckIcon /> : <CopyIcon />}
      </button>
      <span className="turn-actions-note" aria-live="polite">
        {copied ? "Copied" : ""}
      </span>
    </div>
  );
}

export default function App() {
  const [users, setUsers] = useState<DemoUser[]>([]);
  const [current, setCurrent] = useState<DemoUser | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [view, setView] = useState<"chat" | "admin">("chat");
  const [uploads, setUploads] = useState<UploadRecord[]>([]);
  const [uploadNote, setUploadNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [railOpen, setRailOpen] = useState(() => {
    if (typeof window === "undefined") return true;
    // Below the breakpoint the rail is a drawer over the conversation, so a remembered
    // "open" from a desktop session would greet a phone with the sidebar covering the
    // thing it came for. The preference is honoured only where the rail sits beside it.
    if (!window.matchMedia(WIDE).matches) return false;
    return localStorage.getItem(RAIL_KEY) !== "closed";
  });

  const fileRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const railRef = useRef<HTMLElement>(null);
  const railToggleRef = useRef<HTMLButtonElement>(null);
  /** False once the reader scrolls up: a transcript that yanks itself back down while
   *  someone is reading an earlier answer is unusable. */
  const stick = useRef(true);
  const turnCount = useRef(0);

  /* Where the rail overlays the conversation, anything chosen inside it has to close it:
     the drawer sits on top of the transcript and the composer, so a phone that switches
     identity and leaves the drawer up shows the reader the sidebar they just finished
     with, over the answer they came for, with no visible way back to typing. */
  const dismissDrawer = useCallback(() => {
    if (!window.matchMedia(WIDE).matches) setRailOpen(false);
  }, []);

  const switchUser = useCallback(async (handle: string, restore = false) => {
    const { token, user } = await loginAs(handle);
    setToken(token);
    setCurrent(user);
    setView("chat");
    setUploadNote(null);
    setError(null);
    dismissDrawer();

    // Switching identity starts a fresh thread (spec 07) — a conversation belongs to
    // the person who had it. A page refresh is different: the checkpointer still holds
    // the thread, so restore it rather than silently losing the conversation.
    const stored = restore ? localStorage.getItem(threadKey(handle)) : null;
    if (stored) {
      try {
        const snapshot = await getThread(stored);
        setThreadId(stored);
        setTurns(
          snapshot.message && snapshot.response
            ? [{ id: "restored", question: snapshot.message, response: snapshot.response }]
            : [],
        );
      } catch {
        localStorage.removeItem(threadKey(handle));
        setThreadId(null);
        setTurns([]);
      }
    } else {
      setThreadId(null);
      setTurns([]);
    }

    try {
      setUploads(await listUploads());
    } catch {
      setUploads([]);
    }
  }, [dismissDrawer]);

  useEffect(() => {
    void (async () => {
      const list = await demoUsers();
      setUsers(list);
      if (!list.length) return;
      const last = localStorage.getItem(LAST_USER_KEY);
      const handle = list.some((u) => u.handle === last) ? last! : list[0].handle;
      await switchUser(handle, true);
    })();
  }, [switchUser]);

  useEffect(() => {
    if (current) localStorage.setItem(LAST_USER_KEY, current.handle);
  }, [current]);

  useEffect(() => {
    if (!current) return;
    if (threadId) localStorage.setItem(threadKey(current.handle), threadId);
    else localStorage.removeItem(threadKey(current.handle));
  }, [threadId, current]);

  useEffect(() => {
    // Only the wide layout writes the preference. Opening the drawer on a phone is a
    // momentary thing — it should not be what the desktop remembers next time.
    if (!window.matchMedia(WIDE).matches) return;
    localStorage.setItem(RAIL_KEY, railOpen ? "open" : "closed");
  }, [railOpen]);

  /* The rail is marked aria-hidden once it is shut, and the button that shuts it lives
     inside it — so focus has to be handed to the button that reopens it. Leaving it on a
     hidden element strands a keyboard reader on a control screen readers can no longer
     see, one tab short of the conversation. */
  useEffect(() => {
    if (railOpen) return;
    const active = document.activeElement;
    if (active instanceof HTMLElement && railRef.current?.contains(active)) {
      railToggleRef.current?.focus();
    }
  }, [railOpen]);

  /* The transcript follows the answer as it streams, but only while the reader is already
     at the bottom. A new question always jumps: they just asked it. */
  useEffect(() => {
    const el = scrollRef.current;
    if (!el || !stick.current) return;
    const isNewTurn = turns.length !== turnCount.current;
    turnCount.current = turns.length;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    el.scrollTo({
      top: el.scrollHeight,
      behavior: isNewTurn && !reduced ? "smooth" : "auto",
    });
  }, [turns]);

  /* The composer grows with the draft and then scrolls — measured rather than guessed,
     because a wrapped line is not a newline and counting "\n" gets it wrong. */
  useLayoutEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, COMPOSER_MAX_PX)}px`;
  }, [draft, sending]);

  useEffect(() => {
    if (!sending && view === "chat") textareaRef.current?.focus();
  }, [sending, view]);

  function onTranscriptScroll() {
    const el = scrollRef.current;
    if (!el) return;
    stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 140;
  }

  async function send(message: string) {
    if (!message.trim() || sending) return;
    const id = `${Date.now()}`;
    setDraft("");
    setSending(true);
    stick.current = true;
    setTurns((prev) => [
      ...prev,
      { id, question: message, streaming: true, streamedText: "", stages: [] },
    ]);

    const patch = (fn: (turn: Turn) => Turn) =>
      setTurns((prev) => prev.map((t) => (t.id === id ? fn(t) : t)));

    await streamChat(message, threadId, {
      // The trail keeps every stage rather than replacing one with the next: the point of
      // showing the work is that the reader can see the access check happened, and a
      // stage that flashes past for 400ms was never seen.
      onStatus: (stage) =>
        patch((t) => {
          const stages = t.stages ?? [];
          if (stages[stages.length - 1] === stage) return t;
          return { ...t, stages: [...stages, stage] };
        }),
      onToken: (text) => patch((t) => ({ ...t, streamedText: (t.streamedText ?? "") + text })),
      onFinal: (response: AgentResponse) => {
        if (response.thread_id) setThreadId(response.thread_id);
        patch((t) => ({ ...t, response, streaming: false, stages: undefined }));
      },
      onError: (msg) => patch((t) => ({ ...t, streaming: false, error: msg })),
    });
    setSending(false);
  }

  async function onUpload(file: File) {
    try {
      const result = await upload(file);
      setUploadNote(result.note);
      setUploads(await listUploads());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed.");
    }
  }

  function newConversation() {
    setTurns([]);
    setThreadId(null);
    setView("chat");
    stick.current = true;
    dismissDrawer();
  }

  const isAdmin = current?.role === "admin";
  const suggestions = SUGGESTIONS[current?.handle ?? ""] ?? BASE_SUGGESTIONS;

  return (
    <div className={`app${railOpen ? " rail-open" : ""}`}>
      <aside
        className="rail"
        id="rail"
        ref={railRef}
        aria-label="Workspace"
        aria-hidden={!railOpen}
      >
        <div className="rail-top">
          <div className="brand">
            <span className="brand-mark" aria-hidden="true" />
            <span className="brand-name">
              <strong>EchoMind</strong>
              <span>Infinity X</span>
            </span>
          </div>
          <button
            className="icon-btn"
            onClick={() => setRailOpen(false)}
            aria-label="Hide sidebar"
            title="Hide sidebar"
            tabIndex={railOpen ? 0 : -1}
          >
            <PanelIcon />
          </button>
        </div>

        <button className="rail-new" onClick={newConversation} tabIndex={railOpen ? 0 : -1}>
          <PlusIcon />
          New conversation
        </button>

        <div className="rail-scroll">
          <div className="rail-section">
            <h2>Signed in as</h2>
            {users.map((u) => (
              <button
                key={u.handle}
                className={`user-btn ${current?.handle === u.handle ? "active" : ""}`}
                aria-pressed={current?.handle === u.handle}
                onClick={() => void switchUser(u.handle)}
                tabIndex={railOpen ? 0 : -1}
              >
                <span className="who">
                  <strong>{u.name}</strong>
                  <span className={`role role--${u.role}`}>{u.role}</span>
                </span>
                <span className="blurb">{u.blurb}</span>
              </button>
            ))}
          </div>

          <div className="rail-section">
            <h2>Your documents</h2>
            <div className="uploads-list">
              {uploads.map((u) => (
                <div className="upload-row" key={u.id}>
                  <span title={u.title}>{u.title}</span>
                  <button
                    aria-label={`Delete ${u.title}`}
                    title="Delete"
                    tabIndex={railOpen ? 0 : -1}
                    onClick={async () => {
                      await deleteUpload(u.id);
                      setUploads(await listUploads());
                    }}
                  >
                    <TrashIcon />
                  </button>
                </div>
              ))}
              {!uploads.length && <div className="uploads-empty">Nothing uploaded yet.</div>}
            </div>
            <input
              ref={fileRef}
              type="file"
              accept=".md,.txt,.pdf"
              style={{ display: "none" }}
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void onUpload(f);
                e.target.value = "";
              }}
            />
            <button
              className="link-btn"
              onClick={() => fileRef.current?.click()}
              tabIndex={railOpen ? 0 : -1}
            >
              <UploadIcon />
              Upload a document
            </button>
            {uploadNote && <div className="private-note">{uploadNote}</div>}
          </div>
        </div>

        <div className="rail-actions">
          {isAdmin && (
            <button
              className="link-btn subtle"
              onClick={() => {
                setView(view === "admin" ? "chat" : "admin");
                dismissDrawer();
              }}
              tabIndex={railOpen ? 0 : -1}
            >
              {view === "admin" ? "← Back to chat" : "Admin console"}
            </button>
          )}
        </div>
      </aside>

      {/* Only ever visible where the rail overlays the conversation. */}
      <div
        className="rail-scrim"
        onClick={() => setRailOpen(false)}
        role="presentation"
        hidden={!railOpen}
      />

      <main className="main">
        <header className="topbar">
          <div className="topbar-left">
            {!railOpen && (
              <button
                className="icon-btn"
                ref={railToggleRef}
                onClick={() => setRailOpen(true)}
                aria-label="Show sidebar"
                aria-expanded={railOpen}
                aria-controls="rail"
                title="Show sidebar"
              >
                <PanelIcon />
              </button>
            )}
            <div className="ctx">
              {current ? (
                <>
                  <span className="ctx-name">{current.name}</span>
                  <span className={`role role--${current.role}`}>{current.role}</span>
                  {current.lab_ids.length > 0 && (
                    <span className="ctx-labs">{current.lab_ids.join(" · ")}</span>
                  )}
                </>
              ) : (
                <span className="ctx-name">Loading…</span>
              )}
            </div>
          </div>
          <div className="topbar-right">
            <span className="thread-id" title="Conversation id">
              {threadId ? threadId : "new thread"}
            </span>
            <ThemeToggle />
            <button
              className="icon-btn"
              onClick={newConversation}
              aria-label="New conversation"
              title="New conversation"
            >
              <PlusIcon />
            </button>
          </div>
        </header>

        {view === "admin" ? (
          <Admin />
        ) : (
          <div className="transcript" ref={scrollRef} onScroll={onTranscriptScroll}>
            <div className="thread">
              {error && (
                <div className="error-banner" role="alert">
                  {error}
                </div>
              )}

              {!turns.length && (
                <div className="empty">
                  <span className="empty-mark" aria-hidden="true">
                    <AssistantMark />
                  </span>
                  <h1>What can I look up for you{current ? `, ${current.name.split(" ")[0]}` : ""}?</h1>
                  <p>
                    Every fact comes from the platform's own records. If it can't be
                    verified, you'll be told so rather than guessed at.
                  </p>
                  <div className="suggestions">
                    {suggestions.map((s) => (
                      <button key={s.text} onClick={() => void send(s.text)}>
                        <span className="suggestion-note">{s.note}</span>
                        <span className="suggestion-text">{s.text}</span>
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {turns.map((turn, index) => (
                <article className="turn" key={turn.id}>
                  <div className="turn-user">
                    <div className="user-msg">{turn.question}</div>
                  </div>

                  <div className={`turn-assistant${turn.streaming ? " is-streaming" : ""}`}>
                    <span className="turn-mark" aria-hidden="true">
                      <AssistantMark />
                    </span>
                    <div className="turn-body">
                      {turn.streaming && (
                        <div className="reply reply--working">
                          {turn.streamedText ? (
                            <p className="reply-text is-streaming">{turn.streamedText}</p>
                          ) : (
                            <StageTrail stages={turn.stages ?? []} />
                          )}
                        </div>
                      )}

                      {turn.error && (
                        <div className="error-banner" role="alert">
                          {turn.error}
                        </div>
                      )}

                      {turn.response && (
                        <>
                          <Reply response={turn.response} onSend={(text) => void send(text)} />
                          {turn.response.pending_action && (
                            <ApprovalCard
                              action={turn.response.pending_action}
                              decision={turn.decision}
                              /* "actually make it 3 hours" does not retire the two-hour
                                 proposal above it: that action is still pending and its
                                 Approve button still books two hours. Until the server
                                 supersedes it, the card at least has to stop looking
                                 like the live one. */
                              earlier={turns.some(
                                (later, j) =>
                                  j > index && later.response?.pending_action && !later.decision,
                              )}
                              onDecided={(status, text, actionId) =>
                                setTurns((prev) =>
                                  prev.map((t) =>
                                    t.id === turn.id
                                      ? { ...t, decision: { status, text, actionId } }
                                      : t,
                                  ),
                                )
                              }
                            />
                          )}
                          <TurnActions text={turn.response.text} />
                        </>
                      )}
                    </div>
                  </div>
                </article>
              ))}
            </div>

            <div className="dock">
              <form
                className={`composer${sending ? " is-busy" : ""}`}
                onSubmit={(e) => {
                  e.preventDefault();
                  void send(draft);
                }}
              >
                <textarea
                  ref={textareaRef}
                  value={draft}
                  rows={1}
                  aria-label="Ask a question"
                  disabled={sending}
                  placeholder={
                    sending ? "Checking the records…" : "Ask about facilities, instruments, bookings, billing, policies…"
                  }
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    // isComposing guards the IME: Enter mid-composition commits a
                    // candidate, it does not mean "send".
                    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                      e.preventDefault();
                      void send(draft);
                    }
                  }}
                />
                <button
                  className="send-btn"
                  type="submit"
                  disabled={sending || !draft.trim()}
                  aria-label={sending ? "Working" : "Send"}
                  title={sending ? "Working…" : "Send"}
                >
                  {sending ? <SpinnerIcon /> : <SendIcon />}
                </button>
              </form>
              <p className="hint">
                Verified or silent — answers carry their sources, and write actions wait
                for your approval.
              </p>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
