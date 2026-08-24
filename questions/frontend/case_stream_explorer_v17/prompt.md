Complete the supplied `starter.html` as a production-quality, self-contained Case Stream Explorer.

Do not use tools, web search, network access, external files, shell commands, code execution, browsers, developer tools, or iterative debugging; solve only from this prompt and the supplied starter HTML.

Return only one complete HTML document. Do not use Markdown fences, explanations, external libraries, remote fonts, network requests, data URLs, or extra files. Preserve the **Case Stream** product name, injected API names, visible control labels, dark visual direction, and information architecture. The shell renders, but query state, virtualization, selection, and save reconciliation are deliberately incomplete.

## Injected runtime

The host defines `window.MODELDIAL_CASES`, an array whose size, order, teams, values, and accented text may vary. Each item has:

```js
{ id, title, owner, team, status, priority, updatedAt, tags, summary }
```

It also defines `window.MODELDIAL_SAVE_CASE({ id, status, revision })`, which resolves to the saved fields plus `serverRevision`, or rejects. Deep-clone the data and never mutate the injected array.

## Query and URL contract

- Search ID, title, owner, and tags case-insensitively after Unicode `NFKD` normalization and removal of combining marks; trim and collapse whitespace. Every whitespace-delimited token must match somewhere in the same case.
- Render dynamic single-choice facets for every injected team and status, each with an `All` choice. Provide sort options `Priority`, `Recently updated`, `Owner`, and `ID`.
- Priority order is `P0`, `P1`, `P2`, `P3`; recent is descending `updatedAt`; owner uses locale text order. Every sort ends with ascending ID as a deterministic tie-breaker.
- URL parameters are `q`, `team`, `status`, and `sort` in that canonical order. Omit default／empty values and preserve unrelated parameters and the hash. Search commits with `replaceState` after `120ms`; facet and sort changes use `pushState`. Initial URL and `popstate` restore controls, results, active row, and scroll position without reload.
- Show the filtered result count and a clear empty state.

## Fixed-height virtualization and keyboard

- `Case results` is a `336px`-high internal scroller. Every result occupies exactly `56px`; one spacer exposes `filteredCount × 56px` total height.
- Render only the visible window plus two overscan rows on each side, never more than `16` case rows. Rows use absolute `translateY(index × 56px)` geometry and expose their 1-based `aria-posinset` and total `aria-setsize`.
- ArrowDown／ArrowUp move the active row by one, Home／End move to the first／last result, and PageDown／PageUp move by five. Clamp at the ends, scroll the active row into view, preserve DOM focus after rerender, and expose exactly one active option.
- Pointer activation opens `Case inspector`; closing it restores focus to the originating row, including when it must be virtualized back into view.

## Selection and optimistic bulk updates

- Each row has `Toggle selection ID`. Shift-click selects the inclusive range from the last toggled row in the current filtered／sorted order. Selection survives filtering, sorting, scrolling, and inspector use; show an exact selected count.
- `Mark investigating` and `Mark resolved` update every selected case optimistically and call the save API exactly once per case. Revisions increase independently per ID. Keep selection and expose saving state.
- For `CASE-005`, response 1 may resolve after `320ms` and response 2 after `80ms`; an older response must never overwrite newer intent.
- `CASE-013` rejects after `180ms`. In a mixed bulk update, retain successful cases, restore only rejected cases to their last confirmed status, and show exactly `1 update failed. Restored CASE-013.` in one `role="alert"`.
- Keep one visible `role="status" aria-live="polite"` save status.

## Responsive and visual contract

- Preserve metrics, query controls, dynamic facets, virtual results, priority／status metadata, selection toolbar, inspector, and distinct focus／active／selected／saving states.
- At `1440 × 900`, results and inspector form a composed split view with no document horizontal overflow.
- At `768 × 1024`, controls wrap intentionally, the result list remains an internal scroller, and the document does not overflow horizontally.
- At `390 × 844`, facets scroll horizontally, rows remain readable, and the inspector is a full-viewport sheet. Both `Close case inspector` controls close it and restore the originating row.
- Interactive targets are at least `44px` high. Honor reduced motion and emit no representative console/page errors or external requests.

STARTER_HTML_BEGIN
{{STARTER_HTML}}
STARTER_HTML_END
