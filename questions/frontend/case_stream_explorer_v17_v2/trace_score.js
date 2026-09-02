async (page) => {
  page.setDefaultTimeout(3200);
  page.setDefaultNavigationTimeout(5000);

  const targetBase = page.url().split("?", 1)[0];
  const makeCases = () => Array.from({ length: 60 }, (_, index) => {
    const number = index + 1;
    const id = `CASE-${String(number).padStart(3, "0")}`;
    const item = {
      id,
      title: `Case ${number} evidence review`,
      owner: ["Maya Chen", "Noah Kim", "Priya Shah", "Alvaro Ruiz", "Emile Roy", "Zoe Lee"][index % 6],
      team: ["Core", "Data", "Edge", "Growth", "Trust"][index % 5],
      status: ["open", "investigating", "resolved", "blocked"][index % 4],
      priority: ["P0", "P1", "P2", "P3"][index % 4],
      updatedAt: 1787565600000 - index * 3600000,
      tags: [`signal-${index % 7}`, `lane-${index % 3}`],
      summary: `Evidence summary for ${id}.`,
    };
    if (id === "CASE-005") Object.assign(item, {
      title: "Caféine gateway timeout",
      owner: "Zoëline Lee",
      team: "Core",
      status: "open",
      priority: "P0",
      tags: ["payments", "caféine"],
    });
    if (id === "CASE-012") Object.assign(item, {
      title: "Release evidence mismatch",
      team: "Data",
      status: "open",
      priority: "P1",
    });
    if (id === "CASE-013") Object.assign(item, {
      title: "Rejectable trust incident",
      team: "Trust",
      status: "investigating",
      priority: "P0",
    });
    return item;
  });
  const cases = makeCases();
  const results = {};
  const workflows = { browse_recovery: {}, concurrent_save: {} };
  const workflowErrors = {};

  await page.addInitScript((fixture) => {
    window.MODELDIAL_CASES = JSON.parse(JSON.stringify(fixture));
    window.__SAVE_CALLS = [];
    window.__SAVE_COUNTS = {};
    window.MODELDIAL_SAVE_CASE = (patch) => {
      const copy = JSON.parse(JSON.stringify(patch));
      window.__SAVE_CALLS.push(copy);
      const count = (window.__SAVE_COUNTS[copy.id] || 0) + 1;
      window.__SAVE_COUNTS[copy.id] = count;
      const delay = copy.id === "CASE-005"
        ? (count === 1 ? 320 : count === 2 ? 80 : 100)
        : copy.id === "CASE-013" ? 180 : 100;
      return new Promise((resolve, reject) => setTimeout(
        () => copy.id === "CASE-013"
          ? reject(new Error("mock rejection"))
          : resolve({ ...copy, serverRevision: `srv-${copy.revision}` }),
        delay,
      ));
    };
  }, cases);

  const record = (id, passed, evidence) => {
    results[id] = { passed: Boolean(passed), evidence };
  };
  const reset = async (suffix = "", width = 1440, height = 900) => {
    await page.setViewportSize({ width, height });
    await page.emulateMedia({ reducedMotion: "reduce", colorScheme: "light" });
    await page.goto("about:blank");
    await page.goto(`${targetBase}${suffix}`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(90);
    await page.evaluate(() => {
      window.__TRACE_POPSTATES = [];
      addEventListener("popstate", (event) => {
        window.__TRACE_POPSTATES.push(JSON.parse(JSON.stringify(event.state)));
      }, { capture: true });
    });
  };
  const rowIds = () => page.locator("[data-case-id]").evaluateAll(
    (rows) => rows.map((row) => row.dataset.caseId),
  );
  const row = async (id) => {
    const locator = page.locator(`[data-case-id='${id}']`);
    for (let index = 0; index < await locator.count(); index += 1) {
      if (await locator.nth(index).isVisible()) return locator.nth(index);
    }
    throw new Error(`case row missing:${id}`);
  };
  const setQuery = async (value) => {
    await page.locator("#search").fill(value);
    await page.waitForTimeout(155);
  };
  const clickFacet = async (host, name) => page.locator(`#${host} button`)
    .filter({ hasText: new RegExp(`^${name}$`) })
    .click();
  const openCase = async (id) => {
    const item = await row(id);
    await item.locator(`[data-open-id='${id}'],[data-open='${id}'],.case-main`).first().click();
  };
  const selectById = async (id) => {
    await setQuery(id);
    await page.getByRole("button", { name: `Toggle selection ${id}`, exact: true }).click();
    await page.waitForTimeout(15);
  };
  const metric = (id) => page.locator(`#metric-${id}`).textContent();
  const activeState = () => page.evaluate(() => {
    const active = [...document.querySelectorAll("[data-case-id][data-active='true']")];
    const focused = document.activeElement?.closest?.("[data-case-id]");
    return {
      count: active.length,
      id: active[0]?.dataset.caseId || "",
      focused: focused?.dataset.caseId || "",
      pos: active[0]?.getAttribute("aria-posinset") || "",
      size: active[0]?.getAttribute("aria-setsize") || "",
      scrollTop: document.querySelector("#virtual-list")?.scrollTop || 0,
    };
  });
  const checkpoint = async (label) => page.evaluate((checkpointLabel) => {
    const url = new URL(location.href);
    const rows = [...document.querySelectorAll("[data-case-id]")].map((item) => ({
      id: item.dataset.caseId || "",
      index: item.dataset.index || "",
      active: item.dataset.active || "",
      selected: item.dataset.selected || "",
      saving: item.dataset.saving || "",
      status: item.dataset.status || "",
      pos: item.getAttribute("aria-posinset") || "",
      size: item.getAttribute("aria-setsize") || "",
      height: Math.round(item.getBoundingClientRect().height * 1000) / 1000,
      transform: item.style.transform,
    }));
    const list = document.querySelector("#virtual-list");
    const spacer = document.querySelector("#virtual-spacer");
    const inspector = document.querySelector("#inspector");
    const inspectorBox = inspector?.getBoundingClientRect();
    const focused = document.activeElement?.closest?.("[data-case-id]");
    return {
      label: checkpointLabel,
      urlEntries: [...url.searchParams],
      hash: url.hash,
      query: document.querySelector("#search")?.value || "",
      sort: document.querySelector("#sort")?.value || "",
      team: document.querySelector("#team-facets [aria-pressed='true']")?.textContent || "",
      status: document.querySelector("#status-facets [aria-pressed='true']")?.textContent || "",
      matching: document.querySelector("#metric-matching")?.textContent || "",
      selectedCount: document.querySelector("#metric-selected")?.textContent || "",
      selectedVisible: rows.filter((item) => item.selected === "true").map((item) => item.id),
      savingVisible: rows.filter((item) => item.saving === "true").map((item) => item.id),
      activeId: rows.find((item) => item.active === "true")?.id || "",
      focusedId: focused?.dataset.caseId || "",
      scrollTop: list?.scrollTop || 0,
      spacerHeight: spacer ? Number.parseFloat(getComputedStyle(spacer).height) : 0,
      rows,
      inspectorOpen: inspector?.dataset.mobileOpen || "",
      inspectorBox: inspectorBox ? {
        x: Math.round(inspectorBox.x),
        y: Math.round(inspectorBox.y),
        width: Math.round(inspectorBox.width),
        height: Math.round(inspectorBox.height),
      } : null,
      alertText: document.querySelector("[role='alert']")?.textContent || "",
      liveText: document.querySelector("[role='status'][aria-live='polite']")?.textContent || "",
      horizontalOverflow: document.documentElement.scrollWidth > innerWidth + 1
        || document.body.scrollWidth > innerWidth + 1,
      popstates: window.__TRACE_POPSTATES || [],
    };
  }, label);
  const stateFor = async (id) => {
    await setQuery(id);
    const item = await row(id);
    return {
      id,
      status: await item.getAttribute("data-status"),
      saving: await item.getAttribute("data-saving"),
      selected: await item.getAttribute("data-selected"),
    };
  };

  try {
    await reset("?keep=1&q=case&team=Core&sort=owner#anchor");
    const initial = await checkpoint("initial-url");
    const initialIds = await rowIds();
    if (initialIds.length < 4) throw new Error("browse workflow needs four visible rows");

    let focused = await row(initialIds[0]);
    await focused.focus();
    await focused.press("PageDown");
    await page.waitForTimeout(25);
    focused = await row((await activeState()).id);
    await focused.press("PageDown");
    await page.waitForTimeout(35);
    const navigated = await activeState();
    const selectionIds = await rowIds();
    await page.getByRole("button", { name: `Toggle selection ${selectionIds[0]}`, exact: true }).click();
    await page.getByRole("button", { name: `Toggle selection ${selectionIds[3]}`, exact: true })
      .click({ modifiers: ["Shift"] });
    const selectedRange = await checkpoint("range-selected");
    await page.locator("#virtual-list").evaluate(
      (element, scrollTop) => { element.scrollTop = scrollTop; },
      navigated.scrollTop,
    );
    await page.waitForTimeout(30);
    focused = await row(navigated.id);
    await focused.focus();
    const beforeHistory = await checkpoint("before-history-change");

    await clickFacet("status-facets", "open");
    await page.locator("#sort").selectOption("id");
    await page.waitForTimeout(35);
    const changed = await checkpoint("changed-view");

    await page.goBack();
    await page.waitForTimeout(80);
    await page.goBack();
    await page.waitForTimeout(100);
    const restored = await checkpoint("restored-view");

    workflows.browse_recovery = {
      initial,
      initialIds,
      selectedRange,
      beforeHistory,
      changed,
      restored,
    };
    let postHistoryNavigation = restored;
    let restoredActive = restored.activeId;
    if (!restoredActive) {
      const visibleIds = await rowIds();
      if (!visibleIds.length) throw new Error("restored view has no rows");
      focused = await row(visibleIds[0]);
      await focused.focus();
      await focused.press("PageDown");
      await page.waitForTimeout(35);
      postHistoryNavigation = await checkpoint("post-history-navigation");
      restoredActive = postHistoryNavigation.activeId;
    }
    if (!restoredActive) throw new Error("post-history active row is empty");
    workflows.browse_recovery.postHistoryNavigation = postHistoryNavigation;
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(30);
    await openCase(restoredActive);
    await page.waitForTimeout(30);
    const mobileOpen = await checkpoint("mobile-inspector-open");
    workflows.browse_recovery.mobileOpen = mobileOpen;
    await page.getByRole("button", { name: "Close case inspector", exact: true }).first().click();
    await page.waitForTimeout(35);
    const mobileClosed = await checkpoint("mobile-inspector-closed");
    workflows.browse_recovery.mobileClosed = mobileClosed;
  } catch (error) {
    workflowErrors.browse_recovery = String(error);
  }

  try {
    await reset();
    for (const id of ["CASE-005", "CASE-012", "CASE-013"]) await selectById(id);
    const selected = await checkpoint("three-selected");

    await page.getByRole("button", { name: "Mark investigating", exact: true }).click();
    await page.waitForTimeout(25);
    const firstPending = await checkpoint("first-revision-pending");
    await page.getByRole("button", { name: "Mark resolved", exact: true }).click();
    await page.waitForTimeout(25);
    await page.locator("#sort").selectOption("owner");
    await page.waitForTimeout(25);
    const secondPending = await checkpoint("second-revision-pending");

    await page.waitForTimeout(360);
    const states = {};
    for (const id of ["CASE-005", "CASE-012", "CASE-013"]) states[id] = await stateFor(id);
    const settled = await checkpoint("settled");
    const calls = await page.evaluate(() => window.__SAVE_CALLS || []);
    workflows.concurrent_save = {
      selected,
      firstPending,
      secondPending,
      states,
      settled,
      calls,
    };
  } catch (error) {
    workflowErrors.concurrent_save = String(error);
  }

  const browse = workflows.browse_recovery;
  const save = workflows.concurrent_save;
  const relevantKeys = (state) => (state?.urlEntries || [])
    .map(([key]) => key)
    .filter((key) => ["q", "team", "status", "sort"].includes(key));
  const params = (state) => Object.fromEntries(state?.urlEntries || []);

  const initialParams = params(browse.initial);
  const changedParams = params(browse.changed);
  const restoredParams = params(browse.restored);
  const urlCertificate = initialParams.keep === "1"
    && initialParams.q === "case"
    && initialParams.team === "Core"
    && initialParams.sort === "owner"
    && browse.initial.hash === "#anchor"
    && browse.initial.team === "Core"
    && browse.initial.status === "All"
    && relevantKeys(browse.initial).join(",") === "q,team,sort"
    && changedParams.keep === "1"
    && changedParams.status === "open"
    && changedParams.sort === "id"
    && browse.changed.team === "Core"
    && browse.changed.status === "open"
    && relevantKeys(browse.changed).join(",") === "q,team,status,sort"
    && restoredParams.keep === "1"
    && browse.restored.sort === "owner"
    && browse.restored.team === "Core"
    && browse.restored.status === "All"
    && relevantKeys(browse.restored).join(",") === "q,team,sort";
  record("C01", urlCertificate, browse);

  const before = browse.beforeHistory || {};
  const restored = browse.restored || {};
  const afterHistory = browse.postHistoryNavigation || restored;
  const beforeRows = before.rows || [];
  const afterRows = afterHistory.rows || [];
  const restoredState = (restored.popstates || []).at(-1) || {};
  const virtualCertificate = before.activeId
    && before.focusedId === before.activeId
    && beforeRows.length > 0
    && beforeRows.length <= 16
    && beforeRows.every((item) => item.height === 56
      && item.transform === `translateY(${Number(item.index) * 56}px)`
      && item.pos === String(Number(item.index) + 1))
    && restoredState.activeId === before.activeId
    && Math.abs(Number(restoredState.scrollTop) - Number(before.scrollTop)) <= 1
    && afterHistory.activeId
    && afterHistory.focusedId === afterHistory.activeId
    && afterRows.length > 0
    && afterRows.length <= 16;
  record("C02", virtualCertificate, browse);

  const selectedStates = save.states || {};
  const selectionCertificate = browse.selectedRange?.selectedCount === "4"
    && browse.changed?.selectedCount === "4"
    && browse.restored?.selectedCount === "4"
    && save.selected?.selectedCount === "3"
    && save.secondPending?.selectedCount === "3"
    && save.settled?.selectedCount === "3"
    && ["CASE-005", "CASE-012", "CASE-013"].every(
      (id) => selectedStates[id]?.selected === "true",
    );
  record("C03", selectionCertificate, { browse, save });

  const calls = Array.isArray(save.calls) ? save.calls : [];
  const callsById = Object.fromEntries(["CASE-005", "CASE-012", "CASE-013"].map(
    (id) => [id, calls.filter((item) => item.id === id)],
  ));
  const revisionCertificate = calls.length === 6
    && Object.values(callsById).every((items) => items.length === 2
      && items[0].revision === 1
      && items[0].status === "investigating"
      && items[1].revision === 2
      && items[1].status === "resolved");
  record("C04", revisionCertificate, { calls, callsById, secondPending: save.secondPending });

  const rollbackCertificate = selectedStates["CASE-005"]?.status === "resolved"
    && selectedStates["CASE-012"]?.status === "resolved"
    && selectedStates["CASE-013"]?.status === "investigating"
    && Object.values(selectedStates).every((state) => state?.saving === "false")
    && save.settled?.alertText === "1 update failed. Restored CASE-013."
    && save.settled?.liveText === "All updates confirmed.";
  record("C05", rollbackCertificate, save);

  const open = browse.mobileOpen || {};
  const closed = browse.mobileClosed || {};
  const box = open.inspectorBox || {};
  const responsiveCertificate = open.inspectorOpen === "true"
    && box.x <= 1
    && box.y <= 1
    && box.width >= 389
    && box.height >= 843
    && closed.inspectorOpen === "false"
    && closed.focusedId === browse.postHistoryNavigation?.activeId
    && closed.horizontalOverflow === false;
  record("C06", responsiveCertificate, { open, closed, restored: browse.restored });

  return {
    schema_version: "frontend_v22_trace_payload_v1",
    certificate_results: results,
    workflows,
    workflow_errors: workflowErrors,
  };
}
