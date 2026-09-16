"""The company OCR API, on our own host (CR-01).

GUESS, until the OCR team sends its documentation: one CV per request,

    POST {TALENT_OCR_BASE_URL}/ocr
    Content-Type: multipart/form-data, one part named "file"
    Authorization: Bearer {TALENT_OCR_API_KEY}   (only when a key is set)

    200  the answer, JSON (docs/intake/OCR_ANSWER.md)
    408, 429, 5xx  busy or down: tried again later
    other 4xx      the file cannot be read: a person looks at it

Only PATH and FILE_PART below should need to change when the real contract arrives. Nothing from a
response is logged; the answer body is returned untouched so it can be saved as it came.
"""

import httpx

from intake.ocr import OcrRejected, OcrReply, OcrTimeout, OcrUnavailable

PATH = "/ocr"
FILE_PART = "file"
RETRYABLE_STATUS = frozenset({408, 425, 429})


class HttpOcrReader:
    name = "ocr-api"

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str = "",
        timeout_seconds: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._url = base_url.rstrip("/") + PATH
        self._headers = {"Accept": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.Client(
            timeout=timeout_seconds, follow_redirects=False, transport=transport
        )

    def read(self, content: bytes, media_type: str) -> OcrReply:
        try:
            response = self._client.post(
                self._url,
                headers=self._headers,
                files={FILE_PART: ("cv", content, media_type)},
            )
        except httpx.TimeoutException:
            raise OcrTimeout("The OCR API did not answer in time.") from None
        except httpx.HTTPError:
            raise OcrUnavailable("The OCR API could not be reached.") from None

        status = response.status_code
        if status == 200:
            return OcrReply(
                body=response.content,
                media_type=response.headers.get("content-type", "application/json"),
                reader=self.name,
            )
        if status in RETRYABLE_STATUS or status >= 500:
            raise OcrUnavailable(f"The OCR API answered {status}.")
        raise OcrRejected(f"The OCR API refused the file with {status}.")

    def close(self) -> None:
        self._client.close()
