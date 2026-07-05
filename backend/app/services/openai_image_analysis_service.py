from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import logging
import re

from backend.app.core.config import get_env
from backend.app.services.scraping_service import PageImage
from backend.app.services.scraping_service import PageMetadata

logger = logging.getLogger(__name__)
DEFAULT_OPENAI_VISION_MODEL = "gpt-4o-mini"
DEFAULT_OPENAI_VISION_MAX_IMAGES = 3
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
    entry_start_date: str | None
    entry_deadline_date: str | None
    entry_status: str
    evidence_text: str | None


class OpenAIImageAnalysisService:
    def analyze(self, metadata: PageMetadata) -> ImageAnalysisResult | None:
        if not self._is_enabled():
            return None

        image_inputs = self._select_image_inputs(metadata)
        if not image_inputs:
            return None

        from openai import APIStatusError
        from openai import OpenAI
        from openai import RateLimitError

        client = OpenAI(api_key=get_env("OPENAI_API_KEY"), max_retries=0)
        try:
            response = client.responses.create(
                model=self._get_model(),
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
                                    "通知文は作らず、JSONのみを返してください。"
                                    "日付は必ず YYYY-MM-DD 形式にしてください。"
                                    "画像内に年が読めない日付は推測せず null にしてください。"
                                    "entry_status は open, closed, unknown のいずれかにしてください。"
                                    "JSON形式は {\"entry_start_date\": null, "
                                    "\"entry_deadline_date\": null, "
                                    "\"entry_status\": \"unknown\", "
                                    "\"evidence_text\": \"...\"} です。"
                                    "該当情報がない場合は日付を null、entry_status を unknown にしてください。"
                                ),
                            },
                            *image_inputs,
                        ],
                    }
                ],
            )
        except RateLimitError as exc:
            logger.warning("openai image analysis skipped due to rate limit or quota: %s", self._format_api_error(exc))
            return None
        except APIStatusError as exc:
            logger.warning("openai image analysis skipped due to api error: %s", self._format_api_error(exc))
            return None

        image_analysis = self._parse_image_analysis_result(response.output_text)
        if image_analysis is None:
            return None

        return image_analysis

    def _is_enabled(self) -> bool:
        if not get_env("OPENAI_API_KEY"):
            return False

        enabled = get_env("ENABLE_LLM_IMAGE_ANALYSIS", "true")
        return enabled.lower() in {"1", "true", "yes", "on"}

    def _select_image_inputs(self, metadata: PageMetadata) -> list[dict[str, str]]:
        max_images = self._get_max_images()
        selected_images = self._select_relevant_images(metadata.images)
        image_inputs = [
            {"type": "input_image", "image_url": image.url}
            for image in selected_images[:max_images]
        ]

        if len(image_inputs) < max_images and metadata.screenshot_base64:
            image_inputs.append(
                {
                    "type": "input_image",
                    "image_url": f"data:image/png;base64,{metadata.screenshot_base64}",
                }
            )

        return image_inputs

    def _get_model(self) -> str:
        return get_env("OPENAI_VISION_MODEL", DEFAULT_OPENAI_VISION_MODEL) or DEFAULT_OPENAI_VISION_MODEL

    def _get_max_images(self) -> int:
        raw_max_images = get_env("OPENAI_VISION_MAX_IMAGES", str(DEFAULT_OPENAI_VISION_MAX_IMAGES))
        try:
            max_images = int(raw_max_images or DEFAULT_OPENAI_VISION_MAX_IMAGES)
        except ValueError:
            return DEFAULT_OPENAI_VISION_MAX_IMAGES

        if max_images <= 0:
            return DEFAULT_OPENAI_VISION_MAX_IMAGES

        return max_images

    def _select_relevant_images(self, images: tuple[PageImage, ...]) -> list[PageImage]:
        scored_images: list[tuple[int, PageImage]] = []
        for image in images:
            haystack = " ".join(part for part in (image.url, image.alt, image.context) if part)
            score = sum(1 for keyword in IMAGE_RELEVANCE_KEYWORDS if keyword in haystack)
            if score > 0:
                scored_images.append((score, image))

        return [image for _, image in sorted(scored_images, key=lambda item: item[0], reverse=True)]

    def _parse_image_analysis_result(self, output_text: str) -> ImageAnalysisResult | None:
        stripped_text = self._strip_json_fence(output_text.strip())
        try:
            payload = json.loads(stripped_text)
        except json.JSONDecodeError:
            return None

        entry_start_date = self._normalize_date_value(payload.get("entry_start_date"))
        entry_deadline_date = self._normalize_date_value(payload.get("entry_deadline_date"))
        entry_status = self._normalize_entry_status(payload.get("entry_status"))
        evidence_text = self._normalize_text_value(payload.get("evidence_text"))

        if entry_start_date is None and entry_deadline_date is None and evidence_text is None:
            return None

        return ImageAnalysisResult(
            entry_start_date=entry_start_date,
            entry_deadline_date=entry_deadline_date,
            entry_status=entry_status,
            evidence_text=evidence_text,
        )

    def _normalize_date_value(self, value: object) -> str | None:
        if not isinstance(value, str):
            return None

        normalized_value = value.strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized_value):
            return None

        try:
            datetime.strptime(normalized_value, "%Y-%m-%d")
        except ValueError:
            return None

        return normalized_value

    def _normalize_entry_status(self, value: object) -> str:
        if not isinstance(value, str):
            return "unknown"

        normalized_value = value.strip().lower()
        if normalized_value in {"open", "closed", "unknown"}:
            return normalized_value

        return "unknown"

    def _normalize_text_value(self, value: object) -> str | None:
        if not isinstance(value, str):
            return None

        normalized_value = re.sub(r"\s+", " ", value).strip()
        return normalized_value or None

    def _strip_json_fence(self, text: str) -> str:
        match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL)
        if not match:
            return text

        return match.group(1)

    def _format_api_error(self, exc: Exception) -> str:
        status_code = getattr(exc, "status_code", None)
        code = None
        body = getattr(exc, "body", None)
        if isinstance(body, dict):
            error = body.get("error")
            if isinstance(error, dict):
                code = error.get("code")

        if status_code and code:
            return f"status={status_code} code={code}"

        if status_code:
            return f"status={status_code}"

        return exc.__class__.__name__
