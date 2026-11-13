"""Official Instagram Graph API content publishing (single image posts)."""
from __future__ import annotations

import datetime as dt
import logging
import time

import requests

log = logging.getLogger("skibot")


class InstagramError(RuntimeError):
    pass


class Instagram:
    def __init__(self, user_id: str, token: str, host: str = "graph.facebook.com", version: str = "v23.0"):
        self.user_id, self.token = user_id, token
        self.base = f"https://{host}/{version}"

    def _call(self, method: str, path: str, retries: int = 3, **params) -> dict:
        params["access_token"] = self.token
        last = ""
        for attempt in range(1, retries + 1):
            try:
                r = requests.request(method, f"{self.base}/{path}",
                                     params=params if method == "GET" else None,
                                     data=params if method != "GET" else None, timeout=60)
            except requests.RequestException as exc:
                last = f"network error: {exc}"
            else:
                if r.status_code < 400:
                    return r.json()
                try:
                    err = r.json().get("error", {})
                except ValueError:
                    err = {}
                last = f"HTTP {r.status_code}: {err.get('message', r.text[:200])} (code {err.get('code')}, subcode {err.get('error_subcode')})"
                transient = r.status_code >= 500 or err.get("is_transient") or err.get("code") in (1, 2, 4, 17, 32, 613)
                if not transient:
                    raise InstagramError(last)  # auth/permission/media errors will not fix themselves
            log.warning("Graph API %s %s attempt %s/%s failed: %s", method, path, attempt, retries, last)
            time.sleep(5 * 2 ** (attempt - 1))
        raise InstagramError(last)

    def check_credentials(self) -> str:
        data = self._call("GET", self.user_id, fields="username")
        return data.get("username", "?")

    def recent_posts(self, limit: int = 10) -> list[dict]:
        return self._call("GET", f"{self.user_id}/media", fields="id,timestamp,permalink", limit=limit).get("data", [])

    def posted_since(self, since_utc: dt.datetime) -> bool:
        for m in self.recent_posts():
            ts = dt.datetime.strptime(m["timestamp"], "%Y-%m-%dT%H:%M:%S%z")
            if ts >= since_utc:
                return True
        return False

    def publish_image(self, image_url: str, caption: str, alt_text: str = "") -> dict:
        params = {"image_url": image_url, "caption": caption}
        if alt_text:
            params["alt_text"] = alt_text[:1000]
        container = self._call("POST", f"{self.user_id}/media", **params)["id"]
        for _ in range(30):  # up to ~2.5 minutes
            status = self._call("GET", container, fields="status_code,status").get("status_code")
            if status == "FINISHED":
                break
            if status in ("ERROR", "EXPIRED"):
                raise InstagramError(f"Media container {status}: {self._call('GET', container, fields='status')}")
            time.sleep(5)
        else:
            raise InstagramError("Media container did not finish processing in time")
        # Publishing is NOT retried blindly: a retry after an ambiguous failure could double-post.
        media_id = self._call("POST", f"{self.user_id}/media_publish", retries=1, creation_id=container)["id"]
        try:
            permalink = self._call("GET", media_id, fields="permalink").get("permalink", "")
        except InstagramError:
            permalink = ""
        return {"media_id": media_id, "permalink": permalink}


def wait_for_url(url: str, timeout: int = 120) -> bool:
    """raw.githubusercontent.com can lag briefly after a push; Instagram must be able to fetch the URL."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            r = requests.get(url, timeout=20, stream=True)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/"):
                return True
        except requests.RequestException:
            pass
        time.sleep(6)
    return False
