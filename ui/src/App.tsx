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
import { DataSpaces } from "./components/DataSpaces";
import { Library } from "./components/Library";
import { ApprovalCard } from "./components/ApprovalCard";
import {
  ArrowDownIcon,
  AssistantMark,
  CheckIcon,
  CopyIcon,
  PanelIcon,
  PlusIcon,
  SendIcon,
  StopIcon,
  TrashIcon,
  UploadIcon,
} from "./components/icons";
import { Reply } from "./components/Reply";
import { StageTrail } from "./components/StageTrail";
import { copyText } from "./clipboard";
import {
  composerIntent,
  recall,
  shouldStickToBottom,
  shouldStopStream,
  type KeyLike,
} from "./composer";
import { openersFor } from "./openers";
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
 * A keyboard event as the decision functions want it.
 *
 * Written out field by field rather than spread: `key`, `shiftKey` and the rest live on
 * KeyboardEvent's prototype, so `{...event}` copies none of them and every rule would
 * silently see `undefined` — a bug that looks exactly like "the shortcut does nothing".
 */
function keyLike(event: KeyboardEvent): KeyLike {
  return {
    key: event.key,
    shiftKey: event.shiftKey,
    altKey: event.altKey,
    ctrlKey: event.ctrlKey,
    metaKey: event.metaKey,
    isComposing: event.isComposing,
  };
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

  return (
    <div className="turn-actions">
      <button
        className="icon-btn icon-btn--sm"
        onClick={() => void copyText(text).then(setCopied)}
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
  const [view, setView] = useState<"chat" | "admin" | "library" | "dataspaces">("chat");
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

  /** Where the reader is in their own history, or null while writing something new. */
  const [recallCursor, setRecallCursor] = useState<number | null>(null);
  /** Mirrors `stick` for rendering only. The ref stays the authority for the scroll
   *  effect — reading React state there would act on the value from the previous frame. */
  const [followingTail, setFollowingTail] = useState(true);

  const fileRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const railRef = useRef<HTMLElement>(null);
  const railToggleRef = useRef<HTMLButtonElement>(null);
  const stopRef = useRef<HTMLButtonElement>(null);
  /** The turn in flight, so Escape and the Stop button can end it. */
  const streamRef = useRef<{ id: string; controller: AbortController } | null>(null);
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

  /** Drops the turn in flight without saying anything about it — for when the transcript
   *  it belonged to is going away. Stopping is a statement to the reader; this is not. */
  const abandonStream = useCallback(() => {
    streamRef.current?.controller.abort();
    streamRef.current = null;
  }, []);

  const switchUser = useCallback(async (handle: string, restore = false) => {
    // Whatever is still arriving belongs to the person who asked it, and they are about
    // to stop being the person on screen.
    abandonStream();
    const { token, user } = await loginAs(handle);
    setToken(token);
    setCurrent(user);
    setView("chat");
    setUploadNote(null);
    setError(null);
    setRecallCursor(null);
    setFollowingTail(true);
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
            ? [{
                id: "restored",
                question: snapshot.message,
                response: snapshot.response,
                // The server says whether the action is still waiting; without this the
                // restored turn drew a live approval card — "Awaiting your approval",
                // "Nothing is written until you approve" — over a booking that had
                // already been made, with buttons that posted to /actions/undefined.
                // A refresh is the most ordinary thing a person does, and it was turning
                // a completed write back into a decision they appeared not to have taken.
                //
                // The status is deliberately vague: the snapshot records that the action
                // was decided, not which way, so the card says "decided" rather than
                // guessing "executed" and claiming something happened that may have been
                // declined.
                decision: snapshot.response.pending_action && !snapshot.awaiting_approval
                  ? {
                      status: "decided",
                      text: undefined,
                      actionId: snapshot.response.pending_action.action_id ?? "",
                    }
                  : undefined,
              }]
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
  }, [dismissDrawer, abandonStream]);

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
    if (sending || view !== "chat") return;
    // Not while a preview is over the conversation. Opening a source or the evidence
    // table while a turn streams is an ordinary thing to do, and when the turn finished
    // this pulled focus out of the dialog and into the composer behind it — so a whole
    // message could be typed and sent from a field the reader could not see, under a
    // modal that still said aria-modal="true".
    if (document.querySelector('[role="dialog"][aria-modal="true"]')) return;
    textareaRef.current?.focus();
  }, [sending, view]);

  function onTranscriptScroll() {
    const el = scrollRef.current;
    if (!el) return;
    const following = shouldStickToBottom(el);
    stick.current = following;
    setFollowingTail((was) => (was === following ? was : following));
  }

  function jumpToLatest() {
    const el = scrollRef.current;
    if (!el) return;
    stick.current = true;
    setFollowingTail(true);
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }

  /* Ends the turn in flight. The stream stops at once; the lookup behind it is already
     running on the server and will finish there, so the turn is marked "stopped" rather
     than "cancelled" — the difference matters in a product whose whole claim is that it
     does not say more than it knows. Nothing can have been written: a write only ever
     happens through the approval card, which this turn never reached. */
  const stopStreaming = useCallback(() => {
    const live = streamRef.current;
    if (!live) return;
    streamRef.current = null;
    live.controller.abort();
    setTurns((prev) =>
      prev.map((t) =>
        t.id === live.id ? { ...t, streaming: false, stages: undefined, stopped: true } : t,
      ),
    );
  }, []);

  /* Escape reaches the turn from wherever the reader's focus happens to be — including
     the composer, which is disabled while a turn runs and so cannot receive the key
     itself. A preview open over the conversation takes it first; see shouldStopStream. */
  useEffect(() => {
    if (!sending) return;
    const onKey = (e: KeyboardEvent) => {
      const open = document.querySelector('[role="dialog"]') !== null;
      if (!shouldStopStream(keyLike(e), { sending, dialogOpen: open })) return;
      e.preventDefault();
      stopStreaming();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [sending, stopStreaming]);

  /* The composer is disabled while the turn runs, and disabling a focused element blurs
     it — leaving a keyboard reader at the top of the document, one tab away from a Stop
     button they cannot see. Focus follows the control that replaced it, and the effect
     below hands it back to the composer when the turn ends. */
  useEffect(() => {
    if (sending) stopRef.current?.focus();
  }, [sending]);

  async function send(message: string) {
    if (!message.trim() || sending) return;
    const id = `${Date.now()}`;
    const controller = new AbortController();
    streamRef.current = { id, controller };
    setDraft("");
    setRecallCursor(null);
    setSending(true);
    stick.current = true;
    setFollowingTail(true);
    setTurns((prev) => [
      ...prev,
      { id, question: message, streaming: true, streamedText: "", stages: [] },
    ]);

    const patch = (fn: (turn: Turn) => Turn) =>
      setTurns((prev) => prev.map((t) => (t.id === id ? fn(t) : t)));

    try {
      await streamChat(
        message,
        threadId,
        {
          // The trail keeps every stage rather than replacing one with the next: the point
          // of showing the work is that the reader can see the access check happened, and a
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
          // stopStreaming has already marked the turn; there is nothing to report.
          onAborted: () => undefined,
        },
        controller.signal,
      );
    } finally {
      // In a finally because an exception on the way out used to leave `sending` true
      // for good: the composer stayed disabled and the only way back was a reload.
      if (streamRef.current?.id === id) streamRef.current = null;
      setSending(false);
    }
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
    // A turn whose transcript is about to be thrown away has nowhere to arrive. Left
    // running it goes on streaming an answer into a conversation that no longer exists.
    abandonStream();
    setTurns([]);
    setThreadId(null);
    setView("chat");
    setRecallCursor(null);
    setFollowingTail(true);
    stick.current = true;
    dismissDrawer();
  }

  const isAdmin = current?.role === "admin";
  const suggestions = openersFor(current);
  /** What this person has already asked, oldest first — the list Up walks back through. */
  const asked = turns.map((t) => t.question);

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
          {/* Not gated on role: the point of the shelf is that anyone can see what the
              assistant reads, and the list is already filtered to what they may read. */}
          <button
            className="link-btn subtle"
            onClick={() => {
              setView(view === "library" ? "chat" : "library");
              dismissDrawer();
            }}
            tabIndex={railOpen ? 0 : -1}
          >
            {view === "library" ? "← Back to chat" : "Resources"}
          </button>
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
          {/* Gated on role in both places that matter: the button is hidden here and every
              endpoint behind it answers 404 to anyone who is not an admin. Only the second
              one is a control. */}
          {isAdmin && (
            <button
              className="link-btn subtle"
              onClick={() => {
                setView(view === "dataspaces" ? "chat" : "dataspaces");
                dismissDrawer();
              }}
              tabIndex={railOpen ? 0 : -1}
            >
              {view === "dataspaces" ? "← Back to chat" : "Data & tools"}
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
        ) : view === "dataspaces" ? (
          <DataSpaces />
        ) : view === "library" ? (
          <Library />
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
                          {/* The trail stays on screen once the prose starts arriving
                              rather than being replaced by it: what the turn is doing is
                              worth seeing for the whole turn, and a reader who looked away
                              during the checks should not find the record of them gone. */}
                          <StageTrail
                            stages={turn.stages ?? []}
                            compact={Boolean(turn.streamedText)}
                          />
                          {turn.streamedText && (
                            <p className="reply-text is-streaming">{turn.streamedText}</p>
                          )}
                        </div>
                      )}

                      {turn.stopped && (
                        <div className="reply reply--stopped" role="status">
                          Stopped — the answer was not shown. The lookup may already have
                          finished on the server; nothing is ever written without your
                          approval.
                        </div>
                      )}

                      {turn.error && (
                        <div className="error-banner" role="alert">
                          {turn.error}
                        </div>
                      )}

                      {turn.response && (
                        <>
                          <Reply
                            response={turn.response}
                            onSend={(text) => void send(text)}
                            busy={sending}
                          />
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
              {/* Only while the reader has left the tail. It is a way back, not a nag. */}
              {!followingTail && turns.length > 0 && (
                <button className="jump-latest" type="button" onClick={jumpToLatest}>
                  <ArrowDownIcon />
                  Jump to latest
                </button>
              )}
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
                  onChange={(e) => {
                    setDraft(e.target.value);
                    // Typing leaves the history walk: from here Up is a caret key again,
                    // so an edited draft cannot be swallowed by the next Up.
                    setRecallCursor(null);
                  }}
                  onKeyDown={(e) => {
                    const intent = composerIntent(
                      {
                        key: e.key,
                        shiftKey: e.shiftKey,
                        altKey: e.altKey,
                        ctrlKey: e.ctrlKey,
                        metaKey: e.metaKey,
                        // isComposing guards the IME: Enter mid-composition commits a
                        // candidate, it does not mean "send".
                        isComposing: e.nativeEvent.isComposing,
                      },
                      { draft, recalling: recallCursor !== null },
                    );
                    if (intent === "send") {
                      e.preventDefault();
                      void send(draft);
                    } else if (intent === "older" || intent === "newer") {
                      e.preventDefault();
                      const next = recall(asked, recallCursor, intent);
                      setRecallCursor(next.cursor);
                      setDraft(next.draft);
                    }
                  }}
                />
                {sending ? (
                  <button
                    ref={stopRef}
                    className="send-btn send-btn--stop"
                    type="button"
                    onClick={stopStreaming}
                    aria-label="Stop this turn"
                    title="Stop (Esc)"
                  >
                    <StopIcon />
                  </button>
                ) : (
                  <button
                    className="send-btn"
                    type="submit"
                    disabled={!draft.trim()}
                    aria-label="Send"
                    title="Send"
                  >
                    <SendIcon />
                  </button>
                )}
              </form>
              <p className="hint">
                {sending
                  ? "Esc stops this turn. Nothing is written without your approval."
                  : "Verified or silent — answers carry their sources, and write actions wait for your approval."}
              </p>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
