# -*- coding: utf-8 -*-
"""
Product Atelier Desktop - Python Backend Server
================================================
FastAPI backend wrapping the existing AI image processing logic.
Runs as a sidecar process alongside the Tauri desktop app.
All business logic preserved 100% from ecom_workbench.py.
"""
import base64, json, time, io, os, sys, mimetypes, threading, traceback
from pathlib import Path
from datetime import datetime
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
import requests
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# ======================== GUI MODE STDOUT GUARD ========================
# When running as windowed (no console) exe, sys.stdout/sys.stderr may be None.
# Redirect to devnull to prevent print() / uvicorn logging crashes.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")
# Also guard uvicorn's access logger which writes to stderr
import logging
logging.getLogger("uvicorn.access").handlers = [logging.NullHandler()]
logging.getLogger("uvicorn.error").handlers = [logging.NullHandler()]

# ======================== CONFIG ========================
def get_app_data_dir():
    """Get platform-appropriate app data directory for config/storage"""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    d = base / "ProductAtelier"
    d.mkdir(parents=True, exist_ok=True)
    return d

APP_DIR = get_app_data_dir()
CONFIG_PATH = APP_DIR / "config.json"
OUTPUT_DIR = APP_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
(OUTPUT_DIR / "multi-products").mkdir(exist_ok=True)
(OUTPUT_DIR / "_tmp").mkdir(exist_ok=True)
HISTORY_DIR = APP_DIR / "history"
HISTORY_DIR.mkdir(parents=True, exist_ok=True)

# Legacy config path for migration
LEGACY_CONFIG = Path(r"C:\Users\64414\.codex\skills\lk-ai-image\config.json")

BASE_URL = "https://api.lk888.ai/api"

MODEL_OPTIONS = {
    "GPT-Image-2 (最高质量)": "gpt-image-2",
    "Nano Banana Pro (专业商业)": "gemini-3-pro-image-preview",
    "Nano Banana 2 (快速批量)": "gemini-3.1-flash-image-preview",
    "千问-Image (中文优化)": "qwen-image",
}

NEG_BASE = "模糊,低质量,变形,暗角,暖黄偏色,杂物,水印,文字,logo,阴影过重,噪点,失真,jpeg压缩痕迹,过度曝光,欠曝"
NEG_REMOVE_PLATE = "盘子,碟子,托盘,木板,纸板,餐垫,桌布,玻璃器皿,碗,容器,器皿,摆盘,竹垫,石板,餐布,金属台面,木桌面,大理石桌面,纸杯,塑料盒"

ANGLE_PROMPT = {
    "auto": "AI智能选择最佳拍摄角度",
    "keep": "严格保持参考图中产品的原始拍摄角度和透视关系，不要改变拍摄角度",
    "45top": "略微俯视45度角(three-quarter top-down view)，经典电商主图角度",
    "front": "正面平视角度(eye-level straight front view)，包装类产品首选，标签文字清晰正面可见",
    "30side": "30度斜侧角度(dramatic 3/4 view)，突出产品立体感和层次",
    "90top": "90度正俯视角度(flat lay top-down view)，适合平铺展示",
}

# ======================== CONFIG PERSISTENCE ========================
def load_api_key():
    # Check app config first
    if CONFIG_PATH.exists():
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if cfg.get("api_key"):
            return cfg["api_key"]
    # Fallback to legacy config
    if LEGACY_CONFIG.exists():
        cfg = json.loads(LEGACY_CONFIG.read_text(encoding="utf-8"))
        if cfg.get("api_key"):
            return cfg["api_key"]
    return None

def save_config(cfg: dict):
    existing = {}
    if CONFIG_PATH.exists():
        existing = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    existing.update(cfg)
    CONFIG_PATH.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")

API_KEY = load_api_key()

def get_api_key():
    global API_KEY
    if not API_KEY:
        raise RuntimeError("API Key not configured. Please set it in Settings.")
    return API_KEY

def set_api_key(key: str):
    global API_KEY
    API_KEY = key
    save_config({"api_key": key})

def get_settings():
    cfg = {}
    if CONFIG_PATH.exists():
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return {
        "api_key": "***" if cfg.get("api_key") else "",
        "api_key_set": bool(cfg.get("api_key")),
        "default_model": cfg.get("default_model", "gpt-image-2"),
        "default_platter": cfg.get("default_platter", "auto"),
        "default_angle": cfg.get("default_angle", "auto"),
        "default_fidelity": cfg.get("default_fidelity", 40),
        "auto_refine": cfg.get("auto_refine", True),
        "output_dir": str(OUTPUT_DIR),
    }

# ======================== PROGRESS TRACKING ========================
class ProgressTracker:
    def __init__(self):
        self._tasks = {}
        self._lock = threading.Lock()

    def create_task(self, task_id: str):
        with self._lock:
            self._tasks[task_id] = {"progress": 0, "status": "starting", "message": "", "logs": [], "results": None, "error": None}

    def update(self, task_id: str, progress: float = None, status: str = None, message: str = None, log: str = None):
        with self._lock:
            t = self._tasks.get(task_id, {})
            if progress is not None: t["progress"] = progress
            if status is not None: t["status"] = status
            if message is not None: t["message"] = message
            if log is not None:
                t.setdefault("logs", []).append(f"[{datetime.now().strftime('%H:%M:%S')}] {log}")

    def complete(self, task_id: str, results=None, error=None):
        with self._lock:
            t = self._tasks.get(task_id, {})
            if error:
                t["status"] = "error"
                t["error"] = error
            else:
                t["status"] = "completed"
                t["progress"] = 1.0
                t["results"] = results

    def get(self, task_id: str):
        with self._lock:
            return dict(self._tasks.get(task_id, {}))

    def cleanup(self, task_id: str):
        with self._lock:
            self._tasks.pop(task_id, None)

tracker = ProgressTracker()

# ======================== UTILS ========================
def _get_http_session():
    s = requests.Session()
    s.trust_env = True
    s.headers.update({"User-Agent": "ProductAtelier/1.0"})
    try:
        import certifi
        s.verify = certifi.where()
    except Exception:
        s.verify = True
    return s

_http_session = None

def api_request(method, path, body=None, timeout=120):
    global _http_session
    url = BASE_URL + path
    key = get_api_key()
    headers = {"Authorization": f"Bearer {key}"}
    if _http_session is None:
        _http_session = _get_http_session()
    try:
        resp = _http_session.request(method, url, json=body, headers=headers, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except (requests.exceptions.ProxyError, requests.exceptions.SSLError, requests.exceptions.ConnectionError) as e:
        log_msg("system", f"代理连接失败({type(e).__name__}),尝试直连...")
        direct = requests.Session()
        direct.trust_env = False
        direct.headers.update({"User-Agent": "ProductAtelier/1.0"})
        try:
            import certifi
            direct.verify = certifi.where()
        except Exception:
            direct.verify = True
        resp = direct.request(method, url, json=body, headers=headers, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

def image_to_data_url(img):
    if isinstance(img, (str, Path)):
        p = Path(img)
        b = p.read_bytes()
        mime = mimetypes.guess_type(p.name)[0] or "image/jpeg"
    elif isinstance(img, Image.Image):
        buf = io.BytesIO()
        if img.mode == "RGBA":
            img = img.convert("RGB")
        img.save(buf, format="JPEG", quality=96)
        b = buf.getvalue()
        mime = "image/jpeg"
    elif isinstance(img, bytes):
        b = img
        mime = "image/jpeg"
    else:
        raise TypeError(f"Unsupported image type: {type(img)}")
    b64 = base64.b64encode(b).decode("ascii")
    return f"data:{mime};base64,{b64}"

def image_to_bytes(img, fmt="JPEG", quality=96):
    buf = io.BytesIO()
    if fmt == "PNG":
        img.save(buf, format="PNG")
    else:
        if img.mode == "RGBA":
            img = img.convert("RGB")
        img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()

def download_result(url, dest_path):
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    global _http_session
    try:
        if _http_session is None:
            _http_session = _get_http_session()
        resp = _http_session.get(url, timeout=120)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
    except Exception:
        direct = requests.Session()
        direct.trust_env = False
        try:
            import certifi
            direct.verify = certifi.where()
        except Exception:
            direct.verify = True
        resp = direct.get(url, timeout=120)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
    return str(dest)

def log_msg(task_id, msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(OUTPUT_DIR / "workbench.log", "a", encoding="utf-8") as fp:
            fp.write(line + "\n")
    except:
        pass
    tracker.update(task_id, log=msg)

def save_temp(img, prefix="tmp"):
    p = OUTPUT_DIR / "_tmp" / f"{prefix}_{int(time.time()*1000)}.jpg"
    if img.mode == "RGBA":
        img = img.convert("RGB")
    img.save(p, "JPEG", quality=95)
    return str(p)

# ======================== IMAGE PROCESSING ========================
_BGSESSION = None

def _get_bgsession():
    global _BGSESSION
    if _BGSESSION is None:
        from rembg import new_session
        _BGSESSION = new_session("birefnet-general")
    return _BGSESSION

def post_process_enhance(img):
    if img.mode != "RGB": img = img.convert("RGB")
    img = ImageOps.autocontrast(img, cutoff=1)
    img = ImageEnhance.Contrast(img).enhance(1.08)
    img = ImageEnhance.Color(img).enhance(1.05)
    img = ImageEnhance.Sharpness(img).enhance(1.15)
    img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=130, threshold=3))
    return img

def remove_bg_hd(img):
    from rembg import remove
    session = _get_bgsession()
    if isinstance(img, (str, Path)): img = Image.open(img)
    if img.mode != "RGBA": img = img.convert("RGBA")
    # alpha_matting disabled to avoid pymatting/numba dependency (~120MB)
    # BiRefNet produces high-quality alpha masks natively for product photography
    return remove(img, session=session, alpha_matting=False,
                  post_process_mask=True)

def tight_crop_alpha(img, pad_pct=0.06):
    if img.mode != "RGBA": img = img.convert("RGBA")
    bbox = img.getbbox()
    if bbox is None: return img
    w, h = img.size
    pad_x, pad_y = int(w*pad_pct), int(h*pad_pct)
    return img.crop((max(0,bbox[0]-pad_x), max(0,bbox[1]-pad_y), min(w,bbox[2]+pad_x), min(h,bbox[3]+pad_y)))

def crop_product(img, bbox, w, h, pad_pct=0.12):
    x1,y1,x2,y2 = bbox
    bw,bh = x2-x1, y2-y1
    px,py = int(bw*pad_pct), int(bh*pad_pct)
    return img.crop((max(0,x1-px), max(0,y1-py), min(w,x2+px), min(h,y2+py)))

# ======================== AI PIPELINE ========================
def build_negative(platter_mode):
    neg = NEG_BASE
    if platter_mode == "remove": neg += "," + NEG_REMOVE_PLATE
    return neg

def build_single_prompt(product_name, platter_mode="auto", product_type="food", angle="auto"):
    if platter_mode == "remove":
        plate = "产品直接置于纯白背景上悬浮展示，不使用任何盘子、碟子、托盘、木板、器皿、餐垫、容器，画面极简干净"
    elif platter_mode == "keep":
        plate = "保留产品原有的精致器皿或摆盘（如瓷盘、竹垫、玻璃碗、陶瓷板等），器皿质感高端，呈现高级商业摆盘效果，器皿与产品自然搭配"
    else:
        plate = "智能处理：无包装的裸食品保留精致器皿呈现高级商业感；包装类产品（袋装/盒装/瓶装/罐装）直接纯白底展示无需额外器皿"
    angle_desc = ANGLE_PROMPT.get(angle, ANGLE_PROMPT["auto"])
    if angle == "auto":
        angle_desc = "略微俯视30-45度角(three-quarter view)，产品居中端正构图" if product_type != "packaging" else "正面平视角度，产品端正居中构图，包装标签清晰可见"
    elif angle == "keep":
        angle_desc = "严格保持参考图原始角度透视，产品居中端正构图"
    return (f"专业商业影棚拍摄的电商主图，{product_name}，{angle_desc}，产品占据画面70%面积，"
            f"纯白背景(#FFFFFF)，柔和无影灯光，顶部柔光箱加双侧45度补光，无投影，"
            f"{plate}，产品细节清晰锐利，材质质感真实，色彩准确饱和，高光自然，阴影柔和，"
            f"高端电商产品摄影，8K超清，影棚级画质，专业修图，干净极简构图，广告级质感")

def build_stage2_prompt(product_name, platter_mode="auto", product_type="food", angle="auto"):
    plate = "智能保留或去除器皿，整体协调"
    if platter_mode == "remove": plate = "无任何器皿托盘，产品直接纯白底"
    elif platter_mode == "keep": plate = "精致器皿摆盘自然，高级商业感"
    return (f"精修优化这张电商主图：{product_name}，纯白影棚背景，光线完美柔和均匀，{plate}，"
            f"按目标角度修正产品方向使其端正居中，保持角度准确，修正构图使产品占画面70%，"
            f"增强材质细节纹理（食物表皮光泽、包装材质质感、器皿反光），锐化产品边缘，"
            f"提升色彩饱和度和对比度至商业级标准，修复任何变形或不自然的部分，补全缺失的产品细节和边缘，"
            f"确保产品完整不被裁切，边缘清晰干净，白底纯白无杂色无灰斑，最终呈现超高清商业影棚级电商主图效果")

def build_multi_stage1_prompt(product_name, product_type="food", completeness="complete", platter_mode="auto", angle="auto"):
    angle_desc = ANGLE_PROMPT.get(angle, ANGLE_PROMPT["auto"])
    if angle == "auto":
        angle_desc = "略微俯视30-45度角，产品居中端正构图" if product_type != "packaging" else "正面平视视角，产品居中端正构图"
    elif angle == "keep":
        angle_desc = "严格保持参考图原始角度透视，产品居中端正构图"
    complete_hint = "AI补全被裁切的产品部分，修复不完整的产品边缘，还原完整产品形态（保持原产品外观颜色质感一致），" if completeness == "cutoff" else ""
    if platter_mode == "remove": plate = "产品直接纯白背景，无器皿无托盘"
    elif platter_mode == "keep": plate = "搭配精致器皿呈现摆盘高级感"
    else: plate = "根据产品类型智能选择是否保留器皿"
    return (f"专业商业影棚电商主图，单个{product_name}，{angle_desc}，纯白背景(#FFFFFF)，柔和无影灯光，"
            f"顶部柔光加双侧补光，{plate}，{complete_hint}产品居中，占据画面70%面积，细节清晰锐利，"
            f"材质质感真实，色彩准确饱和，高端产品摄影，8K画质")

def submit_generate(prompt, model_key, ref_data_url=None, size="2048x2048", negative_prompt=None):
    params = {"prompt": prompt, "imageSize": size}
    if negative_prompt: params["negative_prompt"] = negative_prompt
    if ref_data_url: params["image"] = ref_data_url
    resp = api_request("POST", "/v1/media/generate", body={"model": model_key, "params": params}, timeout=300)
    if resp.get("code") != 200: raise RuntimeError(f"API error: {resp.get('msg', resp)}")
    return str(resp["data"]["task_id"])

def poll_task(task_id, task_id_ref="?", timeout_sec=480, interval=6):
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        time.sleep(interval)
        try:
            st = api_request("GET", f"/v1/skills/task-status?task_id={task_id}", timeout=60)
        except Exception as e:
            log_msg(task_id_ref, f"轮询异常: {e}, 重试...")
            continue
        state = st.get("state","")
        prog = st.get("progress",0)
        log_msg(task_id_ref, f"状态: {state} 进度: {prog}%")
        if st.get("is_final"):
            if state == "success": return st["result_url"]
            else: raise RuntimeError(f"生成失败: {json.dumps(st, ensure_ascii=False)[:300]}")
    raise TimeoutError(f"任务超时 ({timeout_sec}s)")

def ai_i2i(prompt, ref_img, model_key, negative_prompt=None, size="2048x2048", stage="?", tid_ref="?"):
    ref_url = image_to_data_url(ref_img)
    log_msg(tid_ref, f"[S{stage}] 提交生成 ({model_key})...")
    tid = submit_generate(prompt, model_key, ref_url, size=size, negative_prompt=negative_prompt)
    log_msg(tid_ref, f"[S{stage}] 任务ID: {tid}")
    result_url = poll_task(tid, task_id_ref=tid_ref)
    tmp = OUTPUT_DIR / "_tmp" / f"stage{stage}_{int(time.time()*1000)}.jpg"
    download_result(result_url, tmp)
    img = Image.open(tmp).copy()
    try: tmp.unlink()
    except: pass
    return img

def vlm_detect_products(image_path, tid_ref="?"):
    img_url = image_to_data_url(image_path)
    prompt = ("请分析这张图片，识别图中所有产品（食品/商品），以严格JSON格式返回结果。\n"
        "要求：\n1. 检测每个独立产品的位置bbox[x1,y1,x2,y2]，坐标为0-1000整数(相对宽高)\n"
        "2. name: 中文具体产品名\n3. ptype: food/packaging/dish\n"
        "4. has_container: true/false，是否有器皿\n5. cutoff: true/false，是否被边缘裁切\n"
        "6. angle_hint: 最佳拍摄角度建议\n\n只返回纯JSON：\n"
        '{"products":[{"bbox":[x1,y1,x2,y2],"name":"产品名","ptype":"food|packaging|dish","has_container":true|false,"cutoff":true|false,"angle_hint":"角度建议"}],"count":N,"scene":"single|multi"}')
    body = {"model": "gemini-3.5-flash", "messages": [{"role": "user", "content": [
        {"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": img_url}}]}],
        "max_tokens": 2048, "temperature": 0.1}
    try:
        resp = api_request("POST", "/v1/chat/completions", body=body, timeout=60)
        text = resp["choices"][0]["message"]["content"].strip()
        if text.startswith("```json"): text = text[7:]
        if text.startswith("```"): text = text[3:]
        if text.endswith("```"): text = text[:-3]
        return json.loads(text.strip())
    except Exception as e:
        log_msg(tid_ref, f"VLM检测异常: {e}")
        return {"products": [{"bbox":[0,0,1000,1000],"name":"产品","ptype":"food","has_container":False,"cutoff":False,"angle_hint":"45度俯视"}],"count":1,"scene":"single"}

def fidelity_suffix(fidelity):
    if fidelity <= 25: return "极严格保留参考图中产品的原始形态、颜色、纹理、大小比例和所有细节，只允许更换纯白背景和影棚灯光，禁止改变产品本身的任何外观特征，产品必须与参考图完全一致。"
    elif fidelity <= 50: return "严格保留参考图中产品的整体形态、颜色和主要特征，可适度优化光影效果和背景，但产品的款式、颜色、纹理、装饰、文字标识都必须与参考图保持一致。"
    elif fidelity <= 75: return "保留参考图中产品的基本形态和类型，在保持产品辨识度的前提下可适度增强质感、优化构图、提升细节表现，使整体更具商业摄影品质。"
    else: return "在参考图产品基础上进行专业商业摄影级优化，可调整角度、增强细节质感、优化构图和光影，呈现最佳商业影棚效果，产品保持可识别但允许较大美化。"

# ======================== FASTAPI APP ========================
app = FastAPI(title="Product Atelier API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.get("/api/health")
async def health():
    return {"status": "ok", "api_key_configured": bool(API_KEY), "output_dir": str(OUTPUT_DIR)}

@app.get("/api/settings")
async def get_app_settings():
    return get_settings()

@app.post("/api/settings")
async def update_settings(data: dict):
    if "api_key" in data and data["api_key"]:
        set_api_key(data["api_key"])
    save = {k: v for k, v in data.items() if k != "api_key"}
    if save: save_config(save)
    return get_settings()

@app.get("/api/balance")
async def balance():
    try:
        resp = api_request("GET", "/v1/skills/balance", timeout=15)
        d = resp.get("data", resp)
        return {"balance": d.get("balance", resp.get("balance", "?")), "error": None}
    except Exception as e:
        return {"balance": None, "error": str(e)}

@app.get("/api/progress/{task_id}")
async def get_progress(task_id: str):
    return tracker.get(task_id)

def make_prompt(base, fid):
    return base + "，" + fidelity_suffix(fid)

def run_single_task(task_id, img_bytes, name, model_key, batch, platter, fidelity, angle):
    try:
        tracker.create_task(task_id)
        tracker.update(task_id, progress=0.02, status="processing", message="读取图片...")
        ref_img = Image.open(io.BytesIO(img_bytes))
        product_type = "food"
        if not name or not name.strip():
            log_msg(task_id, "VLM识别产品中...")
            tmp = save_temp(ref_img, "vlm")
            det = vlm_detect_products(tmp, task_id)
            if det.get("products"):
                p = det["products"][0]
                name = p.get("name","产品")
                product_type = p.get("ptype","food")
                if product_type == "packaging": platter = "remove"
                log_msg(task_id, f"VLM: {name} (类型={product_type})")

        log_msg(task_id, f"单产品开始 | 产品: {name} | 模型: {model_key} | 摆盘: {platter} | 角度: {angle} | 还原度: {fidelity}% | 数量: {batch}")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        neg = build_negative(platter)
        main_results, cut_results = [], []
        per = 0.9 / batch

        for i in range(batch):
            tracker.update(task_id, progress=0.05 + i*per, message=f"AI生成第{i+1}/{batch}张...")
            log_msg(task_id, f"--- 批次 {i+1}/{batch} ---")
            p1 = make_prompt(build_single_prompt(name, platter, product_type, angle), fidelity)
            img1 = ai_i2i(p1, ref_img, model_key, negative_prompt=neg, stage=f"1-{i+1}", tid_ref=task_id)
            tracker.update(task_id, progress=0.35 + i*per + per*0.3, message=f"精修 {i+1}/{batch}...")
            p2 = make_prompt(build_stage2_prompt(name, platter, product_type, angle), fidelity)
            img2 = ai_i2i(p2, img1, model_key, negative_prompt=neg, stage=f"2-{i+1}", tid_ref=task_id)
            main_img = post_process_enhance(img2)
            mp = OUTPUT_DIR / f"product_{ts}_{i+1}_main.jpg"
            main_img.save(mp, "JPEG", quality=96)
            main_results.append({"name": mp.name, "data": base64.b64encode(image_to_bytes(main_img)).decode(), "path": str(mp)})
            log_msg(task_id, f"主图: {mp.name}")
            tracker.update(task_id, progress=0.7 + i*per + per*0.6, message=f"抠图 {i+1}/{batch}...")
            cut = remove_bg_hd(main_img)
            cut = tight_crop_alpha(cut)
            cp = OUTPUT_DIR / f"product_{ts}_{i+1}_cutout.png"
            cut.save(cp, "PNG")
            cut_results.append({"name": cp.name, "data": base64.b64encode(image_to_bytes(cut, "PNG")).decode(), "path": str(cp)})
            log_msg(task_id, f"PNG: {cp.name}")

        tracker.complete(task_id, results={"main": main_results, "cutout": cut_results, "product_name": name})
        log_msg(task_id, "=== 完成! ===")
    except Exception as e:
        traceback.print_exc()
        tracker.complete(task_id, error=str(e))

def run_multi_task(task_id, img_bytes, model_key, platter_default, do_refine, fidelity, angle):
    try:
        tracker.create_task(task_id)
        tracker.update(task_id, progress=0.02, message="读取图片...")
        img = Image.open(io.BytesIO(img_bytes))
        w, h = img.size
        tracker.update(task_id, progress=0.04, message="VLM识别产品...")
        tmp = save_temp(img, "vlm_m")
        det = vlm_detect_products(tmp, task_id)
        products = det.get("products", [])
        count = len(products)
        log_msg(task_id, f"检测到 {count} 个产品")
        if count == 0:
            tracker.complete(task_id, error="未检测到产品"); return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        batch_dir = OUTPUT_DIR / "multi-products" / ts
        batch_dir.mkdir(parents=True, exist_ok=True)
        main_results, cut_results = [], []
        per = 0.92 / count
        for idx, p in enumerate(products):
            pname = p.get("name", f"产品{idx+1}")
            ptype = p.get("ptype", "food")
            has_cont = p.get("has_container", False)
            cutoff = p.get("cutoff", False)
            bbn = p.get("bbox", [0,0,1000,1000])
            bbox = (int(bbn[0]/1000*w), int(bbn[1]/1000*h), int(bbn[2]/1000*w), int(bbn[3]/1000*h))
            pmode = "remove" if ptype=="packaging" else ("keep" if (platter_default=="keep" or (platter_default=="auto" and has_cont)) else "remove")
            pad = 0.20 if cutoff else 0.12
            cropped = crop_product(img, bbox, w, h, pad_pct=pad)
            safe = pname.replace("/","_").replace("\\","_").replace(":","_")[:20]
            neg = build_negative(pmode)
            log_msg(task_id, f"--- [{idx+1}/{count}] {pname} | {ptype} | 器皿={has_cont} | 裁切={cutoff} | 摆盘={pmode} ---")
            tracker.update(task_id, progress=0.06 + idx*per, message=f"AI处理 {pname} ({idx+1}/{count})...")
            p1 = make_prompt(build_multi_stage1_prompt(pname, ptype, "cutoff" if cutoff else "complete", pmode, angle), fidelity)
            mimg = ai_i2i(p1, cropped, model_key, negative_prompt=neg, size="2048x2048", stage=f"1-{idx+1}", tid_ref=task_id)
            if do_refine:
                p2 = make_prompt(build_stage2_prompt(pname, pmode, ptype, angle), fidelity)
                mimg = ai_i2i(p2, mimg, model_key, negative_prompt=neg, size="2048x2048", stage=f"2-{idx+1}", tid_ref=task_id)
            mimg = post_process_enhance(mimg)
            mp = batch_dir / f"{idx+1:02d}_{safe}_main.jpg"
            mimg.save(mp, "JPEG", quality=96)
            main_results.append({"name": mp.name, "data": base64.b64encode(image_to_bytes(mimg)).decode(), "path": str(mp)})
            cut = remove_bg_hd(mimg)
            cut = tight_crop_alpha(cut)
            cp = batch_dir / f"{idx+1:02d}_{safe}_cutout.png"
            cut.save(cp, "PNG")
            cut_results.append({"name": cp.name, "data": base64.b64encode(image_to_bytes(cut, "PNG")).decode(), "path": str(cp)})
        tracker.complete(task_id, results={"main": main_results, "cutout": cut_results, "count": count})
        log_msg(task_id, f"=== 完成! 共{count}个产品 ===")
    except Exception as e:
        traceback.print_exc()
        tracker.complete(task_id, error=str(e))

@app.post("/api/single")
async def process_single(
    file: UploadFile = File(...),
    product_name: str = Form(""),
    model: str = Form("gpt-image-2"),
    batch: int = Form(1),
    platter: str = Form("auto"),
    fidelity: int = Form(40),
    angle: str = Form("auto"),
):
    img_bytes = await file.read()
    task_id = f"single_{int(time.time()*1000)}"
    threading.Thread(target=run_single_task, args=(task_id, img_bytes, product_name, model, batch, platter, fidelity, angle), daemon=True).start()
    return {"task_id": task_id}

@app.post("/api/multi")
async def process_multi(
    file: UploadFile = File(...),
    model: str = Form("gemini-3.1-flash-image-preview"),
    platter: str = Form("auto"),
    refine: bool = Form(True),
    fidelity: int = Form(35),
    angle: str = Form("auto"),
):
    img_bytes = await file.read()
    task_id = f"multi_{int(time.time()*1000)}"
    threading.Thread(target=run_multi_task, args=(task_id, img_bytes, model, platter, refine, fidelity, angle), daemon=True).start()
    return {"task_id": task_id}

@app.post("/api/cutout")
async def cutout_only(file: UploadFile = File(...)):
    """Quick cutout only - no AI generation"""
    img_bytes = await file.read()
    img = Image.open(io.BytesIO(img_bytes))
    cut = remove_bg_hd(img)
    cut = tight_crop_alpha(cut)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    cp = OUTPUT_DIR / f"cutout_{ts}.png"
    cut.save(cp, "PNG")
    return {"data": base64.b64encode(image_to_bytes(cut, "PNG")).decode(), "name": cp.name, "path": str(cp)}


@app.get("/api/thumbnail")
async def get_thumbnail(path: str):
    """Serve a local image file for display in the UI."""
    try:
        p = Path(path)
        # Security: only serve from OUTPUT_DIR
        if not str(p).startswith(str(OUTPUT_DIR)):
            return JSONResponse({"error": "access denied"}, status_code=403)
        if not p.exists() or not p.is_file():
            return JSONResponse({"error": "file not found"}, status_code=404)
        ext = p.suffix.lower()
        media_type = "image/jpeg"
        if ext == ".png": media_type = "image/png"
        elif ext == ".webp": media_type = "image/webp"
        return FileResponse(str(p), media_type=media_type)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
@app.get("/api/history")
async def get_history():
    items = []
    for f in sorted(OUTPUT_DIR.glob("product_*_main.jpg"), reverse=True)[:50]:
        items.append({"name": f.name, "path": str(f), "time": f.stat().st_mtime})
    for d in sorted((OUTPUT_DIR / "multi-products").glob("*"), reverse=True)[:20]:
        if d.is_dir():
            mains = list(d.glob("*_main.jpg"))
            if mains:
                items.append({"name": d.name + " (批量)", "path": str(d), "time": d.stat().st_mtime, "batch": True})
    items.sort(key=lambda x: x["time"], reverse=True)
    return items[:50]

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"Product Atelier backend starting on port {port}...", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
