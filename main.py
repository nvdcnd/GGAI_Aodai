import asyncio
import base64
import json
import logging
import os
import random
import re
import unicodedata
import urllib.parse
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# Load biến môi trường từ file .env ngay khi app khởi chạy
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("aodai_api")

app = FastAPI(title="Ao Dai Custom Image Generator API", version="1.7.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load Knowledge Base
KNOWLEDGE_FILE = Path(__file__).parent / "aodai_knowledge.json"
aodai_knowledge_db: List[Dict] = []

if KNOWLEDGE_FILE.exists():
    try:
        with open(KNOWLEDGE_FILE, "r", encoding="utf-8") as f:
            aodai_knowledge_db = json.load(f).get("styles", [])
        logger.info(f"Loaded {len(aodai_knowledge_db)} Ao Dai styles into knowledge base.")
    except Exception as err:
        logger.error(f"Failed to load aodai_knowledge.json: {err}")


class GenderEnum(str, Enum):
    MALE = "Nam"
    FEMALE = "Nữ"


class AoDaiGenerateInput(BaseModel):
    gender: GenderEnum
    body_type: str
    occasion: str
    ao_dai_style: str
    design_style: str
    shirt_color: str
    pants_color: str
    accessories: List[str] = Field(default_factory=list)


class AoDaiGenerateOutput(BaseModel):
    success: bool
    message: str
    image_base64: Optional[str] = None
    provider_used: Optional[str] = None
    prompt_used: Optional[str] = None
    matched_style_knowledge: Optional[str] = None
    error_details: Optional[str] = None


# --- UTILS: TỰ ĐỘNG CHUYỂN TIẾNG VIỆT SANG TIẾNG ANH CHUẨN ASCII ---
COLOR_MAP = {
    "xanh lam đậm": "dark blue",
    "xanh lam": "blue",
    "đỏ nhung": "deep velvet red",
    "đỏ": "red",
    "vàng hoàng kim": "golden yellow",
    "vàng": "yellow",
    "trắng lụa": "white silk",
    "trắng": "white",
    "đen": "black",
    "xanh lá": "green",
    "hồng": "pink",
    "tím": "purple"
}


def translate_or_strip_vietnamese(text: str) -> str:
    """Chuyển đổi các từ tiếng Việt phổ biến hoặc khử dấu unicode thành ASCII hoàn toàn."""
    text_lower = text.strip().lower()
    if text_lower in COLOR_MAP:
        return COLOR_MAP[text_lower]
    
    nfkd_form = unicodedata.normalize('NFKD', text)
    only_ascii = "".join([c for c in nfkd_form if not unicodedata.combining(c)])
    clean_str = re.sub(r'[^a-zA-Z0-9\s,-]', '', only_ascii)
    return re.sub(r'\s+', ' ', clean_str).strip()


def match_style_enrichment(input_style: str, gender: GenderEnum) -> Tuple[str, str]:
    input_lower = input_style.lower()
    for item in aodai_knowledge_db:
        if gender == GenderEnum.MALE and "nam" in item["id"]:
            for kw in item.get("name_keywords", []):
                if kw in input_lower:
                    return item["display_name"], item["prompt_enrichment"]
        elif gender == GenderEnum.FEMALE and "nam" not in item["id"]:
            for kw in item.get("name_keywords", []):
                if kw in input_lower:
                    return item["display_name"], item["prompt_enrichment"]

    for item in aodai_knowledge_db:
        for kw in item.get("name_keywords", []):
            if kw in input_lower:
                return item["display_name"], item["prompt_enrichment"]

    if gender == GenderEnum.MALE:
        return "Áo Dài Nam Truyền Thống", "Authentic traditional Vietnamese male Ao Dai, long silk tunic extending past knees, smooth front chest, loose pants."
    return "Áo Dài Nữ Truyền Thống", "Authentic traditional Vietnamese female Ao Dai, fitted tunic, long flowing panels over silk trousers."


def build_clean_ascii_prompt(inp: AoDaiGenerateInput) -> Tuple[str, str]:
    matched_title, style_enrichment = match_style_enrichment(inp.ao_dai_style, inp.gender)
    
    occasion_en = translate_or_strip_vietnamese(inp.occasion)
    shirt_color_en = translate_or_strip_vietnamese(inp.shirt_color)
    pants_color_en = translate_or_strip_vietnamese(inp.pants_color)
    design_style_en = translate_or_strip_vietnamese(inp.design_style)
    body_type_en = translate_or_strip_vietnamese(inp.body_type)
    acc_clean = [translate_or_strip_vietnamese(a) for a in inp.accessories]
    acc_str = ", ".join(acc_clean) if acc_clean else "none"

    if inp.gender == GenderEnum.MALE:
        prompt = (
            f"Full-length studio photo of a Vietnamese man with {body_type_en} body, wearing authentic traditional Vietnamese male Ao Dai for {occasion_en}. "
            f"Long {shirt_color_en} silk robe tunic extending past knees over loose {pants_color_en} silk trousers. "
            f"{style_enrichment}. Design: {design_style_en}. Accessories: {acc_str}. "
            f"No belt, no Chinese Tangzhuang, no frog buttons. Hyperrealistic 8k fashion photography."
        )
    else:
        prompt = (
            f"Full-length studio photo of a Vietnamese woman with {body_type_en} body, wearing authentic traditional Vietnamese female Ao Dai for {occasion_en}. "
            f"Elegant {shirt_color_en} silk tunic with long flowing panels over wide-leg {pants_color_en} silk trousers. "
            f"{style_enrichment}. Design: {design_style_en}. Accessories: {acc_str}. "
            f"Hyperrealistic 8k fashion photography."
        )

    clean_prompt = prompt.replace("\n", " ").replace("/", " ")
    clean_prompt = re.sub(r'\s+', ' ', clean_prompt).strip()
    return clean_prompt, matched_title


# --- PROVIDER 1 (PRIMARY): Hugging Face Inference API ---
HF_MODELS = [
    "black-forest-labs/FLUX.1-schnell",
    "black-forest-labs/FLUX.1-dev",
    "stabilityai/stable-diffusion-xl-base-1.0"
]

async def generate_via_huggingface(prompt: str) -> Tuple[str, str]:
    hf_token = os.getenv("HF_TOKEN", "").strip()
    if not hf_token:
        raise ValueError("HF_TOKEN chưa được cấu hình.")

    headers = {"Authorization": f"Bearer {hf_token}"}
    last_err = ""

    for model_name in HF_MODELS:
        api_url = f"https://api-inference.huggingface.co/models/{model_name}"
        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                res = await client.post(api_url, headers=headers, json={"inputs": prompt})
                if res.status_code == 200 and res.content and len(res.content) > 2000:
                    b64_str = base64.b64encode(res.content).decode("utf-8")
                    return f"data:image/jpeg;base64,{b64_str}", model_name
                else:
                    last_err = f"Model {model_name} HTTP {res.status_code}: {res.text[:100]}"
        except Exception as e:
            last_err = f"Model {model_name} Error: {str(e)}"
            continue

    raise RuntimeError(f"Hugging Face thất bại: {last_err}")


# --- PROVIDER 2 (FALLBACK): Pollinations.ai ---
async def generate_via_pollinations(prompt: str) -> str:
    encoded_prompt = urllib.parse.quote(prompt, safe='')
    seed = random.randint(1000, 999999)
    
    url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?model=flux&width=1024&height=1365&seed={seed}&nologo=true"

    async with httpx.AsyncClient(timeout=45.0) as client:
        res = await client.get(url)
        if res.status_code != 200:
            raise RuntimeError(f"Pollinations Error HTTP {res.status_code}: {res.text[:100]}")

        if not res.content or len(res.content) < 2000:
            raise RuntimeError("Pollinations trả về dữ liệu ảnh không hợp lệ.")

        b64_str = base64.b64encode(res.content).decode("utf-8")
        return f"data:image/jpeg;base64,{b64_str}"


@app.post("/api/v1/generate-aodai", response_model=AoDaiGenerateOutput, status_code=200)
async def generate_aodai_endpoint(inp: AoDaiGenerateInput):
    prompt, matched_knowledge = build_clean_ascii_prompt(inp)
    logger.info(f"Incoming Request -> Gender: {inp.gender.value}, Style: {inp.ao_dai_style}")
    logger.info(f"Generated Clean ASCII Prompt:\n{prompt}")
    errors = []

    # 1. PRIMARY: Hugging Face
    try:
        logger.info("Đang sinh ảnh qua Primary Provider: Hugging Face...")
        b64_data, used_model = await generate_via_huggingface(prompt)
        logger.info(f"Sinh ảnh thành công qua Hugging Face ({used_model}).")
        return AoDaiGenerateOutput(
            success=True,
            message=f"Sinh ảnh thành công qua Hugging Face ({used_model})",
            image_base64=b64_data,
            provider_used=f"Hugging Face ({used_model})",
            prompt_used=prompt,
            matched_style_knowledge=matched_knowledge
        )
    except Exception as e:
        err_msg = f"HuggingFace Error: {str(e)}"
        logger.warning(err_msg)
        errors.append(err_msg)

    # 2. FALLBACK: Pollinations.ai
    try:
        logger.info("Rollback: Đang chuyển sang Secondary Provider (Pollinations.ai)...")
        b64_data = await generate_via_pollinations(prompt)
        logger.info("Sinh ảnh thành công qua Pollinations.ai.")
        return AoDaiGenerateOutput(
            success=True,
            message="Sinh ảnh thành công qua Pollinations.ai (Rollback)",
            image_base64=b64_data,
            provider_used="Pollinations (Flux)",
            prompt_used=prompt,
            matched_style_knowledge=matched_knowledge
        )
    except Exception as e:
        err_msg = f"Pollinations Error: {str(e)}"
        logger.error(err_msg)
        errors.append(err_msg)

    # 3. Soft Failover Response
    return JSONResponse(
        status_code=200,
        content=AoDaiGenerateOutput(
            success=False,
            message="Không thể sinh ảnh do tất cả các provider đều gặp sự cố.",
            prompt_used=prompt,
            matched_style_knowledge=matched_knowledge,
            error_details=" | ".join(errors)
        ).model_dump()
    )


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)