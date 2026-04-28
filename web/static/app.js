const messagesEl = document.getElementById("messages");
const inputEl = document.getElementById("composerInput");
const sendBtn = document.getElementById("sendBtn");
const dropdownEl = document.getElementById("mentionDropdown");
const learnMoreToggleEl = document.getElementById("learnMoreToggle");
const learnMorePanelEl = document.getElementById("learnMorePanel");
const micBtnEl = document.getElementById("micBtn");
const micHintEl = document.getElementById("micHint");

const SESSION_ID = crypto.randomUUID();

/** True while /api/chat is in flight (Enter bypasses disabled send button). */
let chatInFlight = false;
let thinkingRowEl = null;
const SEND_LABEL_DEFAULT = "Send";

let speechRec = null;
let speechActive = false;
let speechFinalPrefix = "";

let mentionState = {
  open: false,
  items: [],
  selectedIdx: 0,
  replaceStart: -1,
  replaceEnd: -1,
  token: "",
  abort: null,
  debounce: null,
};

/** First email from API (primary_email or emails[0]). */
function contactPrimaryEmail(c) {
  const e = c.primary_email || (c.emails && c.emails[0]);
  return (e && String(e).trim()) || "";
}

/** Title + subtitle for dropdown: name on top, email or phone below when it adds context. */
function contactLinesForDropdown(c) {
  if (c._error)
    return { title: c.display_name || "Contacts unavailable", subtitle: "" };
  const name = (c.display_name || "").trim();
  const email = contactPrimaryEmail(c);
  const phones = c.phones || [];
  const phone = phones[0] ? String(phones[0]).trim() : "";
  if (name) {
    const subtitle =
      email && email !== name ? email : !email && phone ? phone : "";
    return { title: name, subtitle };
  }
  if (email)
    return { title: email, subtitle: phone && phone !== email ? phone : "" };
  if (phone) return { title: phone, subtitle: "" };
  return { title: "(no name)", subtitle: "" };
}

/** Short hint when there are multiple emails or phones (e.g. "+1 email"). */
function contactExtraBadge(c) {
  const ne = (c.emails && c.emails.length) || 0;
  const np = (c.phones && c.phones.length) || 0;
  const bits = [];
  if (ne > 1) bits.push(`+${ne - 1} email${ne === 2 ? "" : "s"}`);
  if (np > 1) bits.push(`+${np - 1} number${np === 2 ? "" : "s"}`);
  return bits.join(" · ");
}

function addMessage(role, text) {
  const msg = document.createElement("div");
  msg.className = "msg";
  const r = document.createElement("div");
  r.className = "role";
  r.textContent = role;
  const body = document.createElement("div");
  body.textContent = text;
  msg.appendChild(r);
  msg.appendChild(body);
  messagesEl.appendChild(msg);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function setMicUi(active) {
  if (!micBtnEl) return;
  micBtnEl.classList.toggle("isRecording", !!active);
  micBtnEl.setAttribute("aria-label", active ? "Stop dictation" : "Dictate message");
  micBtnEl.title = active ? "Stop dictation" : "Dictate message";
}

function setupSpeechToText() {
  if (!micBtnEl) return;

  const SpeechRecognition =
    window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    micBtnEl.disabled = true;
    if (micHintEl) micHintEl.classList.remove("hidden");
    return;
  }

  micBtnEl.disabled = false;
  if (micHintEl) micHintEl.classList.add("hidden");
  speechRec = new SpeechRecognition();
  speechRec.continuous = true;
  speechRec.interimResults = true;

  speechRec.onstart = () => {
    speechActive = true;
    speechFinalPrefix = inputEl.value;
    setMicUi(true);
  };
  speechRec.onend = () => {
    speechActive = false;
    setMicUi(false);
  };
  speechRec.onerror = () => {
    speechActive = false;
    setMicUi(false);
  };
  speechRec.onresult = (event) => {
    let finalTxt = "";
    let interimTxt = "";
    for (let i = event.resultIndex; i < event.results.length; i += 1) {
      const res = event.results[i];
      const t = res[0] && res[0].transcript ? String(res[0].transcript) : "";
      if (res.isFinal) finalTxt += t;
      else interimTxt += t;
    }
    const prefix = speechFinalPrefix ? speechFinalPrefix.replace(/\s+$/, "") : "";
    const spacer = prefix ? " " : "";
    inputEl.value = `${prefix}${spacer}${(finalTxt + interimTxt).trim()}`;
    inputEl.focus();
  };

  micBtnEl.addEventListener("click", () => {
    if (!speechRec) return;
    if (chatInFlight) return;
    if (!speechActive) {
      try {
        speechRec.start();
      } catch (_) {
        // start() can throw if called too quickly after end(); ignore.
      }
    } else {
      try {
        speechRec.stop();
      } catch (_) {
        // ignore
      }
    }
  });
}

function showThinkingRow() {
  removeThinkingRow();
  const msg = document.createElement("div");
  msg.className = "msg msgThinking";
  msg.setAttribute("role", "status");
  msg.setAttribute("aria-live", "polite");
  msg.setAttribute("aria-label", "Agent is working on a response");

  const r = document.createElement("div");
  r.className = "role";
  r.textContent = "agent";

  const body = document.createElement("div");
  body.className = "thinkingWrap";
  const label = document.createElement("span");
  label.className = "thinkingLabel";
  label.textContent = "Working on it";
  const dots = document.createElement("span");
  dots.className = "thinkingDots";
  dots.setAttribute("aria-hidden", "true");
  for (let i = 0; i < 3; i += 1) {
    const d = document.createElement("span");
    d.className = "thinkingDot";
    dots.appendChild(d);
  }
  body.appendChild(label);
  body.appendChild(dots);

  msg.appendChild(r);
  msg.appendChild(body);
  messagesEl.appendChild(msg);
  thinkingRowEl = msg;
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function removeThinkingRow() {
  if (thinkingRowEl && thinkingRowEl.parentNode) {
    thinkingRowEl.parentNode.removeChild(thinkingRowEl);
  }
  thinkingRowEl = null;
}

function getActiveMention(text, caretIdx) {
  // Find nearest '@' that begins a token (start or preceded by whitespace).
  const left = text.slice(0, caretIdx);
  let at = left.lastIndexOf("@");
  if (at < 0) return null;
  const prev = at === 0 ? " " : left[at - 1];
  if (!/\s/.test(prev)) return null;

  // Token ends at caret; stop if it contains whitespace/newline.
  const token = left.slice(at + 1);
  if (/\s/.test(token)) return null;

  return { atIdx: at, token };
}

function hideDropdown() {
  mentionState.open = false;
  mentionState.items = [];
  mentionState.selectedIdx = 0;
  mentionState.replaceStart = -1;
  mentionState.replaceEnd = -1;
  mentionState.token = "";
  dropdownEl.classList.add("hidden");
  dropdownEl.innerHTML = "";
  if (mentionState.abort) {
    mentionState.abort.abort();
    mentionState.abort = null;
  }
  if (mentionState.debounce) {
    clearTimeout(mentionState.debounce);
    mentionState.debounce = null;
  }
}

function renderDropdown() {
  dropdownEl.innerHTML = "";
  if (!mentionState.items.length) {
    hideDropdown();
    return;
  }
  dropdownEl.classList.remove("hidden");
  mentionState.items.forEach((c, idx) => {
    const item = document.createElement("div");
    item.className = "mentionItem";
    item.setAttribute("role", "option");
    item.setAttribute(
      "aria-selected",
      idx === mentionState.selectedIdx ? "true" : "false",
    );

    const { title, subtitle } = contactLinesForDropdown(c);
    const left = document.createElement("div");
    left.className = "mentionLeft";
    const name = document.createElement("div");
    name.className = "mentionName";
    name.textContent = title;
    left.appendChild(name);
    if (subtitle) {
      const meta = document.createElement("div");
      meta.className = "mentionMeta";
      meta.textContent = subtitle;
      left.appendChild(meta);
    }

    const right = document.createElement("div");
    right.className = "mentionRight";
    right.textContent = contactExtraBadge(c);

    item.appendChild(left);
    item.appendChild(right);

    item.addEventListener("mousedown", (e) => {
      // Prevent textarea losing focus before we replace text.
      e.preventDefault();
      mentionState.selectedIdx = idx;
      applyMentionSelection();
    });

    dropdownEl.appendChild(item);
  });
}

function formatMention(contact) {
  if (contact._error) return "@contact";
  const name = (contact.display_name || "").trim();
  const email = contactPrimaryEmail(contact);
  const phone =
    contact.phones && contact.phones[0] ? String(contact.phones[0]).trim() : "";
  // Name + email reads like a mail header: @Name (email) — avoids @@ when email is alone.
  if (name && email) return `@${name} (${email})`;
  if (name && phone) return `@${name} (${phone})`;
  if (name) return `@${name}`;
  if (email) return email;
  if (phone) return phone;
  return "@contact";
}

function applyMentionSelection() {
  const c = mentionState.items[mentionState.selectedIdx];
  if (!c || c._error) return;
  const text = inputEl.value;
  const before = text.slice(0, mentionState.replaceStart);
  const after = text.slice(mentionState.replaceEnd);
  const insert = formatMention(c) + " ";
  const next = before + insert + after;
  inputEl.value = next;
  const newCaret = (before + insert).length;
  inputEl.setSelectionRange(newCaret, newCaret);
  hideDropdown();
}

async function fetchContacts(token) {
  if (mentionState.abort) mentionState.abort.abort();
  mentionState.abort = new AbortController();
  const qs = new URLSearchParams({ q: token, limit: "10" });
  const res = await fetch(`/api/contacts?${qs.toString()}`, {
    signal: mentionState.abort.signal,
  });
  if (!res.ok) throw new Error(`contacts_http_${res.status}`);
  return await res.json();
}

function openMentionDropdown(atIdx, caretIdx, token) {
  mentionState.open = true;
  mentionState.replaceStart = atIdx;
  mentionState.replaceEnd = caretIdx;
  mentionState.token = token;
  mentionState.selectedIdx = 0;

  if (mentionState.debounce) clearTimeout(mentionState.debounce);
  mentionState.debounce = setTimeout(async () => {
    try {
      const items = await fetchContacts(token);
      if (Array.isArray(items) && items.length) {
        mentionState.items = items;
        renderDropdown();
      } else {
        hideDropdown();
      }
    } catch (e) {
      // If the endpoint errors (missing creds, People API disabled, etc) show a single-row error state.
      mentionState.items = [
        {
          display_name: "Contacts unavailable",
          emails: [],
          phones: [],
          _error: true,
        },
      ];
      mentionState.selectedIdx = 0;
      renderDropdown();
    }
  }, 150);
}

inputEl.addEventListener("input", () => {
  const caret = inputEl.selectionStart;
  const info = getActiveMention(inputEl.value, caret);
  if (!info) {
    hideDropdown();
    return;
  }
  // Query can be empty; still allow suggestions after '@'
  openMentionDropdown(info.atIdx, caret, info.token);
});

inputEl.addEventListener("keydown", (e) => {
  // Mention dropdown must be handled in the same handler as send-on-Enter; otherwise the first
  // handler calls hideDropdown() and the second sees mentionState.open === false and submits.
  if (mentionState.open) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      mentionState.selectedIdx = Math.min(
        mentionState.selectedIdx + 1,
        mentionState.items.length - 1,
      );
      renderDropdown();
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      mentionState.selectedIdx = Math.max(mentionState.selectedIdx - 1, 0);
      renderDropdown();
      return;
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      applyMentionSelection();
      return;
    }
    if (e.key === "Escape") {
      e.preventDefault();
      hideDropdown();
      return;
    }
    // Other keys (keep typing the @-query) — default behavior
    return;
  }

  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

async function sendMessage() {
  const text = inputEl.value.trim();
  if (!text || chatInFlight) return;
  hideDropdown();
  if (speechActive && speechRec) {
    try {
      speechRec.stop();
    } catch (_) {
      // ignore
    }
  }

  chatInFlight = true;
  inputEl.value = "";
  inputEl.disabled = true;
  sendBtn.disabled = true;
  sendBtn.textContent = "Working…";
  addMessage("you", text);
  showThinkingRow();

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ message: text, session_id: SESSION_ID }),
    });
    const data = await res.json();
    removeThinkingRow();
    if (!res.ok) {
      const detail = data.detail;
      const msg =
        detail && typeof detail === "object" && !Array.isArray(detail)
          ? detail.hint || detail.message || JSON.stringify(detail)
          : typeof detail === "string"
            ? detail
            : JSON.stringify(data);
      addMessage("agent", `Error: ${msg}`);
    } else {
      addMessage("agent", data.response || "");
    }
  } catch (e) {
    removeThinkingRow();
    const fallback =
      e instanceof SyntaxError
        ? "Error: Could not parse the server response."
        : `Network error: ${String(e)}`;
    addMessage("agent", fallback);
  } finally {
    chatInFlight = false;
    inputEl.disabled = false;
    sendBtn.disabled = false;
    sendBtn.textContent = SEND_LABEL_DEFAULT;
    inputEl.focus();
  }
}

sendBtn.addEventListener("click", sendMessage);

if (learnMoreToggleEl && learnMorePanelEl) {
  learnMoreToggleEl.addEventListener("click", () => {
    const isHidden = learnMorePanelEl.classList.contains("hidden");
    learnMorePanelEl.classList.toggle("hidden", !isHidden);
    learnMoreToggleEl.setAttribute(
      "aria-expanded",
      isHidden ? "true" : "false",
    );
  });
}

setupSpeechToText();

addMessage("agent", "Hi! How can I help today?");
