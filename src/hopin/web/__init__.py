"""The page build, the request's language, the build's files, the health route, the ICE server list and the TURN
provider adapter.

The build's `immutable/` files are cached `private, max-age=31536000, immutable` by the `/_app` mount itself
(`routes.py`), on its successful answers only, because the files are behind the session gate. The exceptions are the
built sign-in pages' stylesheets and icon and the fonts those stylesheets reference, which are served the same line to
anyone; every other `/_app` file is `no-cache`. The page's own stylesheet (`hopin.css`) and icon are part of that build,
imported by the root layout, so they carry a content hash in their name and a release that changes them is fetched by
its new name. No file is served under a fixed name, because an edge cache may store such a file beyond its `no-cache`.
"""
