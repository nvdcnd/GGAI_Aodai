# Ao Dai Custom Image Generator API - Developer & AI Agent Specification

This specification documents the contract for the Ao Dai Custom Image Generator Backend API. Frontend engineers and AI coding agents building web clients must strictly follow this spec.

## Base URL Configuration
- **Local:** `http://localhost:8000`
- **Render Production:** `https://<YOUR-RENDER-APP-NAME>.onrender.com`

---

## 1. Primary Endpoint: Generate Custom Ao Dai Image

- **HTTP Method:** `POST`
- **Endpoint:** `/api/v1/generate-aodai`
- **Content-Type:** `application/json`

### Request Body Schema (`JSON`)

| Field Name | Type | Required | Enum / Constraints | Description / Examples |
| :--- | :--- | :--- | :--- | :--- |
| `gender` | `string` | **Yes** | `"Nam"`, `"Nữ"` | Target wearer's gender. |
| `body_type` | `string` | **Yes** | Non-empty string | Body shape/build. e.g. `"Cao rỏng, thon gọn"` |
| `occasion` | `string` | **Yes** | Non-empty string | Purpose of wearing. e.g. `"Lễ cưới truyền thống"`, `"Chụp ảnh Tết"` |
| `ao_dai_style` | `string` | **Yes** | Keywords matchable with heritage database | Type of Ao Dai. e.g. `"Áo dài ngũ thân"`, `"Giao lĩnh"`, `"Lê Phổ"` |
| `design_style` | `string` | **Yes** | Non-empty string | Design theme/patterns. e.g. `"Thêu hoa văn chim phụng bằng chỉ tơ vàng"` |
| `shirt_color` | `string` | **Yes** | Non-empty string | Color of the tunic/shirt. e.g. `"Đỏ nhung trầm"` |
| `pants_color` | `string` | **Yes** | Non-empty string | Color of trousers/pants. e.g. `"Vàng hoàng kim"` |
| `accessories` | `array[string]`| No | List of strings | List of accessories. e.g. `["Mấn đội đầu", "Quạt giấy"]` |

#### Example Payload Request:
```json
{
  "gender": "Nữ",
  "body_type": "Cao rỏng, thon gọn, dáng vai suôn",
  "occasion": "Lễ cưới truyền thống",
  "ao_dai_style": "Áo dài ngũ thân lập cổ",
  "design_style": "Thêu chìm hoa văn chim phụng bằng chỉ tơ vàng",
  "shirt_color": "Đỏ nhung trầm",
  "pants_color": "Vàng hoàng kim",
  "accessories": [
    "Mấn đội đầu đồng màu",
    "Quạt xếp bằng giấy xuyến chỉ"
  ]
}
```

---

### Response Specifications

> **CRITICAL RULE FOR CLIENT INTEGRATION:**
> The API strictly returns **HTTP Status 200 OK** for all logic execution results (both success and service exceptions). Check the `success` field in the JSON body to determine success or failure.

#### Case 1: Successful Image Generation (Status: `200 OK`)
```json
{
  "success": true,
  "message": "Sinh ảnh thành công qua Gemini API",
  "image_base64": "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2wBD...",
  "provider_used": "Gemini",
  "prompt_used": "A full-length, front-facing commercial fashion portrait of a Vietnamese woman...",
  "matched_style_knowledge": "Áo Dài Ngũ Thân Lập Cổ (Truyền thống Huế)",
  "error_details": null
}
```

#### Case 2: Soft Failover Response (Status: `200 OK` with `success: false`)
Occurs when both primary (Gemini) and fallback (DeepSeek) providers fail (e.g. quota exhausted, network issues).
```json
{
  "success": false,
  "message": "Không thể sinh ảnh do cả 2 dịch vụ Gemini và DeepSeek đều gặp sự cố hoặc hết hạn ngạch.",
  "image_base64": null,
  "provider_used": null,
  "prompt_used": "A full-length, front-facing commercial fashion portrait...",
  "matched_style_knowledge": "Áo Dài Ngũ Thân Lập Cổ (Truyền thống Huế)",
  "error_details": "Gemini Provider Failed: ... | DeepSeek Provider Failed: ..."
}
```

---

## 2. Web Frontend Rendering Implementation Guide

For Web Client / JavaScript Integration:

```javascript
async function renderAoDai(userInputs) {
  try {
    const response = await fetch("https://your-api.onrender.com/api/v1/generate-aodai", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(userInputs)
    });

    const result = await response.json();

    if (result.success && result.image_base64) {
      // Directly assign base64 URI to HTML img element
      document.getElementById("output-image").src = result.image_base64;
    } else {
      alert("Lỗi sinh ảnh: " + result.message);
      console.error(result.error_details);
    }
  } catch (err) {
    console.error("Network Error:", err);
  }
}
```
