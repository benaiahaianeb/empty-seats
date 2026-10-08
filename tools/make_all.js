// Regenerates all/index.html from template.html. Run after any template change.
// node tools/make_all.js
const fs = require("fs");
const SRC = require("path").join(__dirname, "..", "template.html");
const DST = require("path").join(__dirname, "..", "all", "index.html");
let s = fs.readFileSync(SRC, "utf8").split("\r\n").join("\n");
const rep = (o, n, t) => {
  const c = s.split(o).length - 1;
  if (c !== 1) { console.error("FAIL " + t + ": " + c + " matches"); process.exit(1); }
  s = s.replace(o, n); console.log("ok " + t);
};

/* airline picker: a native dialog on first run, a select in Options after */
rep(`<script>
const DATA = /*__DATA__*/null;`,
`<dialog id="airlineDlg">
  <form method="dialog">
    <h3>Which airline?</h3>
    <p>Flights and empty seats are shown for one airline at a time. Pick the one whose flights are listable; change it any time under Options.</p>
    <select id="airlineFirst" required></select>
    <button class="chip on" type="submit">Continue</button>
  </form>
</dialog>
<script type="module">
// One airline's dataset at a time. The choice is saved in the browser and can
// be changed under Options; nothing from another airline is ever loaded, so
// nothing from another airline can be shown.
const KEY = "esa-airline";
// a failed load says so instead of leaving a blank page, then stops the script
const getJSON = url => fetch(url, {cache: "no-cache"}).then(r => r.ok ? r.json() : Promise.reject())
  .catch(() => { document.getElementById("summary").textContent = "Could not load the flight data. Reload to try again."; throw new Error(url); });
const CARRIER_LIST = await getJSON("../data/carriers.json");
let saved = null;
try { saved = localStorage.getItem(KEY); } catch(e) {}
if (!CARRIER_LIST.some(c => c.code === saved)) saved = null;
const fill = sel => { sel.innerHTML = CARRIER_LIST.map(c => '<option value="' + c.code + '">' + c.name + " (" + c.code + ")</option>").join(""); };
if (!saved) {
  const dlg = document.getElementById("airlineDlg"), sel = document.getElementById("airlineFirst");
  fill(sel);
  saved = await new Promise(res => {
    dlg.addEventListener("close", () => res(sel.value));
    dlg.showModal();
  });
  try { localStorage.setItem(KEY, saved); } catch(e) {}
}
const DATA = await getJSON("../data/" + saved + ".json");
{
  const sel = document.getElementById("airlineSel");
  fill(sel); sel.value = saved;
  sel.addEventListener("change", () => { try { localStorage.setItem(KEY, sel.value); } catch(e) {} location.reload(); });
  document.getElementById("airlineName").textContent = DATA.carrier.name;
}`, "loader");

rep(`      <div class="brand">Empty <em>Seats</em></div>`,
    `      <div class="brand">Empty <em>Seats</em> <span class="airline" id="airlineName"></span></div>`, "brand");
rep(`  .themebtn{`, `  .brand .airline{font-size:14px;font-weight:400;color:var(--text-secondary);margin-left:8px}
  #airlineDlg{margin:auto;border:1px solid var(--divider);border-radius:var(--radius);background:var(--surface);color:var(--text);
    padding:20px 22px;max-width:360px;font-size:14px}
  #airlineDlg::backdrop{background:rgba(0,0,0,.45)}
  #airlineDlg h3{margin:0 0 8px;font-size:15px;font-weight:600}
  #airlineDlg p{margin:0 0 12px;color:var(--text-secondary);line-height:1.4}
  #airlineDlg select,.optrow select{font:inherit;font-size:13px;padding:5px 8px;border:1px solid var(--divider);
    border-radius:var(--radius);background:var(--surface);color:var(--text);margin-right:10px}
  .themebtn{`, "css");

/* the Airline select joins the international toggle's row */
rep(`          <div class="optrow">
            <button class="chip" id="intlChip">Include international connections</button>
          </div>`,
`          <div class="optrow">
            <span class="chip-cap">Airline</span>
            <select id="airlineSel" aria-label="Airline"></select>
            <button class="chip" id="intlChip">Include international connections</button>
          </div>`, "airline row");

/* copy */
rep(`Include international connections allows a foreign airport as a connecting point.</p>`,
    `Airline switches the whole dataset to another carrier; the choice is remembered on this device. Include international connections allows a foreign airport as a connecting point.</p>`, "faq options");
rep(`International times come from airport departure boards;`,
    `International times come from airport departure boards, for Delta only so far;`, "faq source");

fs.writeFileSync(DST, s);
console.log("--- wrote " + DST + " ---");
