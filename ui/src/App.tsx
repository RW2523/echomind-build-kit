import { useCallback, useEffect, useRef, useState } from "react";
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
import { Reply } from "./components/Reply";
import { StageTrail } from "./components/StageTrail";
import { useTheme, type ThemeChoice } from "./theme";
import type { AgentResponse, DemoUser, Turn, UploadRecord } from "./types";

const LAST_USER_KEY = "echomind.lastUser";
const threadKey = (handle: string) => `echomind.thread.${handle}`;

interface Suggestion {
  text: string;
  /** What the question demonstrates. Written by the UI, never sourced from an answer. */
  note: string;
}

const SUGGESTIONS: Record<string, Suggestion[]> = {
  alice: [
    { text: "Where is the nearest core that can do cryo-EM?", note: "Facilities · with distance" },
    { text: "I want to image live cells — what should I use?", note: "Instruments · by technique" },
    { text: "What is on my March invoice?", note: "Invoice · your charges only" },
    { text: "How long must the confocal lasers warm up?", note: "Policy · with the passage" },
  ],
  bob: [
    { text: "Where is the nearest core that can do cryo-EM?", note: "Facilities · with distance" },
    { text: "I want to image live cells — what should I use?", note: "Instruments · by technique" },
    { text: "What is on my March invoice?", note: "Invoice · your charges only" },
    { text: "Show me alice's bookings", note: "Permissions · refused, not answered" },
  ],
  asha: [
    { text: "Where is the nearest core that can do cryo-EM?", note: "Facilities · with distance" },
    { text: "I want to image live cells — what should I use?", note: "Instruments · by technique" },
    { text: "Why was lab A charged $412 in March?", note: "Usage · traced to the bookings" },
    { text: "Show me lab A's usage this month", note: "Usage · your labs only" },
  ],
  cora: [
    { text: "Where is the nearest core that can do cryo-EM?", note: "Facilities · with distance" },
    {
      text: "Which instrument had the most downtime in March 2026?",
      note: "Usage · across all cores",
    },
    { text: "Generate the monthly summary for 2026-03", note: "Document · waits for approval" },
    { text: "What is on my March invoice?", note: "Invoice · your charges only" },
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
  const bottomRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const switchUser = useCallback(async (handle: string, restore = false) => {
    const { token, user } = await loginAs(handle);
    setToken(token);
    setCurrent(user);
    setView("chat");
    setUploadNote(null);
    setError(null);

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
  }, []);

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
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns]);

  async function send(message: string) {
    if (!message.trim() || sending) return;
    const id = `${Date.now()}`;
    setDraft("");
    setSending(true);
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

  const isAdmin = current?.role === "admin";
  const suggestions = SUGGESTIONS[current?.handle ?? ""] ?? SUGGESTIONS.alice;

  return (
    <div className="app">
      <aside className="rail">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true" />
          <span className="brand-name">
            <strong>EchoMind</strong>
            <span>Infinity X</span>
          </span>
        </div>

        <div className="rail-section">
          <h2>Sign in as</h2>
          {users.map((u) => (
            <button
              key={u.handle}
              className={`user-btn ${current?.handle === u.handle ? "active" : ""}`}
              aria-pressed={current?.handle === u.handle}
              onClick={() => void switchUser(u.handle)}
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
                  onClick={async () => {
                    await deleteUpload(u.id);
                    setUploads(await listUploads());
                  }}
                >
                  ×
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
          <button className="link-btn" onClick={() => fileRef.current?.click()}>
            + Upload a document
          </button>
          {uploadNote && <div className="private-note">{uploadNote}</div>}
        </div>

        <div className="rail-actions">
          {isAdmin && (
            <button
              className="link-btn"
              onClick={() => setView(view === "admin" ? "chat" : "admin")}
            >
              {view === "admin" ? "← Back to chat" : "Admin"}
            </button>
          )}
          <button
            className="link-btn subtle"
            onClick={() => {
              setTurns([]);
              setThreadId(null);
            }}
          >
            New conversation
          </button>
        </div>
      </aside>

      <main className="main">
        <div className="topbar">
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
              "Loading…"
            )}
          </div>
          <div className="topbar-right">
            <span className="thread-id">{threadId ? threadId : "new thread"}</span>
            <ThemeToggle />
          </div>
        </div>

        {view === "admin" ? (
          <Admin />
        ) : (
          <>
            <div className="transcript">
              <div className="transcript-inner">
                {error && <div className="error-banner">{error}</div>}

                {!turns.length && (
                  <div className="empty">
                    <h3>Ask about the facility</h3>
                    <p>
                      Every fact comes from the platform's own records. If it can't be
                      verified, you'll be told so rather than guessed at.
                    </p>
                    <div className="suggestions">
                      {suggestions.map((s) => (
                        <button key={s.text} onClick={() => void send(s.text)}>
                          <span className="suggestion-text">{s.text}</span>
                          <span className="suggestion-note">{s.note}</span>
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                {turns.map((turn) => (
                  <div className="turn" key={turn.id}>
                    <div className="bubble-user">{turn.question}</div>

                    {turn.streaming && (
                      <div className="reply reply--working">
                        {turn.streamedText ? (
                          <p className="reply-text is-streaming">{turn.streamedText}</p>
                        ) : (
                          <StageTrail stages={turn.stages ?? []} />
                        )}
                      </div>
                    )}

                    {turn.error && <div className="error-banner">{turn.error}</div>}

                    {turn.response && (
                      <>
                        <Reply response={turn.response} onSend={(text) => void send(text)} />
                        {turn.response.pending_action && (
                          <ApprovalCard
                            action={turn.response.pending_action}
                            decision={turn.decision}
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
                      </>
                    )}
                  </div>
                ))}
                <div ref={bottomRef} />
              </div>
            </div>

            <div className="composer">
              <div className="composer-inner">
                <textarea
                  value={draft}
                  aria-label="Ask a question"
                  placeholder="Ask about facilities, instruments, bookings, billing, policies…"
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      void send(draft);
                    }
                  }}
                />
                <button
                  className="btn btn--send"
                  disabled={sending || !draft.trim()}
                  onClick={() => void send(draft)}
                >
                  {sending ? "Working…" : "Send"}
                </button>
              </div>
              <div className="hint">
                Verified or silent — answers carry their sources, and write actions wait
                for your approval.
              </div>
            </div>
          </>
        )}
      </main>
    </div>
  );
}
