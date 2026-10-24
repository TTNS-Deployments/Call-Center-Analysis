# Vendored front-end libraries

Served from the app's own static files (whitenoise) so pages do not depend on public CDNs, and so the
Content-Security-Policy can allow scripts from `'self'` only. Files are copies of the npm packages (their SHA-256 matched the package hash published by jsDelivr when they were
added) with one change: the trailing `//# sourceMappingURL=...` comment was removed, because the `.map` files are not
shipped and `collectstatic` (whitenoise manifest storage) fails on a reference to a missing file.

| File | Package | Source | License |
|---|---|---|---|
| `htmx-2.0.4.min.js` | htmx.org 2.0.4 | `dist/htmx.min.js` | 0BSD |
| `chart-4.4.1.umd.js` | chart.js 4.4.1 | `dist/chart.umd.js` (already minified) | MIT |
| `powerbi-client-2.23.1.min.js` | powerbi-client 2.23.1 | `dist/powerbi.min.js` | MIT |

To upgrade, download the new version from `https://cdn.jsdelivr.net/npm/<package>@<version>/<path>`,
rename it with the new version and update the `{% static %}` references in `templates/`.
