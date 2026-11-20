# Vendored browser libraries

The panel works offline. These files are served by `watch_kiro.py` from an explicit allowlist
from the repository's `assets/vendor/` directory. They are unmodified npm release files.

| File | Package | Version | License |
| --- | --- | --- | --- |
| `marked.umd.js` | [marked](https://www.npmjs.com/package/marked) `lib/marked.umd.js` | 18.0.11 | MIT (`marked.LICENSE.txt`) |
| `purify.min.js` | [dompurify](https://www.npmjs.com/package/dompurify) `dist/purify.min.js` | 3.4.17 | Apache-2.0 or MPL-2.0 (`dompurify.LICENSE-*.txt`) |

SHA-256 of the shipped files:

```
69451c8541c9c1e7a4bf3ffc6f73c4d89633de92bfbe3e484dfe182ef8091f88  marked.umd.js
cd37898f9782adf54820c413191b5691803f0f137c799d6edcfd38523b4c2c31  purify.min.js
```

Both files end with a `sourceMappingURL` comment. The `.map` files are intentionally not shipped,
so browser developer tools may log a harmless 404 for them.

To update, replace the files with a newer npm release, update the table and hashes, and run
`python3 -m unittest discover -s tests` and `npm test`.

## Provider marks

`viewer.html` embeds the Claude and Kiro website icons as local SVG symbols (retrieved 2026-11-19 from https://claude.com/icon.svg and https://kiro.dev/icon.svg). These identify the respective CLI providers; marks belong to their owners. No external icon requests are made by the panel.
