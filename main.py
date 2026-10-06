import asyncio
import base64
import json
import logging
import os
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

# Logger configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("aodai_api")

app = FastAPI(
    title="Ao Dai Custom Image Generator API",
    version="1.0.0",
    description="API sinh ảnh Áo Dài chuẩn Hasselblad Photorealism với tri thức di sản và Fallback Provider"
)

# Enable CORS for Frontend/Client integrations
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API Keys
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")

# Load Knowledge Base
KNOWLEDGE_FILE = Path(__file__).parent / "aodai_knowledge.json"
aodai_knowledge_db: List[Dict] = []

if KNOWLEDGE_FILE.exists():
    try:
        with open(KNOWLEDGE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            aodai_knowledge_db = data.get("styles", [])
        logger.info(f"Loaded {len(aodai_knowledge_db)} Ao Dai styles into knowledge base.")
    except Exception as err:
        logger.error(f"Failed to load aodai_knowledge.json: {err}")


# --- 1. SCHEMAS ---
class GenderEnum(str, Enum):
    MALE = "Nam"
    FEMALE = "Nữ"


class AoDaiGenerateInput(BaseModel):
    gender: GenderEnum = Field(..., description="Giới tính người mặc", example="Nữ")
    #body_type: str = Field(..., description="Thể chất / Dáng người", example="Cao rỏng, thon gọn, vai suôn")
    occasion: str = Field(..., description="Mục đích mặc", example="Lễ cưới truyền thống")
    ao_dai_style: str = Field(..., description="Kiểu áo dài", example="Áo dài ngũ thân truyền thống")
    design_style: str = Field(..., description="Phong cách thiết kế", example="Tối giản, thêu hoa văn chim phụng chỉ tơ vàng")
    shirt_color: str = Field(..., description="Màu áo", example="Đỏ nhung trầm")
    pants_color: str = Field(..., description="Màu quần", example="Vàng hoàng kim")
    accessories: List[str] = Field(default_factory=list, description="Danh sách phụ kiện", example=["Mấn đội đầu đồng màu", "Quạt xuyến chỉ"])


class AoDaiGenerateOutput(BaseModel):
    success: bool = Field(..., description="Trạng thái thực thi thành công hay thất bại")
    message: str = Field(..., description="Thông báo kết quả")
    image_base64: Optional[str] = Field(None, description="Dữ liệu ảnh dạng Base64 data URI")
    provider_used: Optional[str] = Field(None, description="API provider thực tế đã sinh ảnh (Gemini/DeepSeek)")
    prompt_used: Optional[str] = Field(None, description="Prompt tiếng Anh chuẩn hóa được gửi tới AI Model")
    matched_style_knowledge: Optional[str] = Field(None, description="Tên thể loại áo dài được trích xuất từ Knowledge Base")
    error_details: Optional[str] = Field(None, description="Chi tiết lỗi nếu success=False")


# --- 2. KNOWLEDGE BASE MATCHING & PROMPT BUILDER ---
def match_style_enrichment(input_style: str) -> Tuple[str, str]:
    """Matches user input style with knowledge base keywords to get prompt extensions."""
    input_lower = input_style.lower()
    for item in aodai_knowledge_db:
        for kw in item.get("name_keywords", []):
            if kw in input_lower:
                return item["display_name"], item["prompt_enrichment"]
    
    # Generic fallback enrichment
    return "Áo Dài Truyền Thống Việt Nam", "Classic Vietnamese Ao Dai silhouette, high standing collar, tailored fit, flowing elegant panels."


def build_hasselblad_prompt(inp: AoDaiGenerateInput) -> Tuple[str, str]:
    gender_str = "Vietnamese woman" if inp.gender == GenderEnum.FEMALE else "Vietnamese man"
    acc_str = ", ".join(inp.accessories) if inp.accessories else "No extra accessories"
    
    matched_title, style_enrichment = match_style_enrichment(inp.ao_dai_style)

    prompt = (
        f"A full-length, front-facing commercial fashion portrait of a {gender_str} with a normal body build, "
        f"standing gracefully centered in the frame facing the camera directly. The subject is wearing a high-end customized Ao Dai "
        f"tailored specifically for {inp.occasion}.\n\n"
        f"**Style & Heritage Nuance ({matched_title}):**\n"
        f"- Archetype Features: {style_enrichment}\n"
        f"- Custom Specific Style: {inp.ao_dai_style}\n"
        f"- Design Theme & Embroidery: {inp.design_style}\n"
        f"- Tunic/Shirt Color: {inp.shirt_color}\n"
        f"- Trousers/Pants Color: {inp.pants_color}\n"
        f"- Accessories: {acc_str}\n\n"
        f"**Commercial Photography & Technical Specs:**\n"
        f"- Shot on Hasselblad H6D-100c medium format camera with Hasselblad HC 100mm f/2.2 lens.\n"
        f"- Pin-sharp focus on the subject, perfectly straight eye-level perspective, direct front view (chính diện), full body in frame.\n"
        f"- Hyper-realistic fabric micro-textures showing fine silk sheen, authentic stitching detail, natural drape and folds.\n"
        f"- Professional studio softbox lighting with subtle rim lights, clean neutral studio background.\n"
        f"- True-to-life skin tones, 8K resolution, high dynamic range (HDR), rich color grading, photorealistic, zero distortion."
    )
    return prompt.strip(), matched_title


# --- 3. PROVIDER INTEGRATIONS ---
def _call_gemini_sync(prompt: str, api_key: str) -> str:
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not configured.")
    
    client = genai.Client(api_key=api_key)
    response = client.models.generate_images(
        model='imagen-3.0-generate-002',
        prompt=prompt,
        config=types.GenerateImagesConfig(
            number_of_images=1,
            output_mime_type="image/jpeg",
            aspect_ratio="3:4",
            person_generation="ALLOW_ADULT",
        )
    )
    if not response.generated_images:
        raise RuntimeError("Gemini Imagen API returned empty response.")
        
    img_bytes = response.generated_images[0].image.image_bytes
    b64_str = base64.b64encode(img_bytes).decode("utf-8")
    return f"data:image/jpeg;base64,{b64_str}"


async def generate_via_gemini(prompt: str) -> str:
    return await asyncio.to_thread(_call_gemini_sync, prompt, GEMINI_API_KEY)


async def generate_via_deepseek(prompt: str) -> str:
    if not DEEPSEEK_API_KEY:
        raise ValueError("DEEPSEEK_API_KEY is not configured.")

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "prompt": prompt,
        "size": "1024x1365",
        "response_format": "b64_json"
    }

    async with httpx.AsyncClient(timeout=45.0) as client:
        res = await client.post(f"{DEEPSEEK_BASE_URL}/images/generations", json=payload, headers=headers)
        if res.status_code != 200:
            raise RuntimeError(f"DeepSeek API Error HTTP {res.status_code}: {res.text}")

        data = res.json()
        b64_val = data["data"][0]["b64_json"]
        if b64_val.startswith("data:image"):
            return b64_val
        return f"data:image/jpeg;base64,{b64_val}"


# --- 4. ENDPOINT ROUTE ---
@app.post("/api/v1/generate-aodai", response_model=AoDaiGenerateOutput, status_code=200)
async def generate_aodai_endpoint(inp: AoDaiGenerateInput):
    prompt, matched_knowledge = build_hasselblad_prompt(inp)
    logger.info(f"Incoming Request -> Gender: {inp.gender.value}, Style: {inp.ao_dai_style} (Matched: {matched_knowledge})")

    errors = []

    # Primary Attempt: Gemini
    try:
        b64_data = await generate_via_gemini(prompt)
        logger.info("Image generated successfully via Gemini API.")
        return AoDaiGenerateOutput(
            success=True,
            message="Sinh ảnh thành công qua Gemini API",
            image_base64=b64_data,
            provider_used="Gemini",
            prompt_used=prompt,
            matched_style_knowledge=matched_knowledge
        )
    except Exception as e:
        err_msg = f"Gemini Provider Failed: {str(e)}"
        logger.warning(err_msg)
        errors.append(err_msg)

    # Fallback Attempt: DeepSeek
    try:
        logger.info("Initiating Rollback to Secondary Provider (DeepSeek)...")
        b64_data = await generate_via_deepseek(prompt)
        logger.info("Image generated successfully via DeepSeek API.")
        return AoDaiGenerateOutput(
            success=True,
            message="Sinh ảnh thành công qua DeepSeek API (Rollback)",
            image_base64=b64_data,
            provider_used="DeepSeek",
            prompt_used=prompt,
            matched_style_knowledge=matched_knowledge
        )
    except Exception as e:
        err_msg = f"DeepSeek Provider Failed: {str(e)}"
        logger.error(err_msg)
        errors.append(err_msg)

    # Both Failed -> Soft Failover (HTTP 200 with success=False)
    logger.error("All providers failed to render image.")
    return JSONResponse(
        status_code=200,
        content=AoDaiGenerateOutput(
            success=False,
            message="Không thể sinh ảnh do cả 2 dịch vụ Gemini và DeepSeek đều gặp sự cố hoặc hết hạn ngạch.",
            image_base64=None,
            provider_used=None,
            prompt_used=prompt,
            matched_style_knowledge=matched_knowledge,
            error_details=" | ".join(errors)
        ).model_dump()
    )


@app.get("/health")
async def health_check():
    return {"status": "ok", "knowledge_styles_count": len(aodai_knowledge_db)}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
