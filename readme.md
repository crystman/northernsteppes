# Northern Steppes

## Overview

The site is generated via [Zola](https://www.getzola.org/documentation/getting-started/overview/), a static-site-generator. All the content here gets converted into HTML/CSS/JS when a change is made, and is hosted on Github Pages. 

## Website Content

Everything in the `content/` directory is responsible for creating the content on the website. Pages can either be [Markdown](https://www.markdownguide.org/getting-started/) files (e.g. `content/bylaws.md`) or a folder with a `index.md` file inside (e.g. `content/proficiencies/`). Zola requires that all Markdown files have a header that's surrounded with `+++` - check out the files for examples.

## Local Development

The site is built with [Zola](https://www.getzola.org/). **Use Zola 0.22.1** —
this must match the version pinned in `.github/workflows/publish.yaml`.

> **Do not install "the latest" Zola.** 0.23 is a breaking release (Tera v2;
> macros, imports and shortcodes were all removed) and **cannot build this
> site** — it fails on the first `{% import %}` in `templates/index.html`.
> Migrating the templates to 0.23 is planned but not yet done.

### Install Zola 0.22.1

**Windows** (note `winget install getzola.zola` gives you 0.23.x, which will not work):

```powershell
$dest = "$env:USERPROFILE\tools\zola-0.22.1"
New-Item -ItemType Directory -Force -Path $dest | Out-Null
Invoke-WebRequest -Uri "https://github.com/getzola/zola/releases/download/v0.22.1/zola-v0.22.1-x86_64-pc-windows-msvc.zip" -OutFile "$dest\zola.zip"
Expand-Archive -Force "$dest\zola.zip" -DestinationPath $dest
```

Then either add `%USERPROFILE%\tools\zola-0.22.1` to your PATH, or call
`~\tools\zola-0.22.1\zola.exe` directly.

**macOS / Linux** — download the matching archive from the
[v0.22.1 release](https://github.com/getzola/zola/releases/tag/v0.22.1) and put
`zola` on your PATH.

Verify with `zola --version` — it must print `zola 0.22.1`.

### Build and preview

```bash
zola serve
```

Serves a live-reloading preview at <http://127.0.0.1:1111>; edits to `content/`,
`templates/` and `sass/` refresh the browser automatically.

```bash
zola build
```

One-off build into `public/`. A clean build reports `Creating 48 pages (3 orphan)
and 4 sections`; any `ERROR` output means the site would fail to deploy.

Both commands also write a resized-image cache into `static/processed_images/`.
That directory is generated — it is ignored by git and you can safely delete it.

VS Code users: **Terminal → Run Task** has `Zola: serve` and `Zola: build`
preconfigured, and the recommended `karunamurti.tera` extension adds template
syntax highlighting.
