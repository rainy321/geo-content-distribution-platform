# GEO Windows desktop UI design QA

## Test target

- Reference: user-provided desktop UI reference image (not included in the source package).
- Implementation: Vue interface embedded by the Windows EXE; no public-site delivery is implied.
- Primary viewport: 1536 x 1024, DPR 1.
- Responsive viewport: 1280 x 800, DPR 1.
- Test state: authenticated local workspace, one real project available, no stored AI visibility run history. The chart therefore renders an explicit empty state instead of invented demonstration data.

## Evidence

- Full reference/implementation comparison: `geo-reference-comparison.png` (3072 x 1100).
- Dashboard at reference viewport: `geo-dashboard-reference-pass1.png` (1536 x 1024).
- AI visibility and diagnostic workflow: `geo-ai-visibility-pass1.png` (1536 x 1024).
- Dashboard responsive check: `geo-dashboard-1280.png` (1280 x 800).

The rendered QA images live in the private design-review workspace and are intentionally not copied into a source delivery. The filenames above preserve the audit trail without leaking machine-specific paths.

## Visual comparison

The implementation follows the reference's transferable design rules: oversized Chinese headline, restrained black/white palette, thin neutral borders, shallow radii, generous negative space, a data card opposite the headline, and a three-part GEO workflow below the fold. The desktop application's persistent sidebar is intentionally retained because it owns authenticated navigation, publishing state, account/logout controls, and the rest of the installed product. Replacing it with the reference's public landing-page header would remove required EXE functionality.

The existing repository logo asset is used rather than recreating the reference mark. The reference contains a demonstration trend; the implementation only plots actual visibility runs and shows a truthful empty state when none exist. Public marketing navigation and footer elements are intentionally absent because this is an installed desktop workspace, not a public website.

## Functional and interaction checks

- Dashboard data comes from existing dashboard, project, visibility, diagnosis, usage-cost, and publishing APIs.
- AI visibility retains project selection, query/model input, analysis, stored history, evidence, diagnosis dimensions, action items, cost detail, and prior-run comparison.
- Existing authentication, logout, real-publishing gate, publishing state/error, and platform-limit flows remain in the desktop shell.
- Direct dashboard and `#/ai-visibility` route loads were checked in an isolated browser profile against a local QA API copy.
- Project selection populated from the real QA database. Empty visibility history did not produce fabricated values.
- Browser console contained only the Vite connection messages; no uncaught JavaScript, reference, or type errors appeared.
- Automated tests cover dashboard presentation transforms, chronology, delta calculation, evidence extraction, diagnosis comparison, and preservation of the real AI visibility workflow.

## Responsive and revision history

The first browser-based 1536 x 1024 pass matched the reference hierarchy. The packaged EXE review then exposed an important desktop-shell difference: the persistent sidebar leaves less usable content width than a standalone browser viewport, so a 1320 px page breakpoint could still clip the signal card on a 1550 px Windows desktop. The final breakpoint is therefore 1600 px. At ordinary laptop widths the hero and live-signal card form a clean single-column stack; wider desktop windows retain the reference's two-column relationship. The root desktop shell now uses an explicit `auto minmax(0, 1fr)` grid, so the main region cannot retain a second full-viewport width beside the sidebar. The desktop status group also switches to its compact icon presentation at the same 1600 px boundary. Together these changes keep the backup and account controls inside the visible Windows work area.

## Remaining visual differences

- P3, accepted: the existing GEO logo silhouette differs from the sample brand mark. Keeping the real product asset avoids an invented or CSS-drawn logo.
- P3, accepted: the persistent desktop sidebar replaces the public-site top navigation.
- P3, accepted: an empty real-data chart replaces the reference's fabricated demonstration series when no visibility history exists.
- No open P0, P1, or P2 findings.

final result: passed
