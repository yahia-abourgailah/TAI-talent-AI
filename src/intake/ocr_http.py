"""The company OCR API, on our own host (CR-01).

The real contract, read from the service's own description on 17 September 2026:

    POST {TALENT_OCR_BASE_URL}/extract
    Content-Type: multipart/form-data, one part named "file"
    X-API-Key: {TALENT_OCR_API_KEY}

    200  ExtractedCV, translated by intake.cv_extractor
    403  the key is wrong or missing: a person fixes the configuration, not the CV
    408, 429, 5xx  busy or down: tried again later
    other 4xx      the file cannot be read: a person looks at it

Nothing from a response is logged; the answer body is returned untouched so it is saved exactly as
it came (BR-107).
"""

import httpx

from intake.files import EXTENSION
from intake.ocr import OcrRejected, OcrReply, OcrTimeout, OcrUnavailable

PATH = "/extract"
API_KEY_HEADER = "X-API-Key"
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
            self._headers[API_KEY_HEADER] = api_key
        self._client = httpx.Client(
            timeout=timeout_seconds, follow_redirects=False, transport=transport
        )

    def read(self, content: bytes, media_type: str) -> OcrReply:
        # The service chooses its reader from the filename's extension, not from the content type,
        # and refuses a name without one. The name is built from the type we sniffed ourselves —
        # never the candidate's own filename, which is theirs and may say anything at all.
        filename = f"cv.{EXTENSION.get(media_type, 'bin')}"
        try:
            response = self._client.post(
                self._url,
                headers=self._headers,
                files={FILE_PART: (filename, content, media_type)},
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
        if status in {401, 403}:
            # Nothing is wrong with the CV: our key is. Treat it as the service being unavailable,
            # so the CV waits and is read once the key is fixed, rather than being refused.
            raise OcrUnavailable(
                f"The OCR API refused our key ({status}). Check TALENT_OCR_API_KEY."
            )
        raise OcrRejected(f"The OCR API refused the file with {status}.")

    def close(self) -> None:
        self._client.close()
