"use strict";
const $ = (s) => document.querySelector(s);
const chat = $("#chat"), form = $("#form"), input = $("#input"), sendBtn = $("#send");
let history = [];
let busy = false;

/* ---- 최소 마크다운 렌더러: 문자열은 항상 먼저 이스케이프하고 DOM으로만 조립 ---- */
function inline(text, parent, sources) {
  const re = /\*\*(.+?)\*\*|\[(\d{1,2})\]/g;
  let last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) parent.append(text.slice(last, m.index));
    if (m[1] !== undefined) {
      const b = document.createElement("strong");
      b.textContent = m[1];
      parent.append(b);
    } else {
      const n = +m[2];
      if (sources && sources.some((s) => s.n === n)) {
        const c = document.createElement("button");
        c.type = "button"; c.className = "cite"; c.textContent = n; c.dataset.n = n;
        c.setAttribute("aria-label", `근거 ${n} 보기`);
        parent.append(c);
      } else parent.append(m[0]);
    }
    last = re.lastIndex;
  }
  if (last < text.length) parent.append(text.slice(last));
}

function render(el, md, sources) {
  el.replaceChildren();
  let list = null, para = null;
  const flush = () => { list = null; para = null; };
  for (const raw of md.split("\n")) {
    const line = raw.trimEnd();
    let m;
    if (!line.trim()) { flush(); continue; }
    if ((m = line.match(/^\s{0,3}#{1,6}\s+(.*)$/))) {
      flush();
      const h = document.createElement("h3"); inline(m[1], h, sources); el.append(h);
    } else if ((m = line.match(/^\s*(\d+)[.)]\s+(.*)$/)) || (m = line.match(/^\s*[-*•]\s+(.*)$/))) {
      const ordered = m.length === 3;
      const tag = ordered ? "OL" : "UL";
      if (!list || list.tagName !== tag) {
        list = document.createElement(tag.toLowerCase());
        if (ordered && +m[1] > 1) list.start = +m[1];
        el.append(list); para = null;
      }
      const li = document.createElement("li"); inline(ordered ? m[2] : m[1], li, sources); list.append(li);
    } else if (/^\s*[-*_]{3,}\s*$/.test(line)) {
      flush();
    } else {
      if (!para) { para = document.createElement("p"); el.append(para); list = null; }
      else para.append(document.createElement("br"));
      inline(line.trim(), para, sources);
    }
  }
}

/* ---- UI ---- */
function scrollDown() { chat.scrollTop = chat.scrollHeight; }

function addUser(text) {
  const d = document.createElement("div");
  d.className = "msg user";
  const b = document.createElement("div"); b.className = "bubble"; b.textContent = text;
  d.append(b); chat.append(d); scrollDown();
}

function setWait(bubble, label) {
  bubble.replaceChildren();
  const w = document.createElement("span"); w.className = "wait";
  const dots = document.createElement("span"); dots.className = "dots"; dots.setAttribute("aria-hidden", "true");
  dots.append(document.createElement("i"), document.createElement("i"), document.createElement("i"));
  w.append(dots, label);
  bubble.append(w);
}

function addBot() {
  const d = document.createElement("div");
  d.className = "msg bot";
  const b = document.createElement("div"); b.className = "bubble";
  setWait(b, "안내서를 찾는 중");
  d.append(b); chat.append(d); scrollDown();
  return { root: d, bubble: b };
}

async function ask(q) {
  if (busy || !q.trim()) return;
  busy = true; sendBtn.disabled = true;
  $("#welcome")?.remove();
  addUser(q);
  const bot = addBot();
  let sources = [], text = "", started = false;
  try {
    const res = await fetch("/api/chat", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q, history }),
    });
    if (!res.ok || !res.body) throw new Error(`서버 오류 (${res.status})`);
    const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += value;
      let i;
      while ((i = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, i); buf = buf.slice(i + 1);
        if (!line) continue;
        const ev = JSON.parse(line);
        if (ev.type === "sources") { sources = ev.items; bot.root.sources = sources; if (!started) setWait(bot.bubble, "답변을 작성하는 중"); }
        else if (ev.type === "delta") {
          started = true; text += ev.text;
          bot.bubble.classList.add("typing");
          render(bot.bubble, text, sources); scrollDown();
        } else if (ev.type === "error") throw new Error(ev.message);
      }
    }
    bot.bubble.classList.remove("typing");
    if (!started) throw new Error("응답을 받지 못했습니다.");
    history.push({ role: "user", text: q }, { role: "assistant", text });
    history = history.slice(-8);
  } catch (e) {
    bot.bubble.classList.remove("typing");
    if (!started) { bot.bubble.className = "bubble error"; bot.bubble.textContent = "오류: " + e.message; }
  } finally {
    busy = false; sendBtn.disabled = !input.value.trim(); scrollDown(); input.focus();
  }
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const q = input.value.trim();
  input.value = ""; autosize();
  ask(q);
});
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); form.requestSubmit(); }
});
function autosize() {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 160) + "px";
  sendBtn.disabled = busy || !input.value.trim();
}
input.addEventListener("input", autosize);

chat.addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (chip) return ask(chip.textContent);
  const c = e.target.closest(".cite");
  if (c) showTip(c);
});

/* ---- 근거 말풍선: 번호 클릭 시 표시, 마우스를 떼면 사라짐 ---- */
const tip = document.createElement("div");
tip.className = "tip"; tip.setAttribute("role", "tooltip"); tip.hidden = true;
document.body.append(tip);

function showTip(c) {
  const src = c.closest(".msg")?.sources?.find((s) => s.n === +c.dataset.n);
  if (!src) return;
  const file = document.createElement("span"); file.className = "tip-file"; file.textContent = src.name;
  const meta = document.createElement("span"); meta.className = "tip-meta";
  meta.textContent = (src.page ? `${src.page}페이지 · ` : "") + `유사도 ${Math.round(src.score * 100)}%`;
  tip.replaceChildren(file, meta);
  tip.hidden = false; tip.dataset.pos = "top";
  const r = c.getBoundingClientRect(), t = tip.getBoundingClientRect();
  let left = r.left + r.width / 2 - t.width / 2;
  left = Math.max(8, Math.min(left, innerWidth - t.width - 8));
  let top = r.top - t.height - 10;
  if (top < 8) { top = r.bottom + 10; tip.dataset.pos = "bottom"; }
  tip.style.left = left + "px"; tip.style.top = top + "px";
  tip.style.setProperty("--arrow", Math.round(r.left + r.width / 2 - left) + "px");
  tip.dataset.for = c.dataset.n;
}
function hideTip() { tip.hidden = true; }
chat.addEventListener("mouseout", (e) => { if (e.target.closest(".cite")) hideTip(); });
chat.addEventListener("focusout", (e) => { if (e.target.closest(".cite")) hideTip(); });
chat.addEventListener("scroll", hideTip);
addEventListener("resize", hideTip);
document.addEventListener("click", (e) => { if (!e.target.closest(".cite")) hideTip(); });

$("#reset").addEventListener("click", () => {
  if (busy) return;
  history = [];
  chat.querySelectorAll(".msg").forEach((n) => n.remove());
  if (!$("#welcome")) location.reload();
});

autosize(); input.focus();

/* ---- 예시 질문: 4개씩 순환 ---- */
const EXAMPLES = [
  "저상버스는 어떻게 이용하나요?",
  "서면역에 엘리베이터가 있나요?",
  "24번 버스에서 환승할 수 있는 노선은?",
  "휠체어로 버스를 탈 때 어떻게 해야 하나요?",
  "저상버스를 예약할 수 있나요?",
  "24번 버스는 저상버스로 운행하나요?",
  "버스 도착 시간은 어디서 확인하나요?",
  "교통약자석은 어떻게 이용하나요?",
  "내릴 정류장을 기사님께 어떻게 알리나요?",
  "시각장애인은 버스 정보를 어떻게 확인하나요?",
  "엘리베이터 위치는 어디서 알 수 있나요?",
  "보행 보조기를 사용할 때 주의할 점은?",
];
const chipsEl = $("#chips"), SIZE = 4, INTERVAL = 7000;
let setIdx = 0, timer = null, paused = false;

function paint(first) {
  const start = (setIdx * SIZE) % EXAMPLES.length;
  const items = Array.from({ length: SIZE }, (_, k) => EXAMPLES[(start + k) % EXAMPLES.length]);
  chipsEl.replaceChildren(...items.map((t, k) => {
    const b = document.createElement("button");
    b.type = "button"; b.className = "chip"; b.textContent = t;
    b.style.setProperty("--i", k);
    return b;
  }));
}
function rotate() {
  if (!chipsEl.isConnected) return stopRotate();
  chipsEl.classList.add("leaving");
  setTimeout(() => {
    setIdx++; paint();
    chipsEl.classList.remove("leaving");
  }, 280);
}
function startRotate() { stopRotate(); timer = setInterval(() => { if (!paused && !busy) rotate(); }, INTERVAL); }
function stopRotate() { clearInterval(timer); timer = null; }
paint();
startRotate();
$("#more").addEventListener("click", () => { rotate(); startRotate(); });
const welcomeEl = $("#welcome");
welcomeEl.addEventListener("mouseenter", () => (paused = true));
welcomeEl.addEventListener("mouseleave", () => (paused = false));
welcomeEl.addEventListener("focusin", () => (paused = true));
welcomeEl.addEventListener("focusout", () => (paused = false));

/* ---- 신호등: 순차 점등 + 빛이 마우스 커서 방향을 향함 ---- */
(() => {
  const lamps = Array.from(document.querySelectorAll("#signal .lamp"));
  const [red, yellow, green] = lamps;
  const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const seq = [[green, 3200], [yellow, 1200], [red, 3200]];
  let k = 0;
  function step() {
    lamps.forEach((l) => l.classList.toggle("on", l === seq[k][0]));
    const wait = seq[k][1];
    k = (k + 1) % seq.length;
    setTimeout(step, wait);
  }
  if (still) green.classList.add("on"); else step();

  let mx = null, my = null, queued = false;
  function aim() {
    queued = false;
    for (const l of lamps) {
      const r = l.getBoundingClientRect();
      const a = Math.atan2(my - (r.top + r.height / 2), mx - (r.left + r.width / 2)) * 180 / Math.PI + 90;
      l.style.setProperty("--a", a.toFixed(1) + "deg");
    }
  }
  if (!still) {
    addEventListener("pointermove", (e) => {
      mx = e.clientX; my = e.clientY;
      if (!queued) { queued = true; requestAnimationFrame(aim); }
    }, { passive: true });
  }
})();
