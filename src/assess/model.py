"""The company's language model, on our own host (CR-01, NFR-01).

    POST {TALENT_VLLM_BASE_URL}/chat/completions      OpenAI-shaped, as vLLM serves it
    Authorization: Bearer {TALENT_VLLM_API_KEY}

It is the same machine the CV reader runs on, inside the company, so a CV read against a job does
not leave (CR-01). It is also the machine the chatbots use, so this asks for one answer at a time
with a timeout and gives up rather than queueing behind itself (NFR-01): a hiring assessment is
never worth slowing down a customer conversation.

Nothing from an answer is logged. What comes back is handed to assess.answer, which refuses
anything that does not quote the CV it was given.
"""

import json
from dataclasses import dataclass

import httpx

PATH = "/chat/completions"
# The model is asked for JSON and nothing else; vLLM enforces the shape it can.
RESPONSE_FORMAT = {"type": "json_object"}
RETRYABLE_STATUS = frozenset({408, 409, 425, 429})


class ModelUnavailable(Exception):
    """Busy, down, or refusing our key. The CV waits and is assessed later."""


class ModelRefused(Exception):
    """The model would not answer this at all. A person looks at the CV instead."""


@dataclass(frozen=True, slots=True)
class Reply:
    text: str
    model: str
    prompt_tokens: int
    answer_tokens: int


class Model:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key: str = "",
        timeout_seconds: float = 60.0,
        max_answer_tokens: int = 900,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._url = base_url.rstrip("/") + PATH
        self._model = model
        self._max_answer_tokens = max_answer_tokens
        self._headers = {"Accept": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.Client(
            timeout=timeout_seconds, follow_redirects=False, transport=transport
        )

    def ask(self, system: str, question: str) -> Reply:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": question},
            ],
            # The same CV and the same job should give the same answer twice: an assessment that
            # wanders is not something anyone can check (BR-307, NFR-08).
            "temperature": 0,
            "top_p": 1,
            "seed": 20260920,
            "max_tokens": self._max_answer_tokens,
            "response_format": RESPONSE_FORMAT,
        }
        try:
            response = self._client.post(self._url, headers=self._headers, json=payload)
        except httpx.TimeoutException:
            raise ModelUnavailable("The model did not answer in time.") from None
        except httpx.HTTPError:
            raise ModelUnavailable("The model could not be reached.") from None

        status = response.status_code
        if status in RETRYABLE_STATUS or status >= 500:
            raise ModelUnavailable(f"The model answered {status}.")
        if status in {401, 403}:
            # Our key, not this CV. The CV waits rather than being judged by nobody.
            raise ModelUnavailable(f"The model refused our key ({status}).")
        if status != 200:
            raise ModelRefused(f"The model refused the request with {status}.")

        try:
            body = response.json()
            choice = body["choices"][0]["message"]["content"]
            usage = body.get("usage") or {}
        except (json.JSONDecodeError, KeyError, IndexError, TypeError):
            raise ModelRefused("The model's answer was not in the shape vLLM promises.") from None
        return Reply(
            text=str(choice),
            model=str(body.get("model") or self._model),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            answer_tokens=int(usage.get("completion_tokens") or 0),
        )

    def close(self) -> None:
        self._client.close()
