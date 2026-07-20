# Animagine Studio

A two-part setup that turns a free Google Colab GPU into a personal
Animagine XL 4.0 render server, with a web app you run on your laptop to
control it from any device on your wifi.

Model: [cagliostrolab/animagine-xl-4.0](https://huggingface.co/cagliostrolab/animagine-xl-4.0)

## How it fits together

```
 [Phone / tablet / laptop]  --wifi-->  [Your laptop: server.py]  --internet-->  [Colab: colab_backend.py]
        (browser UI)                    (proxy + static files)        (ngrok tunnel)         (GPU, the model)
```

- **`colab_backend.py`** runs in Google Colab. It loads Animagine XL 4.0 on
  the Colab GPU and exposes it as an HTTP API, tunneled to a public URL with
  ngrok.
- **`frontend/server.py`** runs on your laptop. It serves the web app to
  every device on your wifi network and forwards generation requests to the
  Colab backend, so the browser never talks to Colab directly.
- **`frontend/static/`** is the web app itself: prompt box with danbooru tag
  autocomplete, batch size, resolution/aspect ratio, advanced sampling
  settings, and a masonry gallery with a slideshow-style viewer.
- **`frontend/data/build_tag_index.py`** + **`frontend/tag_lookup.py`** are
  the tag autofill: a one-time script turns a danbooru `tags.csv` export
  into a local SQLite index, and the frontend queries it directly — no
  internet call, no live Danbooru API, at request time.

## 1. Start the Colab backend

1. Open a new [Google Colab](https://colab.research.google.com) notebook and
   upload/paste the contents of `colab_backend.py` into a single cell.
2. **Runtime > Change runtime type > GPU** (T4 is enough).
3. Get a free ngrok authtoken at
   [dashboard.ngrok.com/get-started/your-authtoken](https://dashboard.ngrok.com/get-started/your-authtoken)
   and paste it into the `NGROK_AUTH_TOKEN` field near the top of the script.
4. Optionally set your own `API_KEY`; if you leave it blank, one is
   generated for you.
5. Run the cell. It will download the model, start the server, and keep
   running — that's expected, it *is* the server. When it's ready you'll see
   a box printed with your **Backend URL** and **API Key**. Copy both.
6. Keep the cell running while you want to render. Closing it, or Colab's
   session timing out, stops the server (you'll need to rerun it and update
   the URL/key in the frontend, since ngrok issues a new URL each time).

## 2. Build the local tag autofill index

The autofill dropdown is powered entirely by a local SQLite index built from
a danbooru tag export — no network calls, no rate limits.

1. Put `tags.csv` in `frontend/data/`. Needs at least the columns `name`,
   `post_count`, `category`, `is_deprecated`.
2. (Optional) Put `tag_aliases.csv` in the same folder — columns
   `antecedent_name`, `consequent_name` — so searching an old/renamed tag
   still resolves to the current one (shown as `old name → current name` in
   the dropdown).
3. Run the build:
   ```bash
   cd frontend/data
   python build_tag_index.py
   ```
   This produces `tags.db` (took ~45s / ~900k tags on a full danbooru
   export). Re-run it any time you refresh `tags.csv`. Artist tags
   (category 1) and deprecated tags are excluded from the index by design.

## 3. Start the frontend on your laptop

Requires Python 3.9+.

```bash
cd frontend
pip install -r requirements.txt
python server.py
```

The terminal prints two URLs:

- `http://127.0.0.1:5000` — for this laptop only
- `http://<your-lan-ip>:5000` — open this one on your phone, tablet, or any
  other device connected to the **same wifi network**

## 4. Connect the frontend to your Colab backend

1. Open the app in your browser, click the ⚙ settings icon.
2. Paste the **Backend URL** and **API Key** printed by the Colab cell.
3. Click **Test connection** — it should say "Connected."
4. Click **Done**. The status pill in the top bar turns green.

## 5. Generate images

- **Tags**: type danbooru-style tags separated by commas. After 2+
  characters, a dropdown predicts matching tags from your local index —
  matched anywhere in the name (not just the start, e.g. "ela" finds
  "hasumi_elan"), ranked by popularity, color-coded by category, with the
  matched letters bolded and renamed tags shown as `old → current`. Press
  **Tab** or **Enter** to instantly complete the top prediction, use ↑/↓ to
  pick another, or click/tap any suggestion. Whatever's shown with spaces
  gets inserted with underscores (`purple eyes` → `purple_eyes`), matching
  danbooru's actual tag format.
- **Quick tags**: one-click toggles for the model's recommended quality tags
  (masterpiece, high score, great score, absurdres).
- **Negative prompt**: pre-filled with the model card's recommended negative
  prompt — edit freely.
- **Images per batch**: 1–8 images per click of Generate.
- **Resolution**: portrait/landscape/square/tall/wide presets matching
  Animagine's trained resolutions, or a custom width/height (multiples of 64).
- **Advanced**: guidance scale (CFG), sampling steps, and seed (-1 = random;
  a fixed seed is reused as a base and incremented across a batch, so you get
  related-but-different variations you can reproduce later).
- **Results** appear in a Pinterest-style masonry gallery. Click any image to
  open a full-screen viewer with a filmstrip of the whole batch along the
  bottom, arrow-key navigation, and a download button. Use **Download all**
  to grab the whole batch as a .zip.

## Notes

- `frontend/data/tags.csv` and the generated `frontend/data/tags.db` are not
  included in this project — they're large (hundreds of MB) and specific to
  whichever danbooru export you're using. Supply your own `tags.csv` and run
  the build step above.
- Only one browser session needs to be generating at a time — the Colab
  backend processes one request at a time on the free GPU.
- A free Colab session disconnects after a period of inactivity or after a
  few hours; when that happens, rerun `colab_backend.py` and update the URL
  in the frontend's settings (ngrok gives a new URL each run on the free
  tier).
- The `X-API-Key` check exists because ngrok URLs are public — without it,
  anyone who found your URL could use your GPU time. Don't share the printed
  API key outside your own devices.
- Generated images are not saved to disk automatically; download the ones
  you want to keep. Reloading the frontend page clears the gallery (your
  prompt and settings are kept, via the browser's local storage).
