"""
storage.py -- where the portfolio data lives.

GitHubStore: the app's own repo holds data/portfolio.json. Every save from the
UI is a commit, so the repo history is a full audit trail and any mistake can
be rolled back. Used when [github] secrets are configured (on Streamlit Cloud).

LocalStore: a plain local file, for running the app on your own machine.

Why not just a local file on the host? Streamlit Community Cloud's disk is
wiped whenever the app sleeps or restarts, so edits would silently vanish.
"""

from __future__ import annotations

import base64
import json
import os

import requests


class ConflictError(Exception):
    """Someone else saved in between -- reload and redo the edit."""


class LocalStore:
    def __init__(self, path: str):
        self.path = path

    def load(self):
        with open(self.path, encoding="utf-8") as f:
            return json.load(f), None

    def save(self, data: dict, version, message: str):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, ensure_ascii=False)
        os.replace(tmp, self.path)
        return None

    @property
    def label(self):
        return f"local file {self.path}"


class GitHubStore:
    API = "https://api.github.com"

    def __init__(self, token: str, repo: str, path: str = "data/portfolio.json", branch: str = "main"):
        self.repo, self.path, self.branch = repo, path, branch
        self.h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                  "X-GitHub-Api-Version": "2022-11-28"}

    def _url(self):
        return f"{self.API}/repos/{self.repo}/contents/{self.path}"

    def load(self):
        r = requests.get(self._url(), headers=self.h, params={"ref": self.branch}, timeout=20)
        r.raise_for_status()
        j = r.json()
        if j.get("content"):
            raw = base64.b64decode(j["content"])
        else:  # files > 1 MB come back without inline content
            raw = requests.get(j["download_url"], headers=self.h, timeout=20).content
        return json.loads(raw.decode("utf-8")), j["sha"]

    def save(self, data: dict, version, message: str):
        body = json.dumps(data, indent=1, ensure_ascii=False).encode("utf-8")
        payload = {"message": message, "content": base64.b64encode(body).decode(),
                   "sha": version, "branch": self.branch}
        r = requests.put(self._url(), headers=self.h, json=payload, timeout=30)
        if r.status_code in (409, 422):
            raise ConflictError("The portfolio was changed elsewhere since this page loaded.")
        r.raise_for_status()
        return r.json()["content"]["sha"]

    @property
    def label(self):
        return f"GitHub {self.repo}/{self.path}"
