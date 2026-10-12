# Vendored files

Served from this site rather than a third-party CDN (ADR 0005), unchanged from upstream.
`.gitattributes` keeps them byte-exact; `_headers` caches them for a year, so a new version
gets a new file name.

| File | Upstream | sha256 |
|---|---|---|
| `supabase-js-2.117.2.js` | `package/dist/umd/supabase.js` in https://registry.npmjs.org/@supabase/supabase-js/-/supabase-js-2.117.2.tgz (MIT, `supabase-js-LICENSE.txt`) | `59d39487c3589843b410322d8a3d562ce022aba1e5ccb16898ef3fb2a0da2ecd` |

To update: download the new tarball, copy `package/dist/umd/supabase.js` to
`supabase-js-<version>.js`, record its sha256 here, change the `<script>` in `index.html`,
and delete the old file.
