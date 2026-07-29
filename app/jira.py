from dataclasses import dataclass
import re
from typing import Any

import httpx


class JiraError(RuntimeError):
    pass


ISSUE_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{1,254}-[1-9][0-9]*$")


@dataclass(frozen=True)
class PurchaseRequest:
    issue_key: str
    summary: str
    product: str
    purchase_url: str
    amount: str
    requester: str
    status: str


def _display(value: Any) -> str:
    if value is None:
        return "No informado"
    if isinstance(value, dict):
        for key in ("displayName", "name", "value"):
            if value.get(key):
                return str(value[key])
        return str(value)
    if isinstance(value, list):
        return ", ".join(_display(item) for item in value)
    return str(value)


def parse_jira_webhook(
    payload: dict[str, Any],
    product_field_id: str,
    url_field_id: str,
    amount_field_id: str,
) -> PurchaseRequest:
    issue = payload.get("issue")
    if not isinstance(issue, dict):
        raise ValueError("Missing issue")
    key = issue.get("key")
    fields = issue.get("fields")
    if not isinstance(key, str) or not key or not isinstance(fields, dict):
        raise ValueError("Invalid issue payload")
    key = key.upper()
    if not ISSUE_KEY_PATTERN.fullmatch(key):
        raise ValueError("Invalid issue key")
    status = fields.get("status") or {}
    reporter = fields.get("reporter") or fields.get("creator")
    return PurchaseRequest(
        issue_key=key,
        summary=_display(fields.get("summary")),
        product=_display(fields.get(product_field_id)),
        purchase_url=_display(fields.get(url_field_id)),
        amount=_display(fields.get(amount_field_id)),
        requester=_display(reporter),
        status=_display(status),
    )


class JiraClient:
    def __init__(
        self,
        base_url: str,
        email: str,
        api_token: str,
        timeout: float,
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = base_url
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            base_url=base_url,
            auth=(email, api_token),
            timeout=httpx.Timeout(timeout),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def _json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = await self.client.request(method, path, **kwargs)
            response.raise_for_status()
            if response.status_code == 204:
                return {}
            data = response.json()
            if not isinstance(data, dict):
                raise JiraError("Jira returned an invalid response")
            return data
        except (httpx.HTTPError, ValueError) as exc:
            raise JiraError("Jira request failed") from exc

    async def get_status(self, issue_key: str) -> str:
        data = await self._json(
            "GET", f"/rest/api/3/issue/{issue_key}", params={"fields": "status"}
        )
        try:
            return str(data["fields"]["status"]["name"])
        except (KeyError, TypeError) as exc:
            raise JiraError("Jira status response is invalid") from exc

    async def _available_transitions(
        self, issue_key: str
    ) -> list[dict[str, Any]]:
        data = await self._json(
            "GET", f"/rest/api/3/issue/{issue_key}/transitions", params={"expand": "transitions.fields"}
        )
        transitions = data.get("transitions")
        if not isinstance(transitions, list):
            raise JiraError("Jira transitions response is invalid")
        return [item for item in transitions if isinstance(item, dict)]

    @staticmethod
    def _transition_id(
        transitions: list[dict[str, Any]], target_status: str
    ) -> str | None:
        return next(
            (
                str(item["id"])
                for item in transitions
                if isinstance(item.get("to"), dict)
                and str(item["to"].get("name", "")).casefold()
                == target_status.casefold()
            ),
            None,
        )

    async def _execute_transition(self, issue_key: str, transition_id: str) -> None:
        await self._json(
            "POST",
            f"/rest/api/3/issue/{issue_key}/transitions",
            json={"transition": {"id": transition_id}},
        )

    async def transition_to(
        self,
        issue_key: str,
        target_status: str,
        intermediate_status: str | None = None,
    ) -> None:
        transitions = await self._available_transitions(issue_key)
        transition_id = self._transition_id(transitions, target_status)
        if transition_id:
            await self._execute_transition(issue_key, transition_id)
            return
        if not intermediate_status:
            raise JiraError(f"No transition is available to '{target_status}'")
        intermediate_id = self._transition_id(transitions, intermediate_status)
        if not intermediate_id:
            raise JiraError(
                f"No transition is available to intermediate status "
                f"'{intermediate_status}'"
            )
        await self._execute_transition(issue_key, intermediate_id)
        transitions = await self._available_transitions(issue_key)
        transition_id = self._transition_id(transitions, target_status)
        if not transition_id:
            raise JiraError(
                f"Intermediate transition completed, but no transition is "
                f"available to '{target_status}'"
            )
        await self._execute_transition(issue_key, transition_id)

    async def add_comment(self, issue_key: str, text: str) -> None:
        await self._json(
            "POST",
            f"/rest/api/3/issue/{issue_key}/comment",
            json={
                "body": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": text}],
                        }
                    ],
                }
            },
        )
