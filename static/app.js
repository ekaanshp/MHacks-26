"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const demo = new URLSearchParams(location.search).get("demo") === "1";
  const demoPath = (path) => demo ? `${path}${path.includes("?") ? "&" : "?"}demo=1` : path;
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const state = { estate: null, bucket: "all", currentAccount: null, drawerGeneration: 0, drawerAbort: null, pollTimer: null, previousFocus: null, toastTimer: null, calls: new Map(), callAttempts: new Map(), voice: null, sdk: null, evidence: new Map(), activityGeneration: 0 };
  const buckets = [
    { id: "leaving", title: "Money leaving", description: "Charges to stop or move", icon: "↗" },
    { id: "waiting", title: "Money waiting", description: "Assets to find and claim", icon: "↙" },
    { id: "notify", title: "People to notify", description: "Let them know what has changed", icon: "✉" },
    { id: "owed", title: "Money owed", description: "Balances to review with the estate", icon: "≋" },
    { id: "legacy", title: "Her digital legacy", description: "Preserve what matters", icon: "♡" }
  ];
  const statusLabels = { open: "Open", in_progress: "In progress", done: "Done" };
  const actionLabels = { cancel: "Cancel this account", transfer: "Transfer this account", claim: "Claim this asset", notify: "Notify this institution", memorialize: "Preserve her digital legacy" };
  const money = (value, precise = false) => new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: precise && Number(value) % 1 ? 2 : 0, maximumFractionDigits: precise ? 2 : 0 }).format(Number(value) || 0);
  const number = (value) => new Intl.NumberFormat("en-US").format(Number(value) || 0);
  const date = (value, options = {}) => {
    if (!value) return "";
    const parsed = new Date(/^\d{4}-\d{2}-\d{2}$/.test(value) ? `${value}T12:00:00` : value);
    return Number.isNaN(parsed.getTime()) ? "" : parsed.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", ...options });
  };
  const element = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  };
  const button = (text, className, onClick) => {
    const node = element("button", className, text);
    node.type = "button";
    if (onClick) node.addEventListener("click", onClick);
    return node;
  };
  const append = (parent, ...children) => { children.filter(Boolean).forEach((child) => parent.append(child)); return parent; };
  const showError = (node, message) => { node.textContent = message; node.hidden = false; };
  const errorMessage = (error) => error && error.message ? error.message : "Something went wrong. Please try again.";

  // Authentication lives in a server-managed HttpOnly cookie. Only its CSRF
  // token lives in memory; neither the access code nor a bearer token is stored.
  let csrfToken = "";
  let loginPromise = null;
  try { sessionStorage.removeItem("lastly-family-access"); } catch (_) { /* Erase credentials saved by older versions when storage is available. */ }

  class ApiError extends Error {
    constructor(message, status = 0, uncertain = false) {
      super(message);
      this.status = status;
      this.uncertain = uncertain;
    }
  }

  async function responseData(response) {
    let data;
    try { data = await response.json(); } catch (_) { data = null; }
    if (!response.ok) {
      let message = data && (data.detail || data.error || data.message);
      if (typeof message !== "string") message = response.status === 401
        ? "Your family session has expired. Unlock the estate and approve this action again."
        : `We couldn’t complete that request (${response.status}). Please try again.`;
      throw new ApiError(message, response.status, response.status >= 500 || response.status === 409);
    }
    if (data === null) throw new ApiError("Lastly returned an incomplete response. Please try again.", response.status, true);
    return data;
  }

  async function restoreSession() {
    try {
      const response = await fetch("/api/session", { credentials: "same-origin", cache: "no-store", redirect: "error", headers: { Accept: "application/json" } });
      const session = await responseData(response);
      csrfToken = session.authenticated === true && typeof session.csrf_token === "string" ? session.csrf_token : "";
      $("lock-estate").hidden = !csrfToken;
    } catch (_) { csrfToken = ""; }
  }
  const sessionReady = restoreSession();

  function unlockEstate() {
    if (loginPromise) return loginPromise;
    const dialog = $("access-dialog");
    const form = $("access-form");
    const input = $("access-code");
    const submit = $("access-submit");
    const cancel = $("access-cancel");
    const error = $("access-error");
    const previousFocus = document.activeElement;
    const pending = new Promise((resolve, reject) => {
      const cleanup = () => {
        form.removeEventListener("submit", login);
        dialog.removeEventListener("close", cancelled);
        dialog.removeEventListener("cancel", preventPendingClose);
        input.value = "";
        submit.disabled = cancel.disabled = false;
        if (previousFocus && previousFocus.isConnected) previousFocus.focus({ preventScroll: true });
      };
      const preventPendingClose = (event) => { if (submit.disabled) event.preventDefault(); };
      const cancelled = () => { cleanup(); reject(new ApiError("A family access code is required to open this estate.", 401)); };
      const login = async (event) => {
        event.preventDefault();
        if (submit.disabled || !input.value.trim()) return;
        submit.disabled = cancel.disabled = true;
        error.hidden = true;
        // Clear the password field immediately. The submitted code is used only
        // for this exchange and never attached to other API requests.
        const body = JSON.stringify({ access_code: input.value.trim() });
        input.value = "";
        try {
          const response = await fetch("/api/session", {
            method: "POST", credentials: "same-origin", cache: "no-store", redirect: "error",
            headers: { "Content-Type": "application/json", "X-Requested-With": "Lastly", Accept: "application/json" }, body
          });
          const session = await responseData(response);
          if (typeof session.csrf_token !== "string" || !session.csrf_token) throw new ApiError("The session could not be opened. Try unlocking it again.");
          csrfToken = session.csrf_token;
          $("lock-estate").hidden = false;
          cleanup();
          dialog.close();
          resolve();
        } catch (loginError) {
          showError(error, errorMessage(loginError));
          input.focus();
        } finally { submit.disabled = cancel.disabled = false; }
      };
      form.addEventListener("submit", login);
      dialog.addEventListener("close", cancelled);
      dialog.addEventListener("cancel", preventPendingClose);
      error.hidden = true;
      dialog.showModal();
      input.focus();
    });
    loginPromise = pending.finally(() => { loginPromise = null; });
    return loginPromise;
  }

  async function api(path, options = {}, allowAccessPrompt = true) {
    await sessionReady;
    const headers = new Headers(options.headers);
    const method = (options.method || "GET").toUpperCase();
    headers.set("Accept", "application/json");
    if (options.body) headers.set("Content-Type", "application/json");
    if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
      headers.set("X-Requested-With", "Lastly");
      if (csrfToken) headers.set("X-CSRF-Token", csrfToken);
    }
    let response;
    try { response = await fetch(path, { ...options, method, credentials: "same-origin", cache: "no-store", redirect: "error", headers }); }
    catch (error) {
      if (error.name === "AbortError") throw error;
      throw new ApiError("We couldn’t reach Lastly. Check that the server is running and try again.", 0, true);
    }
    if (response.status === 401 && allowAccessPrompt) {
      csrfToken = "";
      $("lock-estate").hidden = true;
      await unlockEstate();
      return api(path, options, false);
    }
    try { return await responseData(response); }
    catch (error) {
      if (error.status === 403 && csrfToken) {
        // Another tab can renew the shared cookie. Recover its current CSRF
        // token through a read; the rejected action still needs an explicit retry.
        const previousToken = csrfToken;
        await restoreSession();
        if (csrfToken && csrfToken !== previousToken) error.message = "Your family session was refreshed. Approve this action again.";
      }
      throw error;
    }
  }

  function toast(message) {
    clearTimeout(state.toastTimer);
    $("toast").textContent = message;
    $("toast").hidden = false;
    state.toastTimer = setTimeout(() => { $("toast").hidden = true; }, 4500);
  }

  function screen(id) {
    ["plan-screen", "welcome-screen", "loading-screen", "dashboard"].forEach((name) => { $(name).hidden = name !== id; });
    window.scrollTo({ top: 0, behavior: "instant" });
  }

  function personalize(estate) {
    const persona = estate.persona || {};
    const name = persona.name || "Your loved one";
    const first = name.split(" ")[0];
    $("welcome-persona").textContent = `${name}’s information is ready`;
    $("analyze-button").replaceChildren(document.createTextNode(`Read ${first}’s inbox `), element("span", "", "→"));
    $("plan-title").replaceChildren(document.createTextNode(`How ${first}’s inbox`), element("br"), document.createTextNode("was connected"));
    $("continue-button").replaceChildren(document.createTextNode(`Continue to ${first}’s estate `), element("span", "", "→"));
    const selected = document.querySelector(".connection-card.selected p");
    selected.textContent = `${first} chose to share her account information with her family when the time came.`;
    document.querySelector(".welcome>.eyebrow").textContent = `${first}’s family hub`;
    document.querySelector(".persona-monogram").firstChild.textContent = first.charAt(0).toUpperCase();
  }

  async function analyze() {
    $("analyze-error").hidden = true;
    $("analyze-button").disabled = true;
    screen("loading-screen");
    const started = performance.now();
    const minDuration = reducedMotion ? 0 : demo ? 3000 : 2200;
    const steps = document.querySelectorAll(".analysis-steps span");
    let finishedEstate = null;
    const updateProgress = () => {
      const fraction = Math.min(.91, (performance.now() - started) / Math.max(2200, minDuration));
      $("progress-fill").value = Math.max(4, fraction * 100);
      const step = fraction < .34 ? 0 : fraction < .68 ? 1 : 2;
      steps.forEach((item, i) => item.classList.toggle("active", i <= step));
      $("analysis-message").textContent = ["Reading the inbox and bank statement…", "Finding accounts and checking the evidence…", "Bringing the next steps together for your family…"][step];
      const estate = finishedEstate || state.estate;
      if (estate && estate.stats) {
        $("email-counter").textContent = number(Math.floor(estate.stats.emails * Math.min(1, fraction * 1.3)));
        $("sender-counter").textContent = number(Math.floor(estate.stats.senders * Math.min(1, fraction * 1.2)));
        $("account-counter").textContent = number(Math.floor(estate.accounts.length * Math.max(0, (fraction - .25) / .65)));
      }
    };
    ["email-counter", "account-counter", "sender-counter"].forEach((id) => { $(id).textContent = "—"; });
    updateProgress();
    const timer = setInterval(updateProgress, 100);
    try {
      const result = await api(demoPath("/api/analyze"), { method: "POST" });
      if (!Array.isArray(result.accounts)) throw new Error("The estate is missing its accounts. Please try reading the inbox again.");
      finishedEstate = result;
      await new Promise((resolve) => setTimeout(resolve, Math.max(0, minDuration - (performance.now() - started))));
      state.estate = result;
      clearInterval(timer);
      $("progress-fill").value = 100;
      renderDashboard();
      screen("dashboard");
      $("dashboard-title").setAttribute("tabindex", "-1");
      $("dashboard-title").focus({ preventScroll: true });
      loadActivity();
    } catch (error) {
      clearInterval(timer);
      screen("welcome-screen");
      showError($("analyze-error"), errorMessage(error));
      $("analyze-button").focus();
    } finally { $("analyze-button").disabled = false; }
  }

  function renderDashboard() {
    const estate = state.estate;
    const persona = estate.persona || {};
    const name = persona.name || "Your loved one";
    const first = name.split(" ")[0];
    personalize(estate);
    $("estate-eyebrow").textContent = `${first}’s family hub`;
    $("as-of").textContent = `As of ${date(estate.today)}`;
    $("persona-name").textContent = name;
    $("persona-avatar").textContent = name.charAt(0).toUpperCase();
    $("persona-detail").textContent = [persona.city, persona.date_of_death ? `Remembering her since ${date(persona.date_of_death, { year: undefined })}` : ""].filter(Boolean).join(" · ");
    $("estate-description").textContent = `We found ${number(estate.accounts.length)} accounts in ${first}’s information. Here’s where to begin.`;
    renderSummary();
    renderUrgent();
    renderDiscovery();
    renderTabs();
    renderLedger();
    renderCoverage();
  }

  function renderSummary() {
    const { totals = {}, stats = {} } = state.estate;
    const cards = [
      { label: "Recurring charges found", value: money(totals.monthly_drain, true), unit: "/ month", note: "Based on recent billing records", icon: "↗", className: "summary-leaving" },
      { label: "Money waiting to be claimed", value: money(totals.assets_found), note: "Balances, savings & insurance", icon: "↙", className: "summary-assets" },
      { label: "Outstanding balances", value: money(totals.debts_found), note: "For the estate to review", icon: "≋" },
      { label: "Charged since she passed", value: money(totals.charged_since_death, true), note: "Active accounts still billing", icon: "◷", className: "summary-leaving" }
    ];
    $("summary-grid").replaceChildren(...cards.map((card) => {
      const node = element("article", `summary-card ${card.className || ""}`);
      const label = append(element("div", "summary-label"), element("span", "", card.label), element("span", "summary-icon", card.icon));
      const amount = element("div", "summary-number", card.value);
      if (card.unit) amount.append(element("small", "", card.unit));
      return append(node, label, amount, element("p", "summary-note", card.note));
    }));
    const facts = [
      [number(stats.emails), "emails reviewed"],
      [number(state.estate.accounts.length), "accounts found"],
      [number(totals.death_certificates), "death certificate copies to plan for"]
    ];
    $("estate-facts").replaceChildren(...facts.map(([value, label]) => append(element("span"), element("strong", "", value), document.createTextNode(` ${label}`))));
  }

  function renderUrgent() {
    const accounts = state.estate.accounts.filter((account) => account.urgent && account.active && account.status !== "done");
    const account = accounts.find((item) => /prime/i.test(item.institution)) || accounts[0];
    const container = $("urgent-alert");
    container.hidden = !account;
    if (!account) return;
    let when = account.days_until === 0 ? "today" : account.days_until === 1 ? "tomorrow" : account.days_until >= 0 ? `in ${account.days_until} days` : "needs attention now";
    const text = append(element("p"), element("strong", "", `${account.institution} ${account.days_until >= 0 ? `renews ${when}` : when}.`), document.createTextNode(` ${account.amount !== null ? `${money(account.amount, true)} may be charged. ` : ""}A small next step can prevent another charge.`));
    container.replaceChildren(element("span", "urgent-icon", "◷"), text, button("View account →", "text-button", () => openAccount(account.id)));
  }

  function renderDiscovery() {
    const insurance = state.estate.accounts.filter((account) => account.category === "insurance" && account.action === "claim" && Number(account.amount) > 0).sort((a, b) => b.amount - a.amount)[0];
    const container = $("discovery-card");
    container.hidden = !insurance;
    if (!insurance) return;
    const content = element("div", "discovery-content");
    append(content, element("span", "eyebrow", "Something worth finding"));
    const heading = element("h2", "", `A ${money(insurance.amount)} life insurance policy, waiting for her family.`);
    heading.id = "discovery-heading";
    append(content, heading, element("p", "", `${insurance.institution} · Found in her records. Open the original evidence and review how to make a claim.`));
    container.replaceChildren(element("span", "discovery-icon", "✳"), content, button("See what we found ↗", "button button-secondary", () => openAccount(insurance.id)));
  }

  function renderTabs() {
    const options = [{ id: "all", title: "All accounts" }, ...buckets];
    $("account-tabs").replaceChildren(...options.map((option) => {
      const count = option.id === "all" ? state.estate.accounts.length : state.estate.accounts.filter((item) => item.bucket === option.id).length;
      const node = button(option.title, `account-tab ${state.bucket === option.id ? "active" : ""}`, () => {
        state.bucket = option.id;
        renderTabs();
        renderLedger();
        const active = [...$("account-tabs").children].find((item) => item.dataset.bucket === option.id);
        if (active) active.focus({ preventScroll: true });
      });
      node.dataset.bucket = option.id;
      node.setAttribute("aria-pressed", String(state.bucket === option.id));
      node.append(element("span", "", number(count)));
      return node;
    }));
  }

  function accountValue(account) {
    if (account.amount === null || account.amount === undefined) return { value: "—", unit: "No amount listed" };
    return { value: money(account.amount, true), unit: { monthly: "per month", annual: "per year", balance: account.category === "insurance" ? "policy benefit" : "balance", one_time: "one-time", none: "" }[account.frequency] || "" };
  }

  function renderLedger() {
    const accounts = state.estate.accounts;
    const open = accounts.filter((account) => account.status !== "done").length;
    $("account-count").textContent = `${open} to review · ${accounts.length - open} completed`;
    $("ledger").replaceChildren();
    buckets.filter((bucket) => state.bucket === "all" || state.bucket === bucket.id).forEach((bucket) => {
      const items = accounts.filter((account) => account.bucket === bucket.id).sort((a, b) => Number(a.status === "done") - Number(b.status === "done") || Number(b.urgent) - Number(a.urgent) || Number(b.active) - Number(a.active));
      if (!items.length) return;
      const section = element("section", `bucket bucket-${bucket.id}`);
      const heading = element("div", "bucket-heading");
      const title = element("h3", "", bucket.title);
      title.id = `bucket-title-${bucket.id}`;
      section.setAttribute("aria-labelledby", title.id);
      append(heading, element("span", "bucket-icon", bucket.icon), title, element("p", "", bucket.description));
      let sum = items.filter((account) => account.active).reduce((total, account) => total + (bucket.id === "leaving" ? Number(account.amount || 0) / (account.frequency === "annual" ? 12 : 1) : Number(account.amount || 0)), 0);
      if (["leaving", "waiting", "owed"].includes(bucket.id)) heading.append(element("span", "bucket-total", `${money(sum, bucket.id === "leaving")}${bucket.id === "leaving" ? " / mo" : ""}`));
      const list = element("div", "ledger-list");
      items.forEach((account) => {
        const row = button("", `account-row ${account.status === "done" ? "done" : ""} ${account.active === false ? "inactive" : ""}`, () => openAccount(account.id));
        row.setAttribute("aria-label", `${account.institution}, ${statusLabels[account.status] || account.status}, ${accountValue(account).value}. Open account details.`);
        row.dataset.accountId = account.id;
        const info = append(element("div", "account-info"), element("strong", "", account.institution));
        if (account.active === false) info.append(element("p", "inactive-label", `Stopped charging in ${String(account.last_seen || "").slice(0, 4) || "an earlier year"}`));
        else info.append(element("p", "", account.why_it_matters));
        const meta = element("div", "account-meta");
        (account.sources || []).forEach((source) => meta.append(element("span", "source-badge", source === "bank" ? "Bank statement" : "Email")));
        if (account.urgent && account.active && account.status !== "done") meta.append(element("span", "renewal-badge", account.days_until >= 0 ? `Due ${account.days_until === 0 ? "today" : `in ${account.days_until}d`}` : "Needs attention"));
        info.append(meta);
        const value = accountValue(account);
        const amount = append(element("div", "account-amount", value.value), element("span", "", value.unit));
        append(row, element("span", "account-letter", account.institution.charAt(0).toUpperCase()), info, amount, element("span", `status-chip status-${account.status}`, statusLabels[account.status] || account.status), element("span", "row-assignee", account.assigned_to || "Unassigned"), element("span", "row-chevron", "›"));
        list.append(row);
      });
      append(section, heading, list);
      $("ledger").append(section);
    });
    if (!$("ledger").children.length) $("ledger").append(element("p", "empty-activity", "No accounts in this section were found in the available records."));
  }

  function renderCoverage() {
    const hasBank = state.estate.accounts.some((account) => account.sources && account.sources.includes("bank"));
    const checklist = [
      { title: `Email (${number((state.estate.stats || {}).emails)} messages)`, checked: true },
      { title: "Chase bank statement", checked: hasBank, note: hasBank ? "Matched charges with email accounts and checked for recurring payments." : "Add a bank statement to find charges that don’t appear in email." },
      { title: "Credit report", note: "Request from the credit bureaus as executor." },
      { title: "Lost life insurance", note: "NAIC Life Insurance Policy Locator." },
      { title: "Unclaimed property", note: "MissingMoney.com and Michigan Unclaimed Property." },
      { title: "Last year’s tax return", note: "Every 1099 is an account worth checking." }
    ];
    $("coverage-list").replaceChildren(...checklist.map((item) => {
      const row = element("li", `coverage-item ${item.checked ? "checked" : ""}`);
      const icon = element("span", "coverage-check", item.checked ? "✓" : "");
      icon.setAttribute("aria-label", item.checked ? "Reviewed" : "Still to review");
      const content = append(element("div"), element("strong", "", item.title), item.note ? element("p", "", item.note) : null);
      return append(row, icon, content);
    }));
  }

  async function loadActivity() {
    const generation = ++state.activityGeneration;
    $("refresh-activity").disabled = true;
    $("activity-list").replaceChildren(element("p", "loading-text", "Reading your family’s activity…"));
    try {
      const response = await api("/api/activity");
      if (generation !== state.activityGeneration) return;
      const activities = Array.isArray(response) ? response : response.activity || [];
      if (!activities.length) {
        $("activity-list").replaceChildren(element("p", "empty-activity", "A shared place to start. Assign an account or update its progress, and your family’s next steps will appear here."));
        return;
      }
      $("activity-list").replaceChildren(...activities.slice(0, 6).map((entry) => {
        const account = state.estate.accounts.find((item) => item.id === entry.account_id);
        const institution = entry.institution || (account && account.institution) || "an account";
        let action;
        if (entry.action === "status:done") action = `completed ${institution}`;
        else if (entry.action === "status:in_progress") action = `started working on ${institution}`;
        else if (entry.action === "status:open") action = `reopened ${institution}`;
        else if (entry.action === "call:placed") action = `had Lastly call ${institution} (AI phone call)`;
        else if (entry.action === "claim:requested") action = `asked Lastly's agent to open a claim with ${institution}`;
        else if (entry.action === "claim:opened") action = `opened a claim for ${institution}`;
        else if (entry.action === "claim:rejected") action = `could not open a claim for ${institution}`;
        else if (entry.action === "call:browser") action = `talked with ${institution} (AI conversation)`;
        else if (String(entry.action).startsWith("assigned:")) action = `assigned ${institution} to ${entry.action.slice(9) || "the family"}`;
        else action = `${String(entry.action || "updated").replace(/[_:]/g, " ")} · ${institution}`;
        const actor = entry.actor || "Your family";
        const time = element("time", "", relativeTime(entry.created_at));
        if (entry.created_at) time.dateTime = entry.created_at;
        return append(element("div", "activity-item"), element("span", "activity-avatar", actor.charAt(0).toUpperCase()), append(element("div"), element("p", "", `${actor} ${action}`), time));
      }));
    } catch (error) {
      if (generation === state.activityGeneration) $("activity-list").replaceChildren(element("p", "inline-error", errorMessage(error)));
    } finally { if (generation === state.activityGeneration) $("refresh-activity").disabled = false; }
  }

  function relativeTime(value) {
    const timestamp = new Date(value).getTime();
    if (Number.isNaN(timestamp)) return "Recently";
    const minutes = Math.max(0, Math.floor((Date.now() - timestamp) / 60000));
    if (minutes < 1) return "Just now";
    if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
    if (minutes < 1440) return `${Math.floor(minutes / 60)} hour${minutes < 120 ? "" : "s"} ago`;
    return date(value);
  }

  function beginDrawer() {
    clearTimeout(state.pollTimer);
    if (state.drawerAbort) state.drawerAbort.abort();
    state.drawerAbort = new AbortController();
    const generation = ++state.drawerGeneration;
    if ($("drawer-shell").hidden) state.previousFocus = document.activeElement;
    $("drawer-shell").hidden = false;
    $("site-header").inert = true;
    $("main").inert = true;
    document.body.classList.add("drawer-open");
    $("drawer-content").replaceChildren();
    $("account-drawer").scrollTop = 0;
    $("drawer-close").focus();
    return generation;
  }

  function closeDrawer() {
    state.drawerGeneration++;
    state.currentAccount = null;
    if (state.drawerAbort) state.drawerAbort.abort();
    clearTimeout(state.pollTimer);
    $("drawer-shell").hidden = true;
    $("site-header").inert = false;
    $("main").inert = false;
    document.body.classList.remove("drawer-open");
    if (state.previousFocus && state.previousFocus.isConnected) state.previousFocus.focus({ preventScroll: true });
    else {
      const previousAccountId = state.previousFocus && state.previousFocus.dataset.accountId;
      const replacement = previousAccountId && [...$("ledger").querySelectorAll(".account-row")].find((row) => row.dataset.accountId === previousAccountId);
      if (replacement) replacement.focus({ preventScroll: true });
      else { $("accounts-title").setAttribute("tabindex", "-1"); $("accounts-title").focus({ preventScroll: true }); }
    }
  }

  function drawerSection(title) {
    return append(element("section", "drawer-section"), element("h3", "", title));
  }

  function openAccount(id, preferredEvidence) {
    const account = state.estate.accounts.find((item) => item.id === id);
    if (!account) { toast("This account is no longer available. Refresh the estate and try again."); return; }
    const generation = beginDrawer();
    state.currentAccount = id;
    const bucket = buckets.find((item) => item.id === account.bucket);
    const content = $("drawer-content");
    append(content, element("span", "eyebrow", bucket ? bucket.title : "Account details"));
    const heading = element("h2", "", account.institution);
    heading.id = "drawer-title";
    append(content, heading, element("p", "drawer-reason", account.why_it_matters));
    const value = accountValue(account);
    append(content, append(element("div", "drawer-top-amount", value.value), element("span", "", value.unit)));
    if (account.active === false) content.append(element("p", "drawer-source-line", `Stopped charging in ${String(account.last_seen).slice(0, 4)}. We found no recent recurring charge in the available records.`));
    if (account.sources && account.sources.length === 1 && account.sources[0] === "bank") content.append(element("p", "drawer-source-line", "No emails. Found only on her Chase statement."));
    else content.append(element("p", "drawer-source-line", [account.email_count ? `${account.email_count} email${account.email_count === 1 ? "" : "s"}` : null, account.first_seen ? `First seen ${date(account.first_seen)}` : null, account.last_seen ? `Last seen ${date(account.last_seen)}` : null].filter(Boolean).join(" · ")));
    renderStateForm(content, account, generation);
    const proof = drawerSection("The information behind this");
    append(proof, element("p", "", "Read the original source before taking the next step."));
    content.append(proof);
    renderEvidence(proof, account.evidence_ids || [], preferredEvidence, generation);
    const actions = drawerSection(actionLabels[account.action] || "Take the next step");
    append(actions, element("p", "", "We can help with a first draft or an approved call. Your family chooses what to send and when."));
    content.append(actions);
    renderActions(actions, account, generation);
  }

  function renderStateForm(parent, account, generation) {
    const section = drawerSection("Your family’s next step");
    const form = element("div", "state-form");
    const makeSelect = (id, label, options, value) => {
      const wrapper = element("div");
      const title = element("label", "field-label", label);
      title.htmlFor = id;
      const select = element("select");
      select.id = id;
      options.forEach(([optionValue, text]) => { const option = element("option", "", text); option.value = optionValue; select.append(option); });
      select.value = value;
      return { wrapper: append(wrapper, title, select), select };
    };
    const status = makeSelect("account-status", "Progress", Object.entries(statusLabels), account.status);
    const assignedOptions = [["", "Unassigned"], ["Daniel", "Daniel"], ["Sarah", "Sarah"], ["Me", "Me"]];
    if (account.assigned_to && !assignedOptions.some(([value]) => value === account.assigned_to)) assignedOptions.push([account.assigned_to, account.assigned_to]);
    const assigned = makeSelect("account-assignment", "Assign to", assignedOptions, account.assigned_to || "");
    append(form, status.wrapper, assigned.wrapper);
    const message = element("div", "drawer-save-message");
    message.setAttribute("role", "status");
    const update = async (field, value) => {
      status.select.disabled = assigned.select.disabled = true;
      message.className = "drawer-save-message";
      message.textContent = "Saving your family’s progress…";
      try {
        const updated = await patchAccount(account.id, { [field]: value });
        if (generation !== state.drawerGeneration) return;
        status.select.value = updated.status;
        assigned.select.value = updated.assigned_to || "";
        message.textContent = "Saved for your family.";
      } catch (error) {
        if (generation !== state.drawerGeneration) return;
        const current = state.estate.accounts.find((item) => item.id === account.id);
        status.select.value = current.status;
        assigned.select.value = current.assigned_to || "";
        message.className = "inline-error";
        message.textContent = errorMessage(error);
      } finally { if (generation === state.drawerGeneration) status.select.disabled = assigned.select.disabled = false; }
    };
    status.select.addEventListener("change", () => update("status", status.select.value));
    assigned.select.addEventListener("change", () => update("assigned_to", assigned.select.value || null));
    append(section, form, message);
    parent.append(section);
  }

  async function patchAccount(id, changes) {
    const response = await api(`/api/account/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(changes) });
    const updated = response.account || response;
    if (!updated.id) throw new Error("The account update was incomplete. Please refresh and check its progress.");
    const index = state.estate.accounts.findIndex((item) => item.id === id);
    if (index !== -1) state.estate.accounts[index] = updated;
    renderLedger();
    renderUrgent();
    renderTabs();
    loadActivity();
    return updated;
  }

  function renderEvidence(parent, ids, preferred, generation) {
    if (!ids.length) { parent.append(element("p", "loading-text", "No original source was provided for this account.")); return; }
    const tabs = element("div", "evidence-tabs");
    const card = element("div", "evidence-card");
    card.setAttribute("aria-live", "polite");
    let latestSelection = 0;
    async function select(id) {
      const selection = ++latestSelection;
      [...tabs.children].forEach((tab) => { const active = tab.dataset.evidenceId === id; tab.classList.toggle("active", active); tab.setAttribute("aria-pressed", String(active)); });
      card.replaceChildren(element("p", "loading-text", "Opening the original source…"));
      try {
        let source = state.evidence.get(id);
        if (!source) {
          source = await api(`/api/${id.startsWith("bank_") ? "bank" : "email"}/${encodeURIComponent(id)}`, { signal: state.drawerAbort.signal });
          state.evidence.set(id, source);
        }
        if (generation !== state.drawerGeneration || selection !== latestSelection) return;
        card.replaceChildren();
        if (id.startsWith("bank_")) {
          append(card, element("div", "evidence-detail", `Chase bank statement · ${id}`));
          [["Date", date(source.date)], ["Description", source.description || source.descriptor || ""], ["Amount", money(source.amount, true)], ["Account", source.account || "Chase checking"]].forEach(([label, value]) => card.append(append(element("div", "bank-fact"), element("span", "", label), element("strong", "", value))));
        } else {
          let sender = source.from || source.sender || source.from_email || "";
          if (sender && typeof sender === "object") sender = sender.email || sender.address || sender.name || "";
          append(card, element("div", "evidence-detail", [sender, date(source.date), id].filter(Boolean).join(" · ")), element("h4", "", source.subject || "Email"), element("div", "evidence-body", source.body || source.text || "This email has no readable text body."));
        }
      } catch (error) {
        if (generation === state.drawerGeneration && selection === latestSelection && error.name !== "AbortError") {
          const retry = button("Try again", "text-button", () => select(id));
          card.replaceChildren(element("p", "inline-error", errorMessage(error)), retry);
        }
      }
    }
    ids.forEach((id, index) => {
      const tab = button(`${id.startsWith("bank_") ? "Statement" : "Email"} ${index + 1}`, "evidence-tab", () => select(id));
      tab.dataset.evidenceId = id;
      tab.title = id;
      tabs.append(tab);
    });
    append(parent, tabs, card);
    select(ids.includes(preferred) ? preferred : ids[0]);
  }

  function renderActions(parent, account, generation) {
    const controls = element("div", "drawer-actions");
    const actionError = element("p", "inline-error");
    actionError.hidden = true;
    actionError.setAttribute("role", "alert");
    const letterContainer = element("div");
    const callContainer = element("div");
    const letterButton = button("Draft a letter ↗", "button button-secondary", async () => {
      letterButton.disabled = true;
      letterButton.textContent = "Writing a first draft…";
      actionError.hidden = true;
      try {
        const response = await api(demoPath(`/api/letter/${encodeURIComponent(account.id)}`), { method: "POST", signal: state.drawerAbort.signal });
        if (generation !== state.drawerGeneration) return;
        if (typeof response.letter !== "string" || !response.letter.trim()) throw new Error("The letter is empty. Please try drafting it again.");
        renderLetter(letterContainer, response.letter, account);
        letterButton.textContent = "Draft a new letter ↗";
      } catch (error) {
        if (generation === state.drawerGeneration && error.name !== "AbortError") showError(actionError, errorMessage(error));
      } finally { if (generation === state.drawerGeneration) { letterButton.disabled = false; if (!letterContainer.children.length) letterButton.textContent = "Draft a letter ↗"; } }
    });
    const callButton = button("Call them for me ↗", "button button-primary", () => {
      callButton.hidden = true;
      renderCallForm(callContainer, account, generation);
    });
    const talkButton = button("Talk to them 🎙", "button button-primary", () => {
      callContainer.replaceChildren();
      if (state.voice && state.voice.account.id === account.id) renderVoiceLive(callContainer, account);
      else renderVoiceForm(callContainer, account, generation);
    });
    append(controls, talkButton, letterButton, callButton);
    const taskContainer = element("div");
    if (account.action !== "claim") {
      const agentButton = button("Let agents handle it ↗", "button button-secondary", async () => {
        agentButton.disabled = true;
        actionError.hidden = true;
        try {
          const task = await api(`/api/agent-task/${encodeURIComponent(account.id)}`, { method: "POST" });
          if (generation === state.drawerGeneration) renderAgentTask(taskContainer, account, task, generation);
        } catch (error) {
          if (generation === state.drawerGeneration) showError(actionError, errorMessage(error));
        } finally { agentButton.disabled = false; }
      });
      controls.insertBefore(agentButton, letterButton);
      api(`/api/agent-task/${encodeURIComponent(account.id)}`, { signal: state.drawerAbort.signal }, false).then((task) => {
        if (generation === state.drawerGeneration && task.status) renderAgentTask(taskContainer, account, task, generation);
      }).catch(() => {});
    }
    const claimContainer = element("div");
    if (account.action === "claim") {
      // Agent-to-agent: Lastly's Fetch.ai agent asks the insurer's agent to open the claim.
      const claimButton = button("Let agents handle it ↗", "button button-secondary", async () => {
        claimButton.disabled = true;
        claimButton.textContent = "Contacting the insurer’s agent…";
        actionError.hidden = true;
        try {
          const claim = await api(`/api/claim/${encodeURIComponent(account.id)}`, { method: "POST" });
          if (generation !== state.drawerGeneration) return;
          claimButton.hidden = true;
          renderClaim(claimContainer, account, claim, generation);
        } catch (error) {
          if (generation === state.drawerGeneration && error.name !== "AbortError") showError(actionError, errorMessage(error));
          claimButton.disabled = false;
          claimButton.textContent = "Let agents handle it ↗";
        }
      });
      controls.prepend(claimButton);
      api(`/api/claim/${encodeURIComponent(account.id)}`, { signal: state.drawerAbort.signal }, false).then((claim) => {
        if (generation !== state.drawerGeneration || !claim || !claim.status) return;
        claimButton.hidden = claim.status !== "failed" && claim.status !== "rejected";
        renderClaim(claimContainer, account, claim, generation);
      }).catch(() => { /* No claim yet. */ });
    }
    append(parent, controls, actionError, taskContainer, claimContainer, letterContainer, callContainer);
    if (state.voice && state.voice.account.id === account.id) {
      callButton.hidden = true;
      renderVoiceLive(callContainer, account);
    } else if (state.calls.has(account.id)) {
      callButton.hidden = true;
      const ongoing = state.calls.get(account.id);
      const callState = renderCallState(callContainer, account, ongoing.status, ongoing.summary);
      if (!["done", "failed"].includes(ongoing.status) && ongoing.conversation_id) pollCall(account, ongoing, callState, generation);
    }
  }

  function renderAgentTask(parent, account, task, generation, waited = 0) {
    if (generation !== state.drawerGeneration) return;
    clearTimeout(parent.taskPollTimer);
    const container = element("div", "call-state");
    const titles = { queued: "Waiting for Lastly’s Fetch.ai agent…", sent: "Contacting the company agent…", awaiting_details: "Company agent requested account details…", details_sent: "Waiting for the company’s confirmation…", completed: "Agent request complete", pending: "The family has steps to complete", rejected: "The company agent declined this request", failed: "The agent request could not be delivered" };
    container.append(element("strong", "", titles[task.status] || "Agent conversation in progress"));
    container.append(element("p", "review-note", "Fetch.ai conversation with a demonstration company agent."));
    const transcript = element("div", "voice-transcript");
    (task.transcript || []).forEach((line) => {
      const row = element("p", "voice-line");
      append(row, element("span", "voice-speaker", `${line.speaker} agent`), document.createTextNode(line.message));
      transcript.append(row);
    });
    container.append(transcript);
    if (task.reference_number) container.append(element("p", "reference", `Reference: ${task.reference_number}`));
    if (task.required_documents && task.required_documents.length) container.append(element("p", "", task.required_documents.join(" · ")));
    const finished = ["completed", "pending", "rejected", "failed"].includes(task.status);
    if (!finished && waited >= 30) container.append(element("p", "inline-error", "Still waiting for the agents. Check that fetch_agent.py and insurer_agent.py are running."));
    parent.replaceChildren(container);
    if (task.status === "completed") {
      const current = state.estate.accounts.find((item) => item.id === account.id);
      if (current && current.status !== "done") {
        current.status = "done";
        renderLedger(); renderTabs(); loadActivity();
        const select = $("account-status");
        if (select) select.value = "done";
      }
    }
    if (finished) return;
    parent.taskPollTimer = setTimeout(async () => {
      if (generation !== state.drawerGeneration) return;
      try {
        const next = await api(`/api/agent-task/${encodeURIComponent(account.id)}`, { signal: state.drawerAbort.signal }, false);
        renderAgentTask(parent, account, next, generation, waited + 2);
      } catch (error) {
        if (error.name !== "AbortError" && generation === state.drawerGeneration) {
          container.append(element("p", "inline-error", errorMessage(error)), button("Check agents again", "text-button", () => renderAgentTask(parent, account, task, generation, waited)));
        }
      }
    }, 2000);
  }

  function renderLetter(parent, text, account) {
    parent.replaceChildren();
    const label = element("label", "field-label letter-label", "Your letter — review and edit before sending");
    label.htmlFor = "letter-editor";
    const editor = element("textarea", "letter-editor");
    editor.id = "letter-editor";
    editor.value = text;
    const actions = element("div", "letter-actions");
    const copy = button("Copy letter", "text-button", async () => {
      try {
        if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(editor.value);
        else { editor.focus(); editor.select(); if (!document.execCommand("copy")) throw new Error("Clipboard access is unavailable. Select the letter text and copy it manually."); }
        toast("Letter copied. Review the contact details before sending.");
      } catch (error) { toast(errorMessage(error)); }
    });
    const download = button("Download text ↓", "text-button", () => {
      const blob = new Blob([editor.value], { type: "text/plain;charset=utf-8" });
      saveBlob(blob, `Lastly-${account.institution.replace(/[^a-z0-9]+/gi, "-")}-letter.txt`);
    });
    append(actions, copy, download);
    append(parent, label, editor, element("p", "review-note", "Replace the contact placeholders and check the details. This draft has not been sent."), actions);
  }

  function saveBlob(blob, name) {
    const url = URL.createObjectURL(blob);
    const anchor = element("a");
    anchor.href = url;
    anchor.download = name;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function renderCallForm(parent, account, generation) {
    const form = element("form", "call-form");
    const label = element("label", "field-label", "Phone number to call");
    label.htmlFor = "call-number";
    const input = element("input");
    input.id = "call-number";
    input.type = "tel";
    input.autocomplete = "tel";
    input.placeholder = "+17345551234";
    input.pattern = "\\+[1-9][0-9]{7,14}";
    input.required = true;
    input.maxLength = 16;
    input.setAttribute("aria-describedby", "call-review-note");
    const note = element("p", "review-note", `By placing this call, you approve an AI assistant calling this number on your family’s behalf about ${account.institution}. It will identify itself as an AI. Use a verified teammate’s number for the live demonstration.`);
    note.id = "call-review-note";
    const submit = button("Approve & place call ↗", "button button-primary");
    submit.type = "submit";
    const error = element("p", "inline-error");
    error.hidden = true;
    error.setAttribute("role", "alert");
    append(form, label, input, note, submit, error);
    parent.append(form);
    input.focus();
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!/^\+[1-9]\d{7,14}$/.test(input.value.trim())) { showError(error, "Enter an international phone number, including + and the country code."); input.focus(); return; }
      error.hidden = true;
      submit.disabled = true;
      submit.textContent = "Placing your approved call…";
      const toNumber = input.value.trim();
      const attemptId = `${account.id}|${toNumber}`;
      let attempt = state.callAttempts.get(attemptId);
      try {
        if (!attempt) {
          // Web Crypto supplies randomness even on browsers without randomUUID.
          const bytes = crypto.getRandomValues(new Uint8Array(16));
          bytes[6] = (bytes[6] & 15) | 64;
          bytes[8] = (bytes[8] & 63) | 128;
          const hex = [...bytes].map((value) => value.toString(16).padStart(2, "0")).join("");
          attempt = { key: `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}` };
          state.callAttempts.set(attemptId, attempt);
        }
        // Keep a submitted call request alive if the drawer closes: the user already approved it.
        // A failed network request can still place a call. Preserve the key for
        // an explicit retry and never automatically repeat a call submission.
        const response = await api(`/api/call/${encodeURIComponent(account.id)}`, { method: "POST", headers: { "Idempotency-Key": attempt.key }, body: JSON.stringify({ to_number: toNumber }) }, false);
        if (response.success !== true) throw new ApiError(response.message || "The call wasn’t placed. Check your voice service setup and try again.", 0, true);
        state.callAttempts.delete(attemptId);
        const ongoing = { conversation_id: response.conversation_id, status: response.conversation_id ? "initiated" : "placed", summary: null };
        state.calls.set(account.id, ongoing);
        if (generation !== state.drawerGeneration) { toast(`Your approved call to ${account.institution} was placed.`); return; }
        form.hidden = true;
        const callState = renderCallState(parent, account, ongoing.status, null);
        if (ongoing.conversation_id) pollCall(account, ongoing, callState, generation);
      } catch (callError) {
        if (callError instanceof ApiError && !callError.uncertain) state.callAttempts.delete(attemptId);
        if (callError.status === 401) {
          csrfToken = "";
          $("lock-estate").hidden = true;
          try { await unlockEstate(); }
          catch (_) { /* The family may cancel; a call is never retried here. */ }
        }
        const message = callError.uncertain
          ? `${errorMessage(callError)} The call’s outcome is unconfirmed. Checking or retrying this same number will reuse your original request.`
          : errorMessage(callError);
        if (generation === state.drawerGeneration) showError(error, message);
        else toast(`Your approved call could not be placed. ${errorMessage(callError)}`);
      } finally {
        if (generation === state.drawerGeneration) { submit.disabled = false; submit.textContent = "Approve & place call ↗"; }
      }
    });
  }

  function renderClaim(parent, account, claim, generation, waited = 0) {
    const container = element("div", "call-state claim-state");
    container.setAttribute("role", "status");
    container.setAttribute("aria-live", "polite");
    const waiting = claim.status === "queued" || claim.status === "sent";
    const titles = { queued: "Lastly’s agent is preparing the claim…", sent: `Waiting for ${account.institution}’s claims agent…`, opened: `Claim opened · ${claim.claim_number || ""}`, rejected: "The insurer’s agent could not open this claim", failed: "The claim could not be delivered" };
    const heading = element("strong", "", titles[claim.status] || "Claim in progress");
    if (waiting) heading.prepend(element("span", "pulse-dot"));
    container.append(heading);
    if (claim.status === "opened") {
      container.append(element("p", "", "Agent-to-agent over Fetch.ai: Lastly’s agent sent the policyholder’s details and the insurer’s agent opened the claim."));
      if (Array.isArray(claim.required_documents) && claim.required_documents.length) {
        container.append(element("p", "", "Documents the beneficiary still needs to send:"));
        const list = element("ul", "claim-documents");
        claim.required_documents.forEach((item) => list.append(element("li", "", item)));
        container.append(list);
      }
      if (claim.message) container.append(element("p", "review-note", claim.message));
    } else if (claim.message) container.append(element("p", "", claim.message));
    else if (waiting && waited >= 45) container.append(element("p", "inline-error", "No answer yet. Check that fetch_agent.py and insurer_agent.py are both running."));
    parent.replaceChildren(container);
    if (claim.status === "opened") {
      const current = state.estate.accounts.find((item) => item.id === account.id);
      if (current && current.status === "open") { current.status = "in_progress"; renderLedger(); renderTabs(); loadActivity(); const select = $("account-status"); if (select) select.value = "in_progress"; }
    }
    if (!waiting || generation !== state.drawerGeneration) return;
    state.pollTimer = setTimeout(async () => {
      if (generation !== state.drawerGeneration) return;
      try {
        const next = await api(`/api/claim/${encodeURIComponent(account.id)}`, { signal: state.drawerAbort.signal }, false);
        renderClaim(parent, account, next, generation, waited + 2);
      } catch (error) { if (error.name !== "AbortError") renderClaim(parent, account, claim, generation, waited + 2); }
    }, 2000);
  }

  // In-browser ElevenLabs conversation: the laptop's mic and speakers stand in for a phone line.
  function loadVoiceSdk() {
    if (window.ElevenLabsClient) return Promise.resolve(window.ElevenLabsClient);
    if (!state.sdk) {
      state.sdk = new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src = "/static/vendor/elevenlabs-client.js";
        script.onload = () => window.ElevenLabsClient ? resolve(window.ElevenLabsClient) : reject(new Error("The voice library did not load."));
        script.onerror = () => { state.sdk = null; reject(new Error("The voice library could not be loaded. Refresh and try again.")); };
        document.head.append(script);
      });
    }
    return state.sdk;
  }

  function renderVoiceForm(parent, account, generation) {
    const form = element("div", "call-form");
    const note = element("p", "review-note", `Speak directly to Lastly using your microphone. For this ${account.institution} role-play, you are the company representative and Lastly speaks for the family. Your replies determine the conversation.`);
    const microphoneLabel = element("label", "field-label", "Microphone");
    microphoneLabel.htmlFor = "voice-microphone";
    const microphone = element("select");
    microphone.id = "voice-microphone";
    const defaultDevice = element("option", "", "System default microphone");
    defaultDevice.value = "";
    microphone.append(defaultDevice);
    const chooseMicrophone = button("Find microphones", "text-button", async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        stream.getTracks().forEach((track) => track.stop());
        const devices = await navigator.mediaDevices.enumerateDevices();
        microphone.replaceChildren(defaultDevice);
        devices.filter((device) => device.kind === "audioinput" && device.deviceId !== "default").forEach((device, index) => {
          const option = element("option", "", device.label || `Microphone ${index + 1}`);
          option.value = device.deviceId;
          microphone.append(option);
        });
      } catch (_) { showError(error, "Allow microphone access in your browser to choose a microphone."); }
    });
    const error = element("p", "inline-error");
    error.hidden = true;
    error.setAttribute("role", "alert");
    const start = button("Approve & start conversation 🎙", "button button-primary", async () => {
      if (state.voice) { showError(error, "Another conversation is already in progress. End it first."); return; }
      start.disabled = true;
      start.textContent = "Connecting…";
      error.hidden = true;
      const voice = { account, conversation: null, mode: "listening", lines: [], finished: false, container: null, muted: false };
      state.voice = voice;
      try {
        const [session, sdk] = await Promise.all([
          api(`/api/voice/${encodeURIComponent(account.id)}`, { method: "POST" }, false),
          loadVoiceSdk(),
        ]);
        if (generation !== state.drawerGeneration) { if (state.voice === voice) state.voice = null; return; }
        voice.conversation = await sdk.Conversation.startSession({
          signedUrl: session.signed_url,
          connectionType: "websocket",
          textOnly: false,
          inputDeviceId: microphone.value || undefined,
          dynamicVariables: session.dynamic_variables,
          workletPaths: { rawAudioProcessor: "/static/vendor/raw-audio-processor.js", audioConcatProcessor: "/static/vendor/audio-concat-processor.js" },
          onModeChange: ({ mode }) => { voice.mode = mode; paintVoice(voice); },
          onConversationCreated: (conversation) => { voice.conversation = conversation; },
          // Expressive voices emit tone tags such as "[calm]"; they shape speech and are not shown.
          onMessage: ({ source, message }) => {
            const text = String(message || "").replace(/\[[a-z][a-z \-]{0,24}\]\s*/gi, "").trim();
            if (text) { voice.lines.push({ source, message: text }); paintVoice(voice); }
          },
          onError: (message) => { voice.error = typeof message === "string" ? message : "The voice connection reported an error."; paintVoice(voice); },
          onDisconnect: () => finishVoice(voice),
        });
        if (voice.finished) return;
        voice.conversation.setMicMuted(false);
        voice.meterTimer = setInterval(() => {
          if (voice.level && voice.level.isConnected && voice.conversation) voice.level.value = voice.muted ? 0 : voice.conversation.getInputVolume();
        }, 150);
        if (generation === state.drawerGeneration) { parent.replaceChildren(); renderVoiceLive(parent, account); }
      } catch (startError) {
        if (voice.conversation) { try { await voice.conversation.endSession(); } catch (_) {} }
        if (state.voice === voice) state.voice = null;
        if (startError.status === 401) {
          csrfToken = "";
          $("lock-estate").hidden = true;
          try { await unlockEstate(); } catch (_) { /* The family may cancel. */ }
        }
        const denied = startError && (startError.name === "NotAllowedError" || /permission/i.test(String(startError.message)));
        if (generation === state.drawerGeneration) showError(error, denied ? "Microphone access was blocked. Allow it in the browser’s address bar, then try again." : errorMessage(startError));
      } finally {
        if (generation === state.drawerGeneration && !(state.voice && state.voice.conversation)) { start.disabled = false; start.textContent = "Approve & start conversation 🎙"; }
      }
    });
    append(form, note, microphoneLabel, microphone, chooseMicrophone, start, error);
    parent.append(form);
  }

  function renderVoiceLive(parent, account) {
    const voice = state.voice;
    if (!voice) return;
    const container = element("div", "call-state voice-live");
    container.setAttribute("role", "status");
    container.setAttribute("aria-live", "polite");
    voice.container = container;
    parent.append(container);
    paintVoice(voice);
  }

  function paintVoice(voice) {
    const container = voice.container;
    if (!container || !container.isConnected) return;
    const heading = element("strong", "", voice.mode === "speaking" ? `Lastly is speaking with ${voice.account.institution}` : `Listening to ${voice.account.institution}…`);
    heading.prepend(element("span", "pulse-dot"));
    const transcript = element("div", "voice-transcript");
    voice.lines.slice(-8).forEach((line) => {
      const row = element("p", line.source === "ai" ? "voice-line voice-ai" : "voice-line");
      append(row, element("span", "voice-speaker", line.source === "ai" ? "Lastly (AI)" : `You · ${voice.account.institution} representative`), document.createTextNode(line.message));
      transcript.append(row);
    });
    const end = button(voice.ending ? "Ending…" : "End conversation", "button button-secondary voice-end", () => {
      if (voice.ending || !voice.conversation) return;
      voice.ending = true;
      paintVoice(voice);
      voice.conversation.endSession().catch(() => finishVoice(voice));
    });
    end.disabled = Boolean(voice.ending);
    container.replaceChildren(heading, transcript);
    const microphoneStatus = element("p", "", voice.muted ? "Microphone muted" : "Microphone on · speak naturally or type a reply below");
    voice.level = element("meter", "voice-level");
    voice.level.min = 0;
    voice.level.max = 1;
    voice.level.value = 0;
    voice.level.setAttribute("aria-label", "Microphone input level");
    const mute = button(voice.muted ? "Unmute microphone" : "Mute microphone", "text-button", () => {
      voice.muted = !voice.muted;
      voice.conversation.setMicMuted(voice.muted);
      paintVoice(voice);
    });
    append(container, microphoneStatus, voice.level, mute);
    if (!voice.replyForm) {
      voice.replyForm = element("form", "voice-reply-form");
      const label = element("label", "field-label", "Your reply as the company representative");
      const input = element("input");
      input.id = "voice-reply";
      label.htmlFor = input.id;
      input.placeholder = "Type a reply if your microphone isn’t working";
      input.maxLength = 2000;
      const send = button("Send reply", "button button-secondary");
      send.type = "submit";
      append(voice.replyForm, label, input, send);
      voice.replyForm.addEventListener("submit", (event) => {
        event.preventDefault();
        const message = input.value.trim();
        if (!message || voice.ending || voice.finished) return;
        voice.conversation.sendUserMessage(message);
        voice.lines.push({ source: "user", message });
        input.value = "";
        paintVoice(voice);
      });
    }
    container.append(voice.replyForm);
    if (voice.error) container.append(element("p", "inline-error", voice.error));
    container.append(end);
  }

  async function finishVoice(voice) {
    if (voice.finished) return;
    voice.finished = true;
    clearInterval(voice.meterTimer);
    if (state.voice === voice) state.voice = null;
    const account = voice.account;
    const conversationId = voice.conversation && voice.conversation.getId && voice.conversation.getId();
    const parent = voice.container && voice.container.isConnected ? voice.container.parentElement : null;
    const generation = state.drawerGeneration;
    if (!conversationId) {
      if (parent) parent.replaceChildren(element("p", "inline-error", "The conversation ended before it started. Try again."));
      return;
    }
    try {
      await api(`/api/voice/${encodeURIComponent(account.id)}/conversation`, { method: "POST", body: JSON.stringify({ conversation_id: conversationId }) }, false);
      const ongoing = { conversation_id: conversationId, status: "processing", summary: null };
      state.calls.set(account.id, ongoing);
      if (!parent || generation !== state.drawerGeneration) { toast(`Your conversation with ${account.institution} ended. Open the account to see the outcome.`); return; }
      parent.replaceChildren();
      const callState = renderCallState(parent, account, ongoing.status, null);
      pollCall(account, ongoing, callState, generation);
    } catch (error) {
      if (parent) parent.replaceChildren(element("p", "inline-error", `The conversation ended, but its outcome could not be saved. ${errorMessage(error)}`));
      else toast(`The conversation outcome could not be saved. ${errorMessage(error)}`);
    }
  }

  function renderCallState(parent, account, status, summary) {
    const container = element("div", "call-state");
    container.setAttribute("role", "status");
    container.setAttribute("aria-live", "polite");
    parent.append(container);
    updateCallState(container, account, status, summary);
    return container;
  }

  function updateCallState(container, account, status, summary) {
    const titles = { initiated: `Calling ${account.institution}…`, "in-progress": "Call in progress", in_progress: "Call in progress", processing: "Reviewing the call outcome…", done: "Call complete", failed: "The call could not be completed", placed: "Call placed" };
    const heading = element("strong", "", titles[status] || "Call in progress");
    if (!["done", "failed", "placed"].includes(status)) heading.prepend(element("span", "pulse-dot"));
    container.replaceChildren(heading);
    if (summary && typeof summary === "object") {
      if (typeof summary.cancelled === "boolean") container.append(element("p", "", summary.cancelled ? "Cancellation confirmed. This account is marked complete." : "The representative did not clearly confirm completed cancellation. If you know it was completed, set Progress to Done above."));
      if (summary.reference_number) container.append(element("p", "reference", `Reference: ${summary.reference_number}`));
      const nextSteps = Array.isArray(summary.next_steps) ? summary.next_steps.join(" ") : summary.next_steps;
      if (nextSteps) container.append(element("p", "", nextSteps));
    } else if (typeof summary === "string" && summary) container.append(element("p", "", summary));
    else if (status === "placed") container.append(element("p", "", "The call was accepted. Confirm the outcome with the recipient, then update the account’s progress."));
    else if (status === "failed") {
      container.append(element("p", "", "Check the phone number and voice service configuration before trying again."));
      const generation = state.drawerGeneration;
      container.append(button("Try another approved call", "text-button", () => {
        const parent = container.parentElement;
        state.calls.delete(account.id);
        parent.replaceChildren();
        renderCallForm(parent, account, generation);
      }));
    }
    else if (status === "done") container.append(element("p", "", "No cancellation confirmation was returned. Review the outcome and update progress manually."));
  }

  async function pollCall(account, ongoing, container, generation) {
    if (generation !== state.drawerGeneration || !ongoing.conversation_id) return;
    try {
      const response = await api(`/api/call/${encodeURIComponent(ongoing.conversation_id)}`, { signal: state.drawerAbort.signal });
      if (generation !== state.drawerGeneration) return;
      ongoing.status = response.status || "in-progress";
      ongoing.summary = response.summary || response.transcript_summary || null;
      if (ongoing.status === "done" && ongoing.summary && typeof ongoing.summary === "object" && ongoing.summary.cancelled === true) {
        const current = state.estate.accounts.find((item) => item.id === account.id);
        if (current && current.status !== "done") {
          try {
            const updated = await patchAccount(account.id, { status: "done" });
            if (generation !== state.drawerGeneration) return;
            const statusSelect = $("account-status");
            if (statusSelect) statusSelect.value = updated.status;
          } catch (error) {
            updateCallState(container, account, ongoing.status, ongoing.summary);
            container.append(element("p", "inline-error", `The call confirmed cancellation, but progress could not be saved. ${errorMessage(error)}`));
            return;
          }
        }
      }
      updateCallState(container, account, ongoing.status, ongoing.summary);
      if (["done", "failed"].includes(ongoing.status)) return;
      state.pollTimer = setTimeout(() => pollCall(account, ongoing, container, generation), 3000);
    } catch (error) {
      if (generation !== state.drawerGeneration || error.name === "AbortError") return;
      container.replaceChildren(element("strong", "", "Call status is temporarily unavailable"), element("p", "", "Your call may still be in progress."), element("p", "inline-error", errorMessage(error)), button("Check call status again", "text-button", () => pollCall(account, ongoing, container, generation)));
    }
  }

  async function ask(event) {
    event.preventDefault();
    const question = $("ask-input").value.trim();
    if (!question) { $("ask-input").focus(); return; }
    $("ask-button").disabled = true;
    $("ask-result").hidden = false;
    $("ask-result").replaceChildren(element("p", "loading-text", "Looking through the records for your answer…"));
    try {
      const response = await api(demoPath("/api/ask"), { method: "POST", body: JSON.stringify({ question }) });
      if (typeof response.answer !== "string") throw new Error("An answer wasn’t returned. Please try your question again.");
      const result = $("ask-result");
      result.replaceChildren(element("p", "", response.answer));
      if (response.evidence_ids && response.evidence_ids.length) {
        const links = element("div", "proof-links");
        response.evidence_ids.forEach((id) => {
          const owner = state.estate.accounts.find((account) => (account.evidence_ids || []).includes(id));
          const link = button(`${id.startsWith("bank_") ? "Statement" : "Email"} · ${id} ↗`, "proof-link", () => {
            if (owner) openAccount(owner.id, id);
            else {
              const generation = beginDrawer();
              state.currentAccount = null;
              const title = element("h2", "", "The original source");
              title.id = "drawer-title";
              $("drawer-content").append(title);
              const section = drawerSection("Evidence for your answer");
              $("drawer-content").append(section);
              renderEvidence(section, [id], id, generation);
            }
          });
          links.append(link);
        });
        result.append(links);
      }
    } catch (error) { $("ask-result").replaceChildren(element("p", "inline-error", errorMessage(error))); }
    finally { $("ask-button").disabled = false; }
  }

  $("continue-button").addEventListener("click", () => { screen("welcome-screen"); $("analyze-button").focus(); });
  $("back-plan-button").addEventListener("click", () => { screen("plan-screen"); $("continue-button").focus(); });
  $("analyze-button").addEventListener("click", analyze);
  $("ask-form").addEventListener("submit", ask);
  $("refresh-activity").addEventListener("click", loadActivity);
  $("drawer-close").addEventListener("click", closeDrawer);
  $("drawer-backdrop").addEventListener("click", closeDrawer);
  $("access-cancel").addEventListener("click", () => { if (!$("access-cancel").disabled) $("access-dialog").close(); });
  $("lock-estate").addEventListener("click", async () => {
    $("lock-estate").disabled = true;
    try {
      // Hang up a live voice conversation before locking; it is not left running unseen.
      if (state.voice && state.voice.conversation) { try { await state.voice.conversation.endSession(); } catch (_) { /* Already ended. */ } }
      await api("/api/session", { method: "DELETE" }, false);
      csrfToken = "";
      state.estate = null;
      state.evidence.clear();
      state.calls.clear();
      state.callAttempts.clear();
      location.reload();
    } catch (error) { toast(`The estate could not be locked. ${errorMessage(error)}`); }
    finally { $("lock-estate").disabled = false; }
  });
  document.addEventListener("keydown", (event) => {
    if ($("access-dialog").open) return; // The browser traps focus inside the password dialog.
    if ($("drawer-shell").hidden) return;
    if (event.key === "Escape") { event.preventDefault(); closeDrawer(); }
    if (event.key !== "Tab") return;
    const focusable = [...$("account-drawer").querySelectorAll('button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]')].filter((node) => node.getClientRects().length && !node.closest("[hidden]"));
    if (!focusable.length) { event.preventDefault(); $("account-drawer").focus(); return; }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && (document.activeElement === first || document.activeElement === $("account-drawer"))) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
  document.querySelector(".checklist-download").addEventListener("click", () => {
    const lines = [...$("coverage-list").children].map((item) => `${item.classList.contains("checked") ? "[x]" : "[ ]"} ${item.querySelector("strong").textContent}${item.querySelector("p") ? `\n    ${item.querySelector("p").textContent}` : ""}`);
    const text = `Lastly — Where else to look\n${(state.estate.persona || {}).name || "Family estate"} · ${date(state.estate.today)}\n\n${lines.join("\n\n")}\n\nFor legal questions, ask a probate lawyer.\n`;
    saveBlob(new Blob([text], { type: "text/plain;charset=utf-8" }), "Lastly-family-checklist.txt");
  });

  if (demo) screen("welcome-screen");
  api("/api/estate").then((estate) => {
    if (Array.isArray(estate.accounts) && !state.estate) { state.estate = estate; personalize(estate); }
  }).catch(() => {
    // A new installation has no estate until the first analysis; the start screen remains usable.
  });
})();
