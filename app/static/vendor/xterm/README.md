# Vendored xterm.js assets

These files are self-hosted copies of the xterm.js terminal library and its
addons. They are committed on purpose: the terminal page (`/terminal`) must not
depend on a third-party CDN.

## Why vendored

The app serves every response with a strict Content Security Policy
(`script-src 'self'; style-src 'self' 'unsafe-inline'`, see
`app/api/app.py`). No CDN origin is allow-listed, so scripts and stylesheets
loaded from `cdn.jsdelivr.net` are **blocked by the browser**. That made
`new Terminal(...)` throw `ReferenceError: Terminal is not defined` on the
terminal page, which left the "Connecting to VPS..." overlay spinning forever in
every context, including installed home-screen PWAs.

Self-hosting also means the installed PWA can start the terminal shell offline,
which a CDN dependency can never provide.

## Contents

| File | Package | Version |
| --- | --- | --- |
| `xterm.min.js` | `@xterm/xterm` | 5.5.0 |
| `xterm.min.css` | `@xterm/xterm` | 5.5.0 |
| `addon-fit.min.js` | `@xterm/addon-fit` | 0.10.0 |
| `addon-web-links.min.js` | `@xterm/addon-web-links` | 0.11.0 |

Downloaded from `https://cdn.jsdelivr.net/npm/<package>@<version>/...`. License
texts are kept next to each bundle (`*.LICENSE`); all three packages are MIT
licensed, copyright the xterm.js authors.

## Updating

```bash
cd app/static/vendor/xterm
curl -sSfL -o xterm.min.js          https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/lib/xterm.min.js
curl -sSfL -o xterm.min.css         https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/css/xterm.min.css
curl -sSfL -o addon-fit.min.js      https://cdn.jsdelivr.net/npm/@xterm/addon-fit@0.10.0/lib/addon-fit.min.js
curl -sSfL -o addon-web-links.min.js https://cdn.jsdelivr.net/npm/@xterm/addon-web-links@0.11.0/lib/addon-web-links.min.js
```

When you bump a version, also bump the `?v=` query in `app/static/terminal.html`
and the copy in `app/static/sw.js` (pre-cache list) so installed PWAs pick up
the new bundle instead of a stale cached one.
