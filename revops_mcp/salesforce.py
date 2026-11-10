"""Minimal read-only Salesforce client.

It borrows the login the Salesforce CLI already has (`sf org display`), so no passwords
or tokens are stored in this repo. The token stays in memory only.
"""
import json
import subprocess
import urllib.parse

import httpx

API_VERSION = "v64.0"


class Salesforce:
    def __init__(self, org_alias: str = "revops-demo"):
        self.org_alias = org_alias
        self._token = None
        self.instance_url = None

    def _sf_json(self, *args) -> dict:
        res = subprocess.run(["sf", *args, "--target-org", self.org_alias, "--json"], capture_output=True, text=True)
        try:
            data = json.loads(res.stdout)
        except json.JSONDecodeError:
            raise RuntimeError(f"Could not read Salesforce CLI login for '{self.org_alias}': {res.stderr.strip()}")
        if data.get("status") != 0:
            raise RuntimeError(f"Salesforce CLI login for '{self.org_alias}' failed: {data.get('message')}")
        return data["result"]

    def _login(self) -> None:
        # `sf org display` redacts the token in current CLI versions; show-access-token returns it (and refreshes it).
        self.instance_url = self._sf_json("org", "display")["instanceUrl"].rstrip("/")
        self._token = self._sf_json("org", "auth", "show-access-token")["accessToken"]

    def query(self, soql: str) -> list[dict]:
        """Run a SOQL query and return all records (follows pagination)."""
        if not self._token:
            self._login()
        url = f"{self.instance_url}/services/data/{API_VERSION}/query?q={urllib.parse.quote(soql)}"
        records = []
        retried = False
        while url:
            r = httpx.get(url, headers={"Authorization": f"Bearer {self._token}"}, timeout=60)
            if r.status_code == 401 and not retried:  # token expired: re-borrow from the CLI once
                retried = True
                self._login()
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"Salesforce query failed ({r.status_code}): {r.text[:500]}")
            body = r.json()
            records += body["records"]
            nxt = body.get("nextRecordsUrl")
            url = f"{self.instance_url}{nxt}" if nxt else None
        return records

    def record_url(self, record_id: str) -> str:
        if not self.instance_url:
            self._login()
        return f"{self.instance_url}/{record_id}"  # Salesforce redirects this to the Lightning record page


def soql_str(value: str) -> str:
    """Quote a value for SOQL (prevents injection via names typed by a user)."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def soql_ids(ids) -> str:
    return "(" + ",".join(soql_str(i) for i in ids) + ")"
