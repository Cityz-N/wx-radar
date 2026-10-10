# wx-radar - Météo-France storm radar for EuroScope (VATSIM)

Shows a live weather-radar overlay of **France** on your EuroScope radar screen, so that
controllers can see storms (CB) and heavy rain and vector traffic around them.

The system has two parts:

| Part | What it does | Where it lives |
|---|---|---|
| **This repository** | Every ~5 minutes: fetches the newest Météo-France radar mosaic, converts it to a small picture, measures how the storms are moving, and publishes everything on GitHub Pages. | GitHub (free, runs by itself) |
| **WxOverlay plugin** | Downloads that picture and draws it under the aircraft tags on your scope. | Your PC (`WxOverlay.dll` in EuroScope) |

> **For flight simulation (VATSIM) use only.** This is an unofficial tool built on open data.
> The picture is an approximation and can be wrong or late. **Never use it for real-world
> flight planning or navigation.**

---

## Quick start (controllers)

1. Copy `WxOverlay.dll` and `WxOverlay.ini` into the same folder (for example your EuroScope
   `Plugins` folder). EuroScope is a 32-bit program, so the plugin is 32-bit too.
2. Open `WxOverlay.ini` in Notepad and set:
   ```ini
   Source=MeteoFrance
   MeteoFranceUrl=https://cityz-n.github.io/wx-radar/
   ```
3. In EuroScope: **Other SET → Plug-ins → Load**, choose `WxOverlay.dll`.
4. Open a radar screen on France. After a few seconds the radar appears and the legend shows
   the time of the radar frame.

If the weather is hidden behind another plugin's drawing, unload and load the plugins in a
different order (draw order depends on load order).

---

## Commands

Type these in the EuroScope command line. They are not case-sensitive. Changes are saved in
`WxOverlay.ini`, so they are still there the next time you start EuroScope.

| Command | What it does |
|---|---|
| `.wx on` | Shows the overlay (and downloads fresh data). |
| `.wx off` | Hides the overlay. |
| `.wx toggle` | Switches between on and off. |
| `.wx opacity 0-100` | Overall transparency of the overlay. Lower = more see-through. Example: `.wx opacity 40` |
| `.wx source mf` | Use Météo-France data (needs `MeteoFranceUrl` in the ini). |
| `.wx source rv` | Use RainViewer instead (free, no setup, coarser, 10-minute updates). |
| `.wx trend 15` / `.wx trend 30` | Show the **outlook outline**: where the strong cells are expected in 15 or 30 minutes. |
| `.wx trend off` | Hide the outlook outline. |
| `.wx fallback on` | Switch to RainViewer automatically when the Météo-France data is old or unreachable (default). |
| `.wx fallback off` | Never switch automatically. |
| `.wx zoom 5-7` | Radar detail for **RainViewer only** (6 is the default, 7 is the finest). |
| `.wx reload` | Re-reads `WxOverlay.ini` after you edited it. No need to restart EuroScope. |
| `.wx status` | Shows the current state (on/off, source, outlook, fallback, opacity, errors) and a list of the commands. |

---

## Reading the display

**Colours** (radar echo intensity in dBZ; the exact values and colours are set in the ini):

| Colour | Default level | Meaning (indicative) |
|---|---|---|
| Pale blue | 30-34 dBZ | Moderate rain |
| Yellow | 35-39 dBZ | Heavy rain |
| Orange | 40-44 dBZ | Heavy rain, possibly embedded storm cells |
| Red | 45-49 dBZ ("CB?") | Very heavy rain, often thunderstorm cells |
| Magenta | 50+ dBZ ("CB") | Intense core, hail possible |

**White outline** (the *outlook*): the edge of the area where cells of at least 40 dBZ are
expected in 15 or 30 minutes. It is the current picture moved along with the storms, so it
tells you **where they are heading**, not whether they will grow or fade.

**Legend** (bottom right of the radar screen):

- the colour scale, plus a hollow box with `+30 min` when the outlook is on;
- `Source: Météo-France - radar frame 14:20Z` - the time of the radar frame (UTC);
- `cells moving towards 070 at 29 kt` - the average storm movement measured over the last
  ~15 minutes;
- `OLD DATA (xx min)` - the picture is more than 15 minutes old (30 for RainViewer);
- `FALLBACK: Météo-France data unavailable` - RainViewer is being shown instead; the plugin
  switches back by itself when fresh Météo-France data is available.

Optional: with `ShowNoData=1` in the ini, areas where there is **no radar coverage** are
lightly shaded in grey so that "blank" is not mistaken for "clear sky".

---

## Settings (`WxOverlay.ini`)

Edit with Notepad, then type `.wx reload`.

### `[Weather]`

| Key | Default | Meaning |
|---|---|---|
| `Enabled` | `1` | 1 = shown, 0 = hidden |
| `Opacity` | `60` | 0-100, overall transparency |
| `Source` | `RainViewer` | `RainViewer` or `MeteoFrance` |
| `MeteoFranceUrl` | *(empty)* | Address of the GitHub Pages site, e.g. `https://cityz-n.github.io/wx-radar/`. Empty = stay on RainViewer. |
| `Trend` | `30` | Outlook outline: `0` (off), `15` or `30` minutes |
| `TrendMinDbz` | `40` | Only cells at least this strong are outlined |
| `TrendColor` | `FFFFFF` | Outline colour (RRGGBB) |
| `TrendOpacity` | `90` | Outline opacity, 0-100 |
| `AutoFallback` | `1` | Switch to RainViewer when Météo-France data is old or unreachable |
| `FallbackAfterMinutes` | `20` | What "old" means for the fallback (5-120) |
| `ShowNoData` | `0` | 1 = lightly shade areas without radar coverage |
| `Zoom` | `6` | RainViewer detail, 5-7 |
| `BlockSize` | `6` | Size in screen pixels of each coloured square (3-16). Smaller = smoother but slower. |

### `[Levels]` - your own colour thresholds

Up to five levels. For each radar echo, the level with the highest `Min` that the echo
reaches decides the colour. Echoes below the lowest `Min` are not drawn.

| Key (N = 1 to 5) | Meaning |
|---|---|
| `LevelN_Min` | Threshold in dBZ. `0` switches the level off. Values below 15 make no difference. |
| `LevelN_Color` | Colour as `RRGGBB` (e.g. `FF0000` is red) |
| `LevelN_Opacity` | 0-100, extra transparency for this level |
| `LevelN_Label` | Optional text shown in the legend |

Example - show only the heavy stuff (≥ 40 dBZ):

```ini
Level1_Min=40
Level1_Color=FF8C00
Level2_Min=45
Level2_Color=E61414
Level3_Min=50
Level3_Color=C800C8
Level4_Min=0
Level5_Min=0
```

---

## Good to know

- **How current is it?** A radar frame becomes available from Météo-France about 2-3 minutes
  after its time. The overlay normally shows it within ~1-2 minutes after that, so expect the
  picture to be about **4-6 minutes behind real time**. The legend always shows the frame time.
- **It shows rain, not clouds.** Radar sees precipitation. It does not show cloud tops, nor does
  it give any information about altitude.
- **How the intensity is obtained.** The data is Météo-France's 500 m rainfall product (5-minute
  accumulation). It is converted to rain rate, then to an *equivalent* radar intensity in dBZ
  with the standard Marshall-Palmer relation (Z = 200·R^1.6). So the dBZ values are an
  approximation, not the raw radar reflectivity, and very intense cells may look somewhat
  different from a real reflectivity display.
- **The outlook is a simple extrapolation.** The storm movement is measured by comparing the
  current frame with the one from about 15 minutes earlier (phase correlation on windows of
  about 160 km), and the current picture is then moved along. It does not know about cells
  that grow, die, split or appear. Treat it as "where it is heading", not as a forecast.
  If there is too little rain to measure movement, the outline is simply not shown.
- **Areas without radar** (far offshore, some border regions, radars that are down) are
  empty - which is not the same as clear sky. Use `ShowNoData=1` if you want them marked.
- **Radar only.** Pilot reports, SIGMETs and METARs remain the authority on weather.

---

## For the maintainer

### How the publishing works

`.github/workflows/radar.yml` runs in a chain: each run waits until Météo-France offers a
frame newer than the one on the site, converts it with `convert.py`, publishes it to GitHub
Pages, and then starts the next run. A 15-minute schedule restarts the chain if it ever stops.

| File | Purpose |
|---|---|
| `convert.py` | Download, convert, measure movement, write `latest.json`, the frame PNGs and a preview page |
| `.github/workflows/radar.yml` | The chain described above |
| `discover.py` | One-off diagnostic script (shows what the Météo-France files contain) |
| `.github/workflows/discover.yml` | Runs `discover.py` manually |

Published files: `latest.json` (what the plugin reads), `frames/<time>.png` (radar picture,
grey value = dBZ, `0` = no echo, `255` = no radar data), `frames/<time>_f15.png` and
`_f30.png` (outlook pictures), and `preview.png` / `index.html` for a human check.

### Pause and resume

- **Pause everything:** *Actions → Publish radar → "…" menu → Disable workflow*.
- **Resume:** *Enable workflow*, then *Run workflow* (tick "Publish right now" to skip the wait).
- Controllers can simply type `.wx off` to hide the overlay on their side.

### Things that need attention now and then

- **API key expiry.** The Météo-France API key has a validity period chosen when it was created.
  When it expires, runs fail with an "invalid key" message and the overlay shows `OLD DATA`
  (then falls back to RainViewer). Create a new key on the Météo-France portal and update the
  repository secret `MF_API_KEY` (*Settings → Secrets and variables → Actions*).
- **Inactivity.** GitHub disables scheduled workflows after 60 days without repository
  activity. If the overlay shows old data for a long time, check the Actions tab and re-enable.
- **Failures.** Occasionally Météo-France is unreachable; the converter retries several times.
  Isolated red runs are normal and the chain continues.

### Building the plugin

Visual Studio 2022, "MFC DLL" project, **x86 / Release**, one source file (`WxOverlay.cpp`),
the EuroScope plugin SDK (`EuroScopePlugIn.h`, `EuroScopePlugInDll.lib`), MFC statically
linked. See `README.txt` next to the plugin source.

---

## Credits and licences

- Radar data: **Source: Météo-France**, published as open data (Licence Ouverte / Etalab).
  Check Météo-France's current terms before reusing the data elsewhere.
- Optional fallback: weather data by **RainViewer** (rainviewer.com), used under their free
  terms for personal and small community use; the credit is shown in the legend.
- Unofficial. Not affiliated with Météo-France, RainViewer, EuroScope, VATSIM or the authors of
  other EuroScope plugins.
