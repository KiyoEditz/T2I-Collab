# ==============================================================================
# SDXL Render Server (for Google Colab)
# Default model: https://huggingface.co/cagliostrolab/animagine-xl-4.0
# (swap MODEL_ID below for any SDXL-compatible checkpoint/finetune)
#
# This turns the Colab GPU into a render backend. It loads the model once,
# exposes a small HTTP API (FastAPI), and tunnels that API to a public URL
# with ngrok so your laptop's frontend (running on your local wifi) can send
# generation requests to it.
#
# HOW TO USE
#   1. Runtime > Change runtime type > select a GPU (T4 is enough).
#   2. Get a free ngrok authtoken: https://dashboard.ngrok.com/get-started/your-authtoken
#      Paste it into NGROK_AUTH_TOKEN below.
#   3. (Optional) Set API_KEY to a password of your choice. If left blank, a
#      random one is generated for you and printed below — anyone who has
#      your public ngrok URL AND this key can use your GPU, so keep it private.
#   4. (Optional) Set MODEL_ID to a different SDXL checkpoint/finetune, and/or
#      list LoRA styles under LORAS — see the comments on each below. The
#      frontend will offer any configured LORAS as a style dropdown.
#   5. Run this whole file as a single Colab cell. It will keep running
#      (that's expected — it IS the server). Copy the printed "Backend URL"
#      and "API Key" into the frontend app's settings panel.
#   6. Leave this cell running for as long as you want to render images.
#      Stopping the cell (or the Colab session timing out) shuts the server down.
# ==============================================================================

# --- 0. Settings you may want to edit -----------------------------------------
NGROK_AUTH_TOKEN = ""  # @param {type:"string"}   <- required, see step 2 above
API_KEY = ""           # @param {type:"string"}   <- optional, leave blank to auto-generate
PORT = 8000

# Base checkpoint. Defaults to Animagine XL 4.0, but any SDXL-compatible
# checkpoint works: a Hugging Face repo id ("author/model-name") or, if
# you've uploaded your own finetune to the Colab filesystem/Drive, a local
# path (e.g. "/content/drive/MyDrive/my-finetune").
MODEL_ID = "cagliostrolab/animagine-xl-4.0"  # @param {type:"string"}

# Optional LoRA styles/finetunes the frontend can offer in a dropdown selector.
# Each entry is loaded once at startup and switched in per-request — no
# reload needed to change styles. Leave the list empty to skip this entirely
# (the frontend just won't show a style selector).
#
# "repo_id" can be a Hugging Face repo id or a local path; "weight_name" is
# the specific .safetensors file inside it (omit/None if the repo only has
# one weights file). "trigger_word" (optional) is automatically prepended to
# the prompt whenever this style is selected. "default_scale" (0.0-2.0) is
# the LoRA strength used unless the frontend requests a different one.
#
# Example:
# LORAS = [
#     {
#         "id": "my_style",
#         "name": "My Custom Style",
#         "repo_id": "username/my-lora-repo",
#         "weight_name": "my_style.safetensors",
#         "trigger_word": "mystyle",
#         "default_scale": 0.8,
#     },
# ]
LORAS = []

# --- 1. Install required libraries --------------------------------------------
# (peft is required for LoRA support — pipe.load_lora_weights()/set_adapters())
!pip install -q -U diffusers transformers accelerate safetensors peft
!pip install -q -U fastapi "uvicorn[standard]" pyngrok nest_asyncio python-multipart

import base64
import io
import random
import secrets
import time

import nest_asyncio
import torch
import uvicorn
from diffusers import StableDiffusionXLPipeline, EulerAncestralDiscreteScheduler
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pyngrok import ngrok

# ------------------------------------------------------------------------------
# 2. LOAD THE MODEL
#    Uses the lpw_stable_diffusion_xl custom pipeline (recommended by the model
#    card) for better handling of long/weighted prompts. MODEL_ID (set above)
#    can point at any SDXL-compatible checkpoint, not just Animagine.
# ------------------------------------------------------------------------------
print(f"Loading {MODEL_ID}... this takes a minute or two.")

pipe = StableDiffusionXLPipeline.from_pretrained(
    MODEL_ID,
    dtype=torch.float16,
    use_safetensors=True,
    custom_pipeline="lpw_stable_diffusion_xl",
    add_watermarker=False,
)
pipe.to("cuda")

# Recommended sampler per the model card: Euler Ancestral (Euler a)
pipe.scheduler = EulerAncestralDiscreteScheduler.from_config(pipe.scheduler.config)

# Keeps memory usage friendlier on free-tier GPUs (e.g. T4) when generating
# several images or at higher resolutions. Wrapped in try/except because some
# diffusers versions don't expose these on the lpw_stable_diffusion_xl custom
# pipeline class — safe to skip if so, generation still works fine.
try:
    pipe.enable_vae_slicing()
except AttributeError:
    pass
try:
    pipe.enable_xformers_memory_efficient_attention()
except Exception:
    pass  # fine if xformers isn't available — torch's SDPA backend still works

print("Model loaded.")

# ------------------------------------------------------------------------------
# 2b. LOAD LORA STYLES (optional)
#    Every entry in LORAS is loaded once, as a named adapter, so a request can
#    switch between them (or use none) without reloading anything.
# ------------------------------------------------------------------------------
LOADED_LORAS = {}  # id -> the LORAS entry, for every adapter that loaded successfully

for lora in LORAS:
    lora_id = lora.get("id")
    try:
        load_kwargs = {"adapter_name": lora_id}
        if lora.get("weight_name"):
            load_kwargs["weight_name"] = lora["weight_name"]
        pipe.load_lora_weights(lora["repo_id"], **load_kwargs)
        LOADED_LORAS[lora_id] = lora
        print(f"Loaded LoRA style '{lora.get('name', lora_id)}' ({lora_id}).")
    except Exception as e:
        # A bad path/repo in one entry shouldn't take down the whole server —
        # just skip it and keep going.
        print(f"Could not load LoRA style '{lora_id}': {e}")

if LOADED_LORAS:
    # Nothing active by default; a request opts into a style explicitly.
    pipe.disable_lora()

# Recommended negative prompt per the model card — used as the default unless
# the frontend sends its own.
DEFAULT_NEGATIVE_PROMPT = (
    "lowres, bad anatomy, bad hands, text, error, missing finger, extra digits, "
    "fewer digits, cropped, worst quality, low quality, low score, bad score, "
    "average score, signature, watermark, username, blurry"
)

if not API_KEY:
    API_KEY = secrets.token_hex(8)

# ------------------------------------------------------------------------------
# 3. API SERVER
#    One endpoint to generate images, one to check the server is alive.
#    Every request must include the header "X-API-Key" matching API_KEY above —
#    this stops strangers from finding your public ngrok URL and burning your
#    free GPU quota.
# ------------------------------------------------------------------------------
app = FastAPI(title="SDXL Render Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

MAX_IMAGES_PER_REQUEST = 8
MIN_SIDE, MAX_SIDE = 512, 1536


class GenerateRequest(BaseModel):
    prompt: str
    negative_prompt: str = DEFAULT_NEGATIVE_PROMPT
    num_images: int = Field(default=1, ge=1, le=MAX_IMAGES_PER_REQUEST)
    width: int = Field(default=832, ge=MIN_SIDE, le=MAX_SIDE)
    height: int = Field(default=1216, ge=MIN_SIDE, le=MAX_SIDE)
    guidance_scale: float = Field(default=5.0, ge=1.0, le=12.0)
    steps: int = Field(default=28, ge=10, le=50)
    seed: int = -1  # -1 = random. Otherwise used as the base seed for the batch.
    style: str | None = None  # LoRA adapter id from GET /styles, or None/"none" for the base model
    lora_scale: float | None = Field(default=None, ge=0.0, le=2.0)  # None = that style's default_scale


def check_api_key(x_api_key: str | None):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key header.")


@app.get("/")
def root():
    return {"message": f"SDXL render server is running ({MODEL_ID}). POST /generate to create images."}


@app.get("/health")
def health(x_api_key: str | None = Header(default=None)):
    check_api_key(x_api_key)
    return {"status": "ok", "device": str(pipe.device), "model": MODEL_ID}


@app.get("/styles")
def styles(x_api_key: str | None = Header(default=None)):
    """Lists the LoRA styles/finetunes loaded at startup (see LORAS above),
    so the frontend can build a selector. Empty list if none are configured."""
    check_api_key(x_api_key)
    return [
        {
            "id": lora_id,
            "name": lora.get("name", lora_id),
            "trigger_word": lora.get("trigger_word", ""),
            "default_scale": lora.get("default_scale", 0.8),
        }
        for lora_id, lora in LOADED_LORAS.items()
    ]


@app.post("/generate")
def generate(req: GenerateRequest, x_api_key: str | None = Header(default=None)):
    check_api_key(x_api_key)

    if req.width % 8 != 0 or req.height % 8 != 0:
        raise HTTPException(status_code=400, detail="width and height must be multiples of 8.")

    # --- Resolve the requested style (LoRA), if any --------------------------
    prompt = req.prompt
    active_style = req.style if req.style and req.style != "none" else None

    if active_style:
        lora = LOADED_LORAS.get(active_style)
        if lora is None:
            raise HTTPException(status_code=400, detail=f"Unknown style '{active_style}'.")
        scale = req.lora_scale if req.lora_scale is not None else lora.get("default_scale", 0.8)
        pipe.set_adapters([active_style], adapter_weights=[scale])
        trigger = (lora.get("trigger_word") or "").strip()
        if trigger and trigger.lower() not in prompt.lower():
            prompt = f"{trigger}, {prompt}"
    elif LOADED_LORAS:
        # Some styles are loaded but this request wants the plain base model.
        pipe.disable_lora()

    base_seed = req.seed if req.seed != -1 else random.randint(0, 2_147_483_647)

    images_out = []
    start_total = time.time()

    for i in range(req.num_images):
        image_seed = base_seed + i
        generator = torch.Generator(device="cuda").manual_seed(image_seed)

        t0 = time.time()
        try:
            result = pipe(
                prompt,
                negative_prompt=req.negative_prompt,
                width=req.width,
                height=req.height,
                guidance_scale=req.guidance_scale,
                num_inference_steps=req.steps,
                generator=generator,
            )
        except torch.cuda.OutOfMemoryError:
            torch.cuda.empty_cache()
            raise HTTPException(
                status_code=500,
                detail=(
                    f"Ran out of GPU memory after {i} of {req.num_images} image(s). "
                    "Try fewer images per request or a smaller resolution."
                ),
            )
        elapsed = time.time() - t0

        image = result.images[0]
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

        images_out.append({"base64": b64, "seed": image_seed, "elapsed_seconds": round(elapsed, 2)})

    return {
        "images": images_out,
        "meta": {
            "model": MODEL_ID,
            "style": active_style,
            "prompt": prompt,
            "width": req.width,
            "height": req.height,
            "guidance_scale": req.guidance_scale,
            "steps": req.steps,
            "base_seed": base_seed,
            "total_elapsed_seconds": round(time.time() - start_total, 2),
        },
    }


# ------------------------------------------------------------------------------
# 4. START THE TUNNEL + SERVER
# ------------------------------------------------------------------------------
if not NGROK_AUTH_TOKEN:
    raise RuntimeError(
        "NGROK_AUTH_TOKEN is empty. Get a free token at "
        "https://dashboard.ngrok.com/get-started/your-authtoken and paste it in "
        "the settings cell above."
    )

ngrok.kill()  # clean up any leftover tunnel from a previous run of this cell
ngrok.set_auth_token(NGROK_AUTH_TOKEN)
public_url = ngrok.connect(PORT, "http").public_url

print("\n" + "=" * 70)
print(f" SDXL render server is ready ({MODEL_ID})")
print("=" * 70)
print(f" Backend URL : {public_url}")
print(f" API Key     : {API_KEY}")
print("=" * 70)
print(" Paste both values into the frontend app's settings panel.")
print(" Keep this cell running — closing it stops the server.")
print("=" * 70 + "\n")

nest_asyncio.apply()

# NOTE: we deliberately do NOT use uvicorn.run() here. Colab/Jupyter cells
# already have an event loop running, and uvicorn.run() calls asyncio.run()
# internally, which refuses to start a second loop on top of it (even with
# nest_asyncio applied, since uvicorn caches the original asyncio.run at
# import time). Using `await server.serve()` runs uvicorn directly on the
# notebook's existing loop instead, which Colab's cell executor supports.
config = uvicorn.Config(app, host="0.0.0.0", port=PORT, log_level="info")
server = uvicorn.Server(config)
await server.serve()