from __future__ import annotations

from dataclasses import dataclass
import json
import re

from backend.app.core.config import get_env
from backend.app.services.scraping_service import PageImage
from backend.app.services.scraping_service import PageMetadata

DEFAULT_OPENAI_VISION_MODEL = "gpt-5.5"
MAX_IMAGE_INPUTS = 3
IMAGE_RELEVANCE_KEYWORDS = (
    "entry",
    "apply",
    "moushikomi",
    "moshikomi",
    "boshu",
    "schedule",
    "エントリー",
    "申込",
    "申し込み",
    "募集",
    "締切",
    "受付",
)


@dataclass(frozen=True)
class ImageAnalysisResult:
    detected_text: str


class OpenAIImageAnalysisService:
    def analyze(self, metadata: PageMetadata) -> ImageAnalysisResult | None:
        if not self._is_enabled():
            return None

        image_inputs = self._select_image_inputs(metadata)
        if not image_inputs:
            return None

        from openai import OpenAI

        client = OpenAI(api_key=get_env("OPENAI_API_KEY"))
        response = client.responses.create(
            model=get_env("OPENAI_VISION_MODEL", DEFAULT_OPENAI_VISION_MODEL),
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "画像から日本語のマラソン大会エントリー情報を抽出してください。"
                                "対象はエントリー開始、申込開始、エントリー締切、申込締切、"
                                "募集締切、受付終了、申込期間、エントリー期間だけです。"
                                "通知文は作らず、根拠として読めた文字列だけを返してください。"
                                "JSON形式で {\"detected_text\": \"...\"} のみを返してください。"
                                "該当情報がない場合は detected_text を空文字にしてください。"
                            ),
                        },
                        *image_inputs,
                    ],
                }
            ],
        )

        detected_text = self._parse_detected_text(response.output_text)
        if not detected_text:
            return None

        return ImageAnalysisResult(detected_text=f"[openai_vision] {detected_text}")

    def _is_enabled(self) -> bool:
        if not get_env("OPENAI_API_KEY"):
            return False

        enabled = get_env("ENABLE_LLM_IMAGE_ANALYSIS", "true")
        return enabled.lower() in {"1", "true", "yes", "on"}

    def _select_image_inputs(self, metadata: PageMetadata) -> list[dict[str, str]]:
        selected_images = self._select_relevant_images(metadata.images)
        image_inputs = [
            {"type": "input_image", "image_url": image.url}
            for image in selected_images[:MAX_IMAGE_INPUTS]
        ]

        if len(image_inputs) < MAX_IMAGE_INPUTS and metadata.screenshot_base64:
            image_inputs.append(
                {
                    "type": "input_image",
                    "image_url": f"data:image/png;base64,{metadata.screenshot_base64}",
                }
            )

        return image_inputs

    def _select_relevant_images(self, images: tuple[PageImage, ...]) -> list[PageImage]:
        scored_images: list[tuple[int, PageImage]] = []
        for image in images:
            haystack = " ".join(part for part in (image.url, image.alt, image.context) if part)
            score = sum(1 for keyword in IMAGE_RELEVANCE_KEYWORDS if keyword in haystack)
            if score > 0:
                scored_images.append((score, image))

        return [image for _, image in sorted(scored_images, key=lambda item: item[0], reverse=True)]

    def _parse_detected_text(self, output_text: str) -> str:
        stripped_text = self._strip_json_fence(output_text.strip())
        try:
            payload = json.loads(stripped_text)
        except json.JSONDecodeError:
            return re.sub(r"\s+", " ", stripped_text).strip()

        detected_text = payload.get("detected_text")
        if not isinstance(detected_text, str):
            return ""

        return re.sub(r"\s+", " ", detected_text).strip()

    def _strip_json_fence(self, text: str) -> str:
        match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL)
        if not match:
            return text

        return match.group(1)
