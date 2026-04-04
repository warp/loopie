const messagesEl = document.getElementById("messages");
const inputEl = document.getElementById("composerInput");
const sendBtn = document.getElementById("sendBtn");
const dropdownEl = document.getElementById("mentionDropdown");

const SESSION_ID = crypto.randomUUID();

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
    item.setAttribute("aria-selected", idx === mentionState.selectedIdx ? "true" : "false");

    const left = document.createElement("div");
    const name = document.createElement("div");
    name.className = "mentionName";
    name.textContent = c.display_name || "(no name)";
    const meta = document.createElement("div");
    meta.className = "mentionMeta";
    meta.textContent = (c.emails && c.emails[0]) || (c.phones && c.phones[0]) || "";
    left.appendChild(name);
    left.appendChild(meta);

    const right = document.createElement("div");
    right.className = "mentionRight";
    right.textContent = c.emails && c.emails.length ? `${c.emails.length} email(s)` : "";

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
  const name = (contact.display_name || "").trim();
  if (name) return `@${name}`;
  const email = (contact.emails && contact.emails[0]) || "";
  if (email) return `@${email}`;
  const phone = (contact.phones && contact.phones[0]) || "";
  if (phone) return `@${phone}`;
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
  const res = await fetch(`/api/contacts?${qs.toString()}`, { signal: mentionState.abort.signal });
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
  if (!mentionState.open) return;

  if (e.key === "ArrowDown") {
    e.preventDefault();
    mentionState.selectedIdx = Math.min(mentionState.selectedIdx + 1, mentionState.items.length - 1);
    renderDropdown();
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    mentionState.selectedIdx = Math.max(mentionState.selectedIdx - 1, 0);
    renderDropdown();
  } else if (e.key === "Enter") {
    // If dropdown open, Enter selects mention; Shift+Enter makes a newline
    if (!e.shiftKey) {
      e.preventDefault();
      applyMentionSelection();
    }
  } else if (e.key === "Escape") {
    e.preventDefault();
    hideDropdown();
  }
});

async function sendMessage() {
  const text = inputEl.value.trim();
  if (!text) return;
  hideDropdown();

  inputEl.value = "";
  sendBtn.disabled = true;
  addMessage("you", text);

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ message: text, session_id: SESSION_ID }),
    });
    const data = await res.json();
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
    addMessage("agent", `Network error: ${String(e)}`);
  } finally {
    sendBtn.disabled = false;
    inputEl.focus();
  }
}

sendBtn.addEventListener("click", sendMessage);
inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !mentionState.open) {
    e.preventDefault();
    sendMessage();
  }
});

addMessage("agent", "Hi — ask me something. Use @ to mention a contact.");

