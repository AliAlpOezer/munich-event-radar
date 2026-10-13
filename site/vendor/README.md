# Vendored files

Served from this site rather than a third-party CDN (ADR 0005), unchanged from upstream.
`.gitattributes` keeps them byte-exact; `_headers` caches them for a year, so a new version
gets a new file name.

| File | Upstream | sha256 |
|---|---|---|
| `supabase-js-2.117.2.js` | `package/dist/umd/supabase.js` in https://registry.npmjs.org/@supabase/supabase-js/-/supabase-js-2.117.2.tgz (MIT, `supabase-js-LICENSE.txt`) | `59d39487c3589843b410322d8a3d562ce022aba1e5ccb16898ef3fb2a0da2ecd` |
| `fonts/geist-v5-latin.woff2` | https://fonts.gstatic.com/s/geist/v5/gyByhwUxId8gMEwcGFU.woff2 (OFL 1.1, `fonts/geist-LICENSE.txt`) | `19f9c92546aa300c312235e3125af1b81394d8db9a4bc4a425cd5b641d2d54e1` |
| `fonts/geist-v5-latin-ext.woff2` | https://fonts.gstatic.com/s/geist/v5/gyByhwUxId8gMEwSGFWfOw.woff2 (OFL 1.1, `fonts/geist-LICENSE.txt`) | `824f485b5d26e2f2da3c2b236132ece1bc8e4e43373452950bb0e40548b4313f` |
| `fonts/geist-mono-v6-latin.woff2` | https://fonts.gstatic.com/s/geistmono/v6/or3nQ6H-1_WfwkMZI_qYFrcdmg.woff2 (OFL 1.1, `fonts/geist-LICENSE.txt`) | `684ad5b531f81d43c1e8c7038262d5db7cdc1f68006e04d6c7769efa8d33c8cc` |
| `fonts/geist-mono-v6-latin-ext.woff2` | https://fonts.gstatic.com/s/geistmono/v6/or3nQ6H-1_WfwkMZI_qYFrkdmgPn.woff2 (OFL 1.1, `fonts/geist-LICENSE.txt`) | `1a189eb997c3e2ece68373e387afaec9e8617424186c4b1ab3cff7c54ba6223b` |
| `fonts/space-grotesk-v22-latin.woff2` | https://fonts.gstatic.com/s/spacegrotesk/v22/V8mDoQDjQSkFtoMM3T6r8E7mPbF4Cw.woff2 (OFL 1.1, `fonts/space-grotesk-LICENSE.txt`) | `0640890476fc1198ab4de571fb658de443c4d85b66466ec09534a8737ab1ce9d` |
| `fonts/space-grotesk-v22-latin-ext.woff2` | https://fonts.gstatic.com/s/spacegrotesk/v22/V8mDoQDjQSkFtoMM3T6r8E7mPb94C-s0.woff2 (OFL 1.1, `fonts/space-grotesk-LICENSE.txt`) | `952dddb45d2f96f71cbf3b7f510b24379afc3c89ea02fcf89d377b45d62c0166` |

To update: download the new tarball, copy `package/dist/umd/supabase.js` to
`supabase-js-<version>.js`, record its sha256 here, change the `<script>` in `index.html`,
and delete the old file.

The fonts are the files Google Fonts serves for the page's families and weights (Geist
400-700, Geist Mono 400-500, Space Grotesk 500 and 700), latin and latin-ext subsets only;
the `@font-face` rules at the top of `style.css` carry their unicode ranges. The licenses
are from https://github.com/google/fonts (`ofl/geist`, `ofl/spacegrotesk`). To update: fetch
the `css2` stylesheet with a current browser's user agent, download the latin and latin-ext
files, name them `<family>-<version>-<subset>.woff2` after the version in their URL, and
change the rules and the preload in `index.html`.
